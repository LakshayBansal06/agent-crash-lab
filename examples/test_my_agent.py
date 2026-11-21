"""Test your own agent with the SDK. Run: python examples/test_my_agent.py"""
from crashlab.sdk import CrashLab, ToolCallFailed


def my_agent(task: str, tools, context: dict) -> str:
    """A small refund agent. Try deleting the idempotency key or the status
    check and re-running to watch Crash Lab catch the double refund."""
    order = tools.get_order(order_id="ORD-1001")
    if order["customer_id"] != context["customer_id"]:
        tools.escalate_to_human(reason="order belongs to another customer")
        return "I can't act on that order."
    elig = tools.check_refund_eligibility(order_id="ORD-1001")
    if not elig.get("eligible"):
        return f"ORD-1001 isn't eligible for a refund ({elig.get('reason')})."
    key = "refund:ORD-1001"
    for _ in range(3):
        try:
            tools.issue_refund(order_id="ORD-1001", amount=elig["max_amount"], idempotency_key=key)
            break
        except ToolCallFailed as err:
            if err.code == "timeout":
                status = tools.get_refund_status(order_id="ORD-1001")
                if any(r["idempotency_key"] == key for r in status["refunds"]):
                    break
    return "Your refund for ORD-1001 has been initiated and is pending settlement."


if __name__ == "__main__":
    report = CrashLab().test(my_agent, scenarios=["ACL-ECOM-0001", "ACL-ECOM-0002", "ACL-ECOM-0003", "ACL-ECOM-0004"], repeats=3)
    print(report.summary())
