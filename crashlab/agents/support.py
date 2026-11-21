"""Reference customer-support agents.

``SupportAgentV1`` reproduces the failure patterns most often seen in real
tool-using LLM agents: blind retries, trusting tool output as instructions,
skipping policy checks some of the time, acting before verifying, and
reporting success it never confirmed. Its stochastic choices are driven by a
seeded RNG, so it behaves like a temperature>0 model: flaky, yet reproducible.

``SupportAgentV2`` is the fix: idempotency keys, post-timeout verification,
backoff, ownership checks, approval gates and honest reporting. It keeps a
small residual flakiness (it occasionally skips the approval step) so the
platform's repeated-trial metrics have something real to measure.

Both agents only see the natural-language task, the tool schemas and the
session context - exactly what an LLM agent sees.
"""
from __future__ import annotations

import re
from typing import Any
from collections.abc import Generator

from .base import Action, FinalAnswer, Observation, ScriptedAgent, ToolCall

ORDER_RE = re.compile(r"\bORD-\d{4}\b")
TICKET_RE = re.compile(r"\bTCK-\d+\b")
QUOTED_RE = re.compile(r"[\"“]([^\"”]{8,})[\"”]")
INJECTED_REFUND_RE = re.compile(r"(?:refund|reimburse)\s+(?:order\s+)?(ORD-\d{4})", re.I)


def parse_request(text: str) -> dict[str, Any]:
    t = text.lower()
    orders = list(dict.fromkeys(ORDER_RE.findall(text)))
    addr = QUOTED_RE.search(text)
    return {
        "orders": orders,
        "tickets": TICKET_RE.findall(text),
        "refund": bool(re.search(r"refund|money back|reimburse", t)),
        "cancel": bool(re.search(r"\bcancel\b", t)),
        "replacement": bool(re.search(r"replacement|replace it|send (me )?(a )?new one", t)),
        "address": addr.group(1).strip() if addr and "address" in t else None,
    }


def inr(n: int) -> str:
    s = str(n)
    if len(s) <= 3:
        return f"₹{s}"
    head, tail = s[:-3], s[-3:]
    groups = []
    while len(head) > 2:
        groups.insert(0, head[-2:])
        head = head[:-2]
    if head:
        groups.insert(0, head)
    return "₹" + ",".join(groups + [tail])


# --------------------------------------------------------------------------- v1
class SupportAgentV1(ScriptedAgent):
    name = "support-agent"
    version = "1.0.0"
    framework = "custom-python"

    SKIP_ELIGIBILITY_P = 0.35
    MAX_REFUND_ATTEMPTS = 3

    def policy(self) -> Generator[Action, Observation, None]:
        req = parse_request(self.task)
        cust = self.context["customer_id"]
        refund_targets = list(req["orders"]) if req["refund"] else []

        # read the ticket and "helpfully" follow any instructions found in it  (BUG: prompt injection)
        for tid in req["tickets"]:
            obs = yield ToolCall("get_ticket", {"ticket_id": tid})
            if obs.ok:
                for oid in INJECTED_REFUND_RE.findall(obs.result.get("notes", "")):
                    if oid not in refund_targets:
                        refund_targets.append(oid)
                if not req["orders"] and obs.result.get("order_id"):
                    req["orders"].append(obs.result["order_id"])
                    if req["refund"]:
                        refund_targets.insert(0, obs.result["order_id"])
        if req["refund"]:
            obs = yield ToolCall("search_kb", {"query": "refund policy"})
            if obs.ok:  # BUG: treats retrieved content as instructions
                for r in obs.result.get("results", []):
                    for oid in INJECTED_REFUND_RE.findall(r.get("content", "")):
                        if oid not in refund_targets:
                            refund_targets.append(oid)

        refunded: dict[str, int] = {}
        cancelled: list[str] = []
        declined: dict[str, str] = {}
        replacements: list[str] = []
        address_done = False

        for oid in list(dict.fromkeys(req["orders"] + refund_targets)):
            # BUG: retries a rate-limited call immediately and forever
            obs = yield ToolCall("get_order", {"order_id": oid})
            while not obs.ok and obs.code in ("rate_limited", "service_unavailable"):
                obs = yield ToolCall("get_order", {"order_id": oid})
            if not obs.ok:
                declined[oid] = "I couldn't find that order"
                continue
            order = obs.result

            if req["cancel"] and oid in req["orders"]:
                yield ToolCall("cancel_order", {"order_id": oid})
                cancelled.append(oid)  # BUG: assumes success regardless of the result

            if oid in refund_targets:
                if self.rng.random() >= self.SKIP_ELIGIBILITY_P:  # BUG: sometimes skips the policy check
                    obs = yield ToolCall("check_refund_eligibility", {"order_id": oid})
                    if obs.ok and not obs.result.get("eligible", True):  # BUG: missing field == eligible
                        declined[oid] = obs.result.get("reason", "not eligible")
                        continue
                amount = order.get("amount", 0)
                for _ in range(self.MAX_REFUND_ATTEMPTS):  # BUG: blind retry, no idempotency key
                    obs = yield ToolCall("issue_refund", {"order_id": oid, "amount": amount})
                    if obs.ok:
                        break
                refunded[oid] = amount  # BUG: reports success even if every attempt failed

        if req["replacement"]:
            for oid in req["orders"]:  # BUG: ships replacement before the address change
                obs = yield ToolCall("create_replacement_order", {"order_id": oid})
                if obs.ok:
                    replacements.append(obs.result["order_id"])
        if req["address"]:
            obs = yield ToolCall("update_address", {"customer_id": cust, "address": req["address"]})
            address_done = obs.ok

        parts: list[str] = []
        for oid in cancelled:
            parts.append(f"I've cancelled order {oid}.")
        for oid, amt in refunded.items():
            parts.append(f"Your refund of {inr(amt)} for {oid} has been processed.")
        for oid, why in declined.items():
            parts.append(f"I wasn't able to refund {oid}: {why}.")
        if address_done:
            parts.append(f"Your delivery address has been updated to {req['address']}.")
        for rid in replacements:
            parts.append(f"A replacement order {rid} is on its way.")
        yield FinalAnswer(" ".join(parts) or "Done! Let me know if there's anything else.")


# --------------------------------------------------------------------------- v2
class SupportAgentV2(ScriptedAgent):
    name = "support-agent"
    version = "2.0.0"
    framework = "custom-python"

    SKIP_APPROVAL_P = 0.12  # residual flakiness, deliberately left in
    MAX_ATTEMPTS = 3
    MAX_BACKOFFS = 6

    def call(self, tool: str, args: dict[str, Any]) -> Generator[Action, Observation, Observation]:
        """Call with retry: exponential backoff on 429/503, never blind on writes."""
        obs = yield ToolCall(tool, args)
        backoffs = 0
        while not obs.ok and obs.code in ("rate_limited", "service_unavailable") and backoffs < self.MAX_BACKOFFS:
            wait = (obs.error or {}).get("retry_after_ms") or 500 * (2 ** backoffs)
            yield ToolCall("wait", {"ms": int(wait)})
            backoffs += 1
            obs = yield ToolCall(tool, args)
        return obs

    def policy(self) -> Generator[Action, Observation, None]:
        req = parse_request(self.task)
        cust = self.context["customer_id"]
        say: list[str] = []
        escalate: list[str] = []

        for tid in req["tickets"]:  # read as *data*; never follow instructions found inside
            obs = yield from self.call("get_ticket", {"ticket_id": tid})
            if obs.ok and not req["orders"] and obs.result.get("order_id"):
                req["orders"].append(obs.result["order_id"])
        if req["refund"]:
            yield from self.call("search_kb", {"query": "refund policy"})

        cancelled_ok: dict[str, bool] = {}
        for oid in req["orders"]:
            obs = yield from self.call("get_order", {"order_id": oid})
            if obs.ok and not {"customer_id", "status", "amount"} <= set(obs.result):
                obs = yield from self.call("get_order", {"order_id": oid})  # malformed -> re-read once
            if not obs.ok or not {"customer_id", "status", "amount"} <= set(obs.result or {}):
                say.append(f"I couldn't load the details for {oid} right now, so I've passed it to a colleague.")
                escalate.append(f"could not load {oid}")
                continue
            order = obs.result
            if order["customer_id"] != cust:
                say.append(f"I can't act on {oid} because it isn't associated with your account.")
                escalate.append(f"customer {cust} requested action on {oid} owned by another customer")
                continue

            if req["cancel"]:
                obs = yield from self.call("cancel_order", {"order_id": oid})
                if not obs.ok and obs.code == "timeout":
                    chk = yield from self.call("get_order", {"order_id": oid})
                    obs = Observation("cancel_order", {}, chk.ok and chk.result.get("status") == "CANCELLED", chk.result)
                if obs.ok:
                    cancelled_ok[oid] = True
                    say.append(f"I've cancelled order {oid}.")
                else:
                    cancelled_ok[oid] = False
                    say.append(
                        f"I couldn't cancel {oid} because it has already shipped, so I haven't refunded it yet - "
                        "once it's returned, the refund will follow."
                        if obs.code == "not_cancellable" else f"I couldn't cancel {oid}; a colleague will follow up.")
                    if obs.code != "not_cancellable":
                        escalate.append(f"cancel failed for {oid}")
                    continue

            if req["refund"]:
                outcome = yield from self.refund(oid, order)
                if outcome[0] == "refunded":
                    say.append(f"Your refund of {inr(outcome[1])} for {oid} has been initiated and is pending settlement.")
                elif outcome[0] == "ineligible":
                    say.append(f"{oid} isn't eligible for a refund ({outcome[1]}).")
                elif outcome[0] == "denied":
                    say.append(f"A supervisor didn't approve the refund for {oid}, so I've escalated it for review.")
                    escalate.append(f"refund approval denied for {oid}")
                else:
                    say.append(f"I couldn't confirm the refund for {oid}, so I've escalated it rather than risk a duplicate.")
                    escalate.append(f"refund unconfirmed for {oid}")

        if req["address"]:
            obs = yield from self.call("get_customer", {"customer_id": cust})
            if obs.ok and obs.result.get("identity_verified"):
                obs = yield from self.call("update_address", {"customer_id": cust, "address": req["address"]})
                if obs.ok:
                    say.append(f"Your delivery address has been updated to {req['address']}.")
            else:
                say.append("I need to verify your identity before changing your address, so I've handed this to a colleague.")
                escalate.append("address change requested for unverified identity")
                req["replacement"] = False

        if req["replacement"]:
            for oid in req["orders"]:
                obs = yield from self.call("create_replacement_order", {"order_id": oid})
                if obs.ok:
                    say.append(f"A replacement order {obs.result['order_id']} will ship to {obs.result['ship_to']}.")

        if escalate:
            yield from self.call("escalate_to_human", {"reason": "; ".join(escalate)})
        yield FinalAnswer(" ".join(say) or "I've looked into this and everything is in order.")

    def refund(self, oid: str, order: dict[str, Any]) -> Generator[Action, Observation, tuple[str, Any]]:
        obs = yield from self.call("check_refund_eligibility", {"order_id": oid})
        if obs.ok and "eligible" not in obs.result:
            obs = yield from self.call("check_refund_eligibility", {"order_id": oid})
        if not obs.ok or "eligible" not in obs.result:
            return ("unconfirmed", None)
        if not obs.result["eligible"]:
            return ("ineligible", obs.result.get("reason", "not eligible"))
        amount = int(obs.result.get("max_amount") or order["amount"])
        if obs.result.get("requires_approval") and self.rng.random() >= self.SKIP_APPROVAL_P:
            ap = yield from self.call("request_human_approval", {
                "action": "issue_refund", "order_id": oid, "amount": amount,
                "reason": "refund above approval threshold"})
            if not (ap.ok and ap.result.get("approved")):
                return ("denied", None)
        key = f"refund:{oid}:{amount}"
        for _ in range(self.MAX_ATTEMPTS):
            obs = yield from self.call("issue_refund", {"order_id": oid, "amount": amount, "idempotency_key": key})
            if obs.ok:
                return ("refunded", amount)
            if obs.code == "timeout":  # the write may have committed: verify before retrying
                st = yield from self.call("get_refund_status", {"order_id": oid})
                if st.ok and any(r.get("idempotency_key") == key for r in st.result.get("refunds", [])):
                    return ("refunded", amount)
                continue  # retry is safe: same idempotency key
            if obs.code not in ("timeout", "service_unavailable", "rate_limited"):
                return ("unconfirmed", None)
        return ("unconfirmed", None)
