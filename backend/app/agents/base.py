"""Base class shared by all agents.

Enforces the two architectural rules that matter for this domain:

1. **Agents communicate only through the shared ``WorkflowState``.** An agent
   reads the keys it needs and returns a partial state; it never reaches into
   another agent's implementation.
2. **Every agent run is traced.** ``BaseAgent.run`` records agent name, graph
   node, duration, whether the LLM was used, and a digest of input/output, so
   any recommendation can be explained after the fact.

Agents also carry an explicit ``PROHIBITIONS`` list. The reviewer agent checks
recommendations against these, which is how "the AI must not dispatch
resources" is enforced mechanically rather than by convention.
"""

from __future__ import annotations

import logging
import time
import uuid
from datetime import datetime, timezone
from typing import Any, ClassVar

from app.orchestration.state import WorkflowState

logger = logging.getLogger(__name__)

__all__ = ["BaseAgent", "AgentError"]


class AgentError(RuntimeError):
    """Raised when an agent cannot complete its step."""


class BaseAgent:
    """Common agent scaffolding: identity, prompt, tracing, safety contract."""

    name: ClassVar[str] = "base_agent"
    role: ClassVar[str] = "Unspecified"
    system_prompt: ClassVar[str] = ""
    node_name: ClassVar[str] = "base"
    #: Actions this agent must never take. Checked by the ReviewerAgent.
    PROHIBITIONS: ClassVar[list[str]] = [
        "dispatch emergency resources",
        "control or direct emergency vehicles",
        "issue evacuation orders",
        "perform medical triage",
        "override an authorised emergency commander",
        "invent resources, roads, capacities, or locations",
        "delete or auto-merge duplicate reports",
    ]
    uses_llm: ClassVar[bool] = False

    # -- tracing ------------------------------------------------------------

    def _trace(
        self,
        state: WorkflowState,
        output: dict[str, Any],
        *,
        started: float,
        llm_used: bool = False,
        error: str | None = None,
    ) -> dict[str, Any]:
        entry = {
            "agent": self.name,
            "role": self.role,
            "node": self.node_name,
            "run_id": state.get("run_id"),
            "at": datetime.now(timezone.utc).isoformat(),
            "duration_ms": round((time.perf_counter() - started) * 1000, 2),
            "llm_used": llm_used,
            "error": error,
            "output_keys": sorted(output.keys()),
        }
        if error:
            entry["failed"] = True
        return {"trace": [entry]}

    @staticmethod
    def new_run_id(prefix: str = "RUN") -> str:
        return f"{prefix}-{uuid.uuid4().hex[:8].upper()}"

    # -- contract -----------------------------------------------------------

    async def execute(self, state: WorkflowState) -> dict[str, Any]:
        """Do the agent's work. Subclasses implement this."""
        raise NotImplementedError

    async def run(self, state: WorkflowState) -> dict[str, Any]:
        """Execute with tracing. Returns a partial state update."""
        started = time.perf_counter()
        try:
            update = await self.execute(state)
        except Exception as exc:
            logger.exception("Agent %s failed", self.name)
            return {
                "trace": self._trace(
                    state, {}, started=started, error=f"{type(exc).__name__}: {exc}"
                ),
                "agent_errors": {self.name: f"{type(exc).__name__}: {exc}"},
            }

        update = update or {}
        trace = self._trace(
            state,
            update,
            started=started,
            llm_used=bool(update.pop("_llm_used", False)),
        )
        return {**update, **trace}