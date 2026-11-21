from .base import Environment, ToolError, ToolSpec
from .ecommerce import EcommerceEnvironment, build_state

ENVIRONMENTS = {"ecommerce": (EcommerceEnvironment, build_state)}


def make_environment(scenario: dict) -> Environment:
    cls, builder = ENVIRONMENTS[scenario["environment"]]
    init = scenario.get("initial_state") or {}
    state = builder(init.get("fixture", "base_store"), init.get("patch"))
    return cls(state)


__all__ = ["Environment", "ToolError", "ToolSpec", "EcommerceEnvironment", "make_environment", "ENVIRONMENTS"]
