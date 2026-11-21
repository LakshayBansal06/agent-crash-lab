"""Agent contract.

An agent sees a task, a list of tool schemas and the session context, then
repeatedly returns either a ``ToolCall`` or a ``FinalAnswer``. Anything that
can be wrapped in that loop - a scripted policy, an LLM tool-calling loop, a
LangGraph graph, a remote HTTP agent - can be tested by the platform.
"""
from __future__ import annotations

import random
from dataclasses import dataclass, field
from typing import Any
from collections.abc import Generator


@dataclass
class ToolCall:
    tool: str
    args: dict[str, Any] = field(default_factory=dict)


@dataclass
class FinalAnswer:
    text: str


@dataclass
class Observation:
    tool: str
    args: dict[str, Any]
    ok: bool
    result: Any = None
    error: dict[str, Any] | None = None

    @property
    def code(self) -> str | None:
        return (self.error or {}).get("code")


Action = ToolCall | FinalAnswer


class Agent:
    name = "agent"
    version = "0.0.0"
    framework = "custom-python"

    def reset(self, task: str, tools: list[dict[str, Any]], context: dict[str, Any], rng: random.Random) -> None:
        raise NotImplementedError

    def act(self, observation: Observation | None) -> Action:
        raise NotImplementedError

    def describe(self) -> dict[str, Any]:
        return {"name": self.name, "version": self.version, "framework": self.framework}


class ScriptedAgent(Agent):
    """Write an agent as a generator: ``obs = yield ToolCall(...)``."""

    def reset(self, task, tools, context, rng):
        self.task, self.tools, self.context, self.rng = task, tools, context, rng
        self._gen: Generator[Action, Observation, None] = self.policy()
        self._started = False

    def policy(self) -> Generator[Action, Observation, None]:  # pragma: no cover - abstract
        raise NotImplementedError
        yield

    def act(self, observation):
        try:
            if not self._started:
                self._started = True
                return next(self._gen)
            return self._gen.send(observation)
        except StopIteration:
            return FinalAnswer("I'm sorry, I wasn't able to complete that.")
