from .base import Agent, FinalAnswer, Observation, ProviderError, ScriptedAgent, ToolCall
from .support import SupportAgentV1, SupportAgentV2

REGISTRY = {
    "support-agent@1.0.0": SupportAgentV1,
    "support-agent@2.0.0": SupportAgentV2,
    "v1": SupportAgentV1,
    "v2": SupportAgentV2,
}


LLM_REFS = ("llm", "llm-agent", "gemini", "gemini-agent")  # model-backed agents, built on demand


def missing_key(ref: str) -> str | None:
    """The environment variable a model-backed agent needs but doesn't have, or None.
    Without it every episode would 'fail' on an auth error, which reads like a real finding."""
    import os

    need = {"llm": "ANTHROPIC_API_KEY", "llm-agent": "ANTHROPIC_API_KEY",
            "gemini": "GEMINI_API_KEY", "gemini-agent": "GEMINI_API_KEY"}.get(ref)
    return need if need and not os.environ.get(need) else None


def make_agent(ref: str) -> Agent:
    if ref in ("llm", "llm-agent"):
        from .llm import LLMToolAgent

        return LLMToolAgent()
    if ref in ("gemini", "gemini-agent"):
        from .llm import GeminiToolAgent

        return GeminiToolAgent()
    if ref not in REGISTRY:
        raise KeyError(f"unknown agent {ref!r}; known: {sorted(REGISTRY)}, 'llm' or 'gemini'")
    return REGISTRY[ref]()


class ReplayAgent(Agent):
    """Replays a recorded action list verbatim - used for deterministic
    reproduction and delta-debugging minimization."""

    framework = "replay"

    def __init__(self, actions, source: dict | None = None) -> None:
        self.actions = list(actions)
        self.source = source or {}
        self.name = self.source.get("name", "replay")
        self.version = self.source.get("version", "replay")

    def reset(self, task, tools, context, rng):
        self._i = 0

    def act(self, observation):
        if self._i >= len(self.actions):
            return FinalAnswer("")
        a = self.actions[self._i]
        self._i += 1
        return FinalAnswer(a.text or "") if a.kind == "final" else ToolCall(a.tool, dict(a.args))


__all__ = ["LLM_REFS", "missing_key", "ProviderError", "Agent", "ToolCall", "FinalAnswer", "Observation", "ScriptedAgent", "make_agent", "REGISTRY",
           "ReplayAgent", "SupportAgentV1", "SupportAgentV2"]
