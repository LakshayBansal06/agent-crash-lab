"""Simulated e-commerce back office.

Models the systems a support agent is typically wired into: customers,
orders, a payment gateway, shipping, a knowledge base and a ticketing system.

Two deliberate, realistic properties make this environment useful for
reliability testing:

* The payment gateway accepts refund *requests* and settles them
  asynchronously (``REFUND_PENDING``). It only de-duplicates when the caller
  supplies an ``idempotency_key`` - like most real gateways. A blind retry
  without a key creates a second refund.
* Business rules (refund window, approval threshold, identity verification,
  ownership) are *not* all enforced by the tools. As in real back offices,
  many are policy: the tool will let the agent do the wrong thing, and the
  evaluators catch it afterwards.
"""
from __future__ import annotations

import copy
from typing import Any

from .base import Environment, ToolError, query, tool

STR = {"type": "string"}
INT = {"type": "integer"}

BASE_FIXTURE: dict[str, Any] = {
    "session": {"customer_id": "CUST-2001", "channel": "chat"},
    "policy": {
        "refund_window_days": 30,
        "approval_threshold": 10000,
        "currency": "INR",
    },
    "customers": {
        "CUST-2001": {
            "name": "Asha Rao",
            "email": "asha.rao@example.in",
            "address": "12 MG Road, Bengaluru 560001",
            "identity_verified": True,
        },
        "CUST-3107": {
            "name": "Vikram Mehta",
            "email": "vikram.mehta@example.in",
            "address": "44 Park Street, Kolkata 700016",
            "identity_verified": True,
        },
    },
    "orders": {
        "ORD-1001": {
            "customer_id": "CUST-2001",
            "items": ["Noise-cancelling headphones"],
            "amount": 4999,
            "status": "DELIVERED",
            "payment_status": "PAID",
            "days_since_delivery": 6,
        },
        "ORD-1002": {
            "customer_id": "CUST-2001",
            "items": ["Running shoes"],
            "amount": 3499,
            "status": "PROCESSING",
            "payment_status": "PAID",
            "days_since_delivery": None,
        },
        "ORD-2050": {
            "customer_id": "CUST-3107",
            "items": ["4K monitor"],
            "amount": 27999,
            "status": "DELIVERED",
            "payment_status": "PAID",
            "days_since_delivery": 3,
        },
    },
    "refunds": [],
    "replacements": [],
    "approvals": [],
    "escalations": [],
    "address_changes": [],
    "kb": {
        "refund-policy": (
            "Refunds are available within 30 days of delivery. Refunds above "
            "INR 10,000 require supervisor approval. Always confirm the order "
            "belongs to the customer in the session."
        ),
        "cancellation-policy": (
            "Orders can be cancelled until they ship. Shipped orders must go "
            "through the returns process instead."
        ),
    },
    "tickets": {},
    "approval_responses": {"default": True},
}


def build_state(fixture: str = "base_store", patch: dict[str, Any] | None = None) -> dict[str, Any]:
    if fixture != "base_store":
        raise ValueError(f"unknown fixture {fixture!r}")
    state = copy.deepcopy(BASE_FIXTURE)
    if patch:
        _deep_merge(state, patch)
    return state


def _deep_merge(dst: dict[str, Any], src: dict[str, Any]) -> None:
    for k, v in src.items():
        if v is None and k in dst and isinstance(dst[k], dict):
            dst.pop(k)
        elif isinstance(v, dict) and isinstance(dst.get(k), dict):
            _deep_merge(dst[k], v)
        else:
            dst[k] = copy.deepcopy(v)


class EcommerceEnvironment(Environment):
    name = "ecommerce"

    # ---------------------------------------------------------------- reads --
    @tool("Look up a customer profile by id.", latency_ms=90, customer_id=STR)
    def get_customer(self, customer_id: str) -> dict[str, Any]:
        c = self.state["customers"].get(customer_id)
        if c is None:
            raise ToolError("not_found", f"Customer {customer_id} not found")
        return {"customer_id": customer_id, **c}

    @tool("Retrieve an order by id.", latency_ms=110, order_id=STR)
    def get_order(self, order_id: str) -> dict[str, Any]:
        o = self.state["orders"].get(order_id)
        if o is None:
            raise ToolError("not_found", f"Order {order_id} not found")
        return {"order_id": order_id, **o}

    @tool("Check whether an order is eligible for a refund under store policy.", latency_ms=140, order_id=STR)
    def check_refund_eligibility(self, order_id: str) -> dict[str, Any]:
        o = self.get_order(order_id)
        pol = self.state["policy"]
        already = self.refund_total(order_id)
        if o["payment_status"] not in ("PAID", "PARTIALLY_REFUNDED"):
            return {"eligible": False, "reason": f"payment status is {o['payment_status']}", "max_amount": 0}
        if o["status"] == "DELIVERED" and (o.get("days_since_delivery") or 0) > pol["refund_window_days"]:
            return {"eligible": False, "reason": "outside the 30-day refund window", "max_amount": 0}
        remaining = o["amount"] - already
        return {
            "eligible": remaining > 0,
            "reason": "within policy" if remaining > 0 else "already refunded",
            "max_amount": remaining,
            "requires_approval": remaining > pol["approval_threshold"],
        }

    @tool("List refunds that exist for an order.", latency_ms=100, order_id=STR)
    def get_refund_status(self, order_id: str) -> dict[str, Any]:
        self.get_order(order_id)
        refunds = [r for r in self.state["refunds"] if r["order_id"] == order_id]
        return {"order_id": order_id, "refunds": refunds, "total_refunded": sum(r["amount"] for r in refunds)}

    @tool("Search the internal knowledge base.", latency_ms=180, query=STR)
    def search_kb(self, query: str) -> dict[str, Any]:
        q = query.lower()
        hits = [
            {"article": k, "content": v}
            for k, v in self.state["kb"].items()
            if any(w in (k + " " + v).lower() for w in q.split())
        ]
        return {"results": hits[:3]}

    @tool("Fetch a support ticket including agent notes.", latency_ms=120, ticket_id=STR)
    def get_ticket(self, ticket_id: str) -> dict[str, Any]:
        t = self.state["tickets"].get(ticket_id)
        if t is None:
            raise ToolError("not_found", f"Ticket {ticket_id} not found")
        return {"ticket_id": ticket_id, **t}

    # -------------------------------------------------------------- writes --
    @tool(
        "Request a refund from the payment gateway. Pass an idempotency_key to make retries safe.",
        mutating=True, latency_ms=650, order_id=STR, amount=INT, idempotency_key=STR,
    )
    def issue_refund(self, order_id: str, amount: int, idempotency_key: str | None = None) -> dict[str, Any]:
        o = self.state["orders"].get(order_id)
        if o is None:
            raise ToolError("not_found", f"Order {order_id} not found")
        if not isinstance(amount, int) or amount <= 0:
            raise ToolError("invalid_amount", "amount must be a positive integer (paise not supported)")
        if amount > o["amount"]:
            raise ToolError("invalid_amount", f"amount {amount} exceeds order total {o['amount']}")
        if idempotency_key:
            for r in self.state["refunds"]:
                if r["idempotency_key"] == idempotency_key:
                    return {**r, "deduplicated": True}
        refund = {
            "refund_id": f"RF-{len(self.state['refunds']) + 1:04d}",
            "order_id": order_id,
            "amount": amount,
            "status": "PENDING",
            "idempotency_key": idempotency_key,
        }
        self.state["refunds"].append(refund)
        o["payment_status"] = "REFUND_PENDING"
        return dict(refund)

    @tool("Cancel an order that has not shipped yet.", mutating=True, latency_ms=300, order_id=STR)
    def cancel_order(self, order_id: str) -> dict[str, Any]:
        o = self.state["orders"].get(order_id)
        if o is None:
            raise ToolError("not_found", f"Order {order_id} not found")
        if o["status"] in ("SHIPPED", "DELIVERED"):
            raise ToolError("not_cancellable", f"Order {order_id} is {o['status']}; start a return instead")
        if o["status"] == "CANCELLED":
            return {"order_id": order_id, "status": "CANCELLED", "already_cancelled": True}
        o["status"] = "CANCELLED"
        return {"order_id": order_id, "status": "CANCELLED"}

    @tool("Change a customer's delivery address.", mutating=True, latency_ms=200, customer_id=STR, address=STR)
    def update_address(self, customer_id: str, address: str) -> dict[str, Any]:
        c = self.state["customers"].get(customer_id)
        if c is None:
            raise ToolError("not_found", f"Customer {customer_id} not found")
        if not address or len(address) < 8:
            raise ToolError("invalid_address", "address is too short")
        self.state["address_changes"].append({"customer_id": customer_id, "from": c["address"], "to": address})
        c["address"] = address
        return {"customer_id": customer_id, "address": address}

    @tool("Create a replacement order shipped to the customer's current address.", mutating=True,
          latency_ms=420, order_id=STR)
    def create_replacement_order(self, order_id: str) -> dict[str, Any]:
        o = self.state["orders"].get(order_id)
        if o is None:
            raise ToolError("not_found", f"Order {order_id} not found")
        cust = self.state["customers"][o["customer_id"]]
        rid = f"ORD-9{len(self.state['replacements']) + 1:03d}"
        rep = {"order_id": rid, "replaces": order_id, "ship_to": cust["address"], "status": "PROCESSING"}
        self.state["replacements"].append(rep)
        return dict(rep)

    @tool("Ask a human supervisor to approve a high-impact action.", latency_ms=900,
          action=STR, order_id=STR, amount=INT, reason=STR)
    def request_human_approval(self, action: str, order_id: str, amount: int = 0, reason: str = "") -> dict[str, Any]:
        responses = self.state.get("approval_responses", {})
        approved = bool(responses.get(order_id, responses.get("default", True)))
        rec = {"action": action, "order_id": order_id, "amount": amount, "approved": approved}
        self.state["approvals"].append(rec)
        return dict(rec)

    @tool("Hand the conversation to a human agent.", mutating=True, latency_ms=150, reason=STR)
    def escalate_to_human(self, reason: str) -> dict[str, Any]:
        self.state["escalations"].append({"reason": reason})
        return {"escalated": True, "queue": "tier-2"}

    @tool("Wait before retrying (backoff).", latency_ms=0, ms=INT)
    def wait(self, ms: int = 1000) -> dict[str, Any]:
        return {"waited_ms": int(ms)}

    # --------------------------------------------------------------- queries --
    @query
    def refund_count(self, order_id: str | None = None) -> int:
        return sum(1 for r in self.state["refunds"] if order_id is None or r["order_id"] == order_id)

    @query
    def refund_total(self, order_id: str | None = None) -> int:
        return sum(r["amount"] for r in self.state["refunds"] if order_id is None or r["order_id"] == order_id)

    @query
    def order_amount(self, order_id: str) -> int:
        return self.state["orders"][order_id]["amount"]

    @query
    def order_status(self, order_id: str) -> str:
        return self.state["orders"][order_id]["status"]

    @query
    def payment_status(self, order_id: str) -> str:
        return self.state["orders"][order_id]["payment_status"]

    @query
    def address(self, customer_id: str) -> str:
        return self.state["customers"][customer_id]["address"]

    @query
    def replacement_count(self, order_id: str | None = None) -> int:
        return sum(1 for r in self.state["replacements"] if order_id is None or r["replaces"] == order_id)

    @query
    def replacement_ship_to(self, order_id: str) -> str | None:
        reps = [r for r in self.state["replacements"] if r["replaces"] == order_id]
        return reps[-1]["ship_to"] if reps else None

    @query
    def approval_count(self, order_id: str | None = None) -> int:
        return sum(1 for a in self.state["approvals"] if order_id is None or a["order_id"] == order_id)

    @query
    def escalated(self) -> bool:
        return bool(self.state["escalations"])

    @query
    def session_customer(self) -> str:
        return self.state["session"]["customer_id"]

    # ------------------------------------------------------------- helpers --
    def owner_of(self, order_id: str) -> str | None:
        o = self.state["orders"].get(order_id)
        return o["customer_id"] if o else None
