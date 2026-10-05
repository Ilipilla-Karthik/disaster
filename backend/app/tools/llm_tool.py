"""LLM tool with graceful degradation (multi-agent backbone).

The LLM is used for *language* tasks only:

* extracting structure from unstructured reports,
* summarising situations into human-readable narrative,
* generating alternative plans and reviewer commentary.

It is deliberately **not** used for:

* priority scores (deterministic model in ``app.services.priority_calculator``),
* resource matching / allocation arithmetic (deterministic engine),
* shelter capacity maths,
* any decision that dispatches or authorises a resource.

If no provider is configured or the call fails, ``complete_json`` falls back to
``fallback_payload`` and reports ``used_llm=False`` so the caller can surface
that the narrative came from templates rather than a model.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from dataclasses import dataclass
from typing import Any

from app.core.config import settings

logger = logging.getLogger(__name__)

__all__ = ["LLMTool", "get_llm", "llm_status", "SAFETY_PREAMBLE"]


SAFETY_PREAMBLE = (
    "You are supporting an academic/prototype emergency decision-support system. "
    "You MUST NOT dispatch resources, control vehicles, issue evacuation orders, "
    "perform medical triage, or override authorised emergency commanders. "
    "Never invent facts, resources, roads, capacities, or locations. If information "
    "is missing or uncertain, state 'Verification Required'. "
    "Only use information present in the provided context."
)


@dataclass
class LLMResult:
    used_llm: bool
    data: dict[str, Any]
    raw_text: str | None = None
    error: str | None = None
    provider: str | None = None
    model: str | None = None


class LLMTool:
    def __init__(self) -> None:
        self.provider = (settings.LLM_PROVIDER or "none").lower()
        self.model = settings.LLM_MODEL
        self._client: Any = None

    # -- availability -------------------------------------------------------

    @property
    def available(self) -> bool:
        if self.provider == "openai" and settings.OPENAI_API_KEY:
            return True
        if self.provider == "gemini" and settings.GEMINI_API_KEY:
            return True
        if self.provider == "anthropic" and settings.ANTHROPIC_API_KEY:
            return True
        return False

    def _get_client(self) -> Any:
        if self._client is not None:
            return self._client
        if self.provider == "openai":
            from openai import AsyncOpenAI

            self._client = AsyncOpenAI(api_key=settings.OPENAI_API_KEY)
        elif self.provider == "gemini":
            from google import genai

            self._client = genai.Client(api_key=settings.GEMINI_API_KEY)
        elif self.provider == "anthropic":
            from anthropic import AsyncAnthropic

            self._client = AsyncAnthropic(api_key=settings.ANTHROPIC_API_KEY)
        return self._client

    # -- public API ---------------------------------------------------------

    async def complete_json(
        self,
        system_prompt: str,
        user_payload: dict[str, Any],
        fallback_payload: dict[str, Any],
        schema_hint: str = "",
    ) -> LLMResult:
        """Ask the model for JSON. Falls back to ``fallback_payload`` on any failure."""
        if not self.available:
            return LLMResult(
                used_llm=False,
                data=fallback_payload,
                error="llm_not_configured",
                provider=self.provider,
            )

        instruction = (
            f"{SAFETY_PREAMBLE}\n\n{system_prompt}\n\n"
            "Respond with a single JSON object and nothing else. "
            f"Expected shape: {schema_hint or 'a JSON object'}."
        )
        prompt = json.dumps(user_payload, default=str, indent=2)

        try:
            text = await self._invoke(instruction, prompt)
            parsed = _extract_json(text)
            if parsed is None:
                return LLMResult(
                    used_llm=False,
                    data=fallback_payload,
                    raw_text=text,
                    error="unparseable_response",
                    provider=self.provider,
                    model=self.model,
                )
            return LLMResult(
                used_llm=True,
                data=parsed,
                raw_text=text,
                provider=self.provider,
                model=self.model,
            )
        except Exception as exc:
            logger.warning("LLM call failed (%s); using deterministic fallback", exc)
            return LLMResult(
                used_llm=False,
                data=fallback_payload,
                error=f"{type(exc).__name__}: {exc}",
                provider=self.provider,
                model=self.model,
            )

    async def complete_text(
        self,
        system_prompt: str,
        user_payload: dict[str, Any],
        fallback_text: str,
    ) -> LLMResult:
        if not self.available:
            return LLMResult(used_llm=False, data={"text": fallback_text},
                             error="llm_not_configured", provider=self.provider)
        try:
            text = await self._invoke(
                f"{SAFETY_PREAMBLE}\n\n{system_prompt}\n\nRespond in plain prose, under 250 words.",
                json.dumps(user_payload, default=str, indent=2),
            )
            return LLMResult(used_llm=True, data={"text": text}, raw_text=text,
                             provider=self.provider, model=self.model)
        except Exception as exc:
            return LLMResult(used_llm=False, data={"text": fallback_text},
                             error=f"{type(exc).__name__}: {exc}", provider=self.provider)

    # -- provider calls -----------------------------------------------------

    async def _invoke(self, system: str, prompt: str) -> str:
        client = self._get_client()
        if self.provider == "openai":
            resp = await client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": prompt},
                ],
                temperature=settings.LLM_TEMPERATURE,
                response_format={"type": "json_object"}
                if "JSON object" in system
                else None,
            )
            return resp.choices[0].message.content or ""

        if self.provider == "gemini":
            from google.genai import types

            resp = await asyncio.wait_for(
                client.aio.models.generate_content(
                    model=self.model,
                    contents=prompt,
                    config=types.GenerateContentConfig(
                        system_instruction=system,
                        temperature=settings.LLM_TEMPERATURE,
                        response_mime_type=(
                            "application/json" if "JSON object" in system else "text/plain"
                        ),
                    ),
                ),
                timeout=45,
            )
            return resp.text or ""

        # anthropic
        resp = await client.messages.create(
            model=self.model,
            max_tokens=2000,
            temperature=settings.LLM_TEMPERATURE,
            system=system,
            messages=[{"role": "user", "content": prompt}],
        )
        return "".join(block.text for block in resp.content if block.type == "text")


def _extract_json(text: str | None) -> dict[str, Any] | None:
    """Tolerate markdown fences or surrounding prose around the JSON body."""
    if not text:
        return None
    candidate = text.strip()
    fence = re.search(r"```(?:json)?\s*(.+?)```", candidate, re.DOTALL)
    if fence:
        candidate = fence.group(1).strip()
    try:
        parsed = json.loads(candidate)
        return parsed if isinstance(parsed, dict) else None
    except json.JSONDecodeError:
        pass
    # Last resort: first balanced object in the text.
    start = candidate.find("{")
    while start != -1:
        depth, in_str, escaped = 0, False, False
        for i in range(start, len(candidate)):
            ch = candidate[i]
            if in_str:
                if escaped:
                    escaped = False
                elif ch == "\\":
                    escaped = True
                elif ch == '"':
                    in_str = False
                continue
            if ch == '"':
                in_str = True
            elif ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    try:
                        parsed = json.loads(candidate[start : i + 1])
                        return parsed if isinstance(parsed, dict) else None
                    except json.JSONDecodeError:
                        break
        start = candidate.find("{", start + 1)
    return None


_llm = LLMTool()


def get_llm() -> LLMTool:
    return _llm


def llm_status() -> dict[str, Any]:
    return {
        "configured": _llm.available,
        "mode": "llm" if _llm.available else "deterministic_fallback",
        "provider": _llm.provider,
        "model": _llm.model if _llm.available else None,
        "note": (
            "Language tasks fall back to deterministic templates when no provider "
            "is configured. Scores and allocations are never LLM-generated."
        ),
    }