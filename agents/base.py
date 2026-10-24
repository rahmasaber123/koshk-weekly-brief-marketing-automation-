"""Shared agent runner: PydanticAI, typed output, temperature from config, rule-check retries, usage tracking."""
from __future__ import annotations

import json
import logging
import os
import time
from typing import Any, Callable, TypeVar

from pydantic import BaseModel
from pydantic_ai import Agent, ModelRetry, RunContext

from config import load_config, simulate_fail

log = logging.getLogger(__name__)
T = TypeVar("T", bound=BaseModel)

COMMON_RULES = """
Hard rules:
- Use ONLY the data in the user message. Never invent ads, IDs, numbers, dates, queries, flavours, prices or promotions.
- Keep FACT (what the data literally shows) separate from INTERPRETATION (your judgement / "so what").
- Copy IDs and numbers exactly as they appear in the data. Do not compute, round or convert numbers.
- If the data is not enough, return fewer items (or none) and say why in `note`. Never pad to reach a count.
"""


def fill_brand(text: str) -> str:
    """Fill {BRAND}, {COMPETITORS}, ... in a prompt from config.yaml (brand + competitor names)."""
    cfg = load_config()
    b = cfg["brand"]
    names = list(dict.fromkeys(c["name"] for c in cfg["ads"]["competitors"]))

    def join(word: str) -> str:
        return names[0] if len(names) == 1 else ", ".join(names[:-1]) + f" {word} " + names[-1]
    for key, value in {"{BRAND_DESC}": b["description"], "{BRAND}": b["name"], "{CATEGORY_PLURAL}": b["category_plural"],
                       "{CATEGORY}": b["category"], "{COMPETITORS_OR}": join("or"), "{COMPETITORS}": join("and"),
                       "{FIRST_COMPETITOR}": names[0]}.items():
        text = text.replace(key, value)
    return text


class LLMUnavailable(Exception):
    """LLM down, key missing, or output invalid after the retry."""


def to_prompt(payload: dict) -> str:
    return "DATA (JSON):\n" + json.dumps(payload, ensure_ascii=False, indent=1, default=str)


def run_agent(name: str, system_prompt: str, output_type: type[T], payload: dict,
              tracker: Any = None, model: Any = None, check: Callable[[Any], str | None] | None = None) -> T:
    """Run one agent. `check(output)` returns a rule violation (or None); a violation is sent back to the model
    to fix (up to 2 retries). `model` overrides config (tests). Raises LLMUnavailable."""
    if simulate_fail() == "llm":
        raise LLMUnavailable("SIMULATE_FAIL=llm (simulated LLM outage)")
    cfg = load_config()["llm"]
    if model is None and not os.getenv("OPENAI_API_KEY"):
        raise LLMUnavailable("OPENAI_API_KEY not set")
    agent = Agent(model or cfg["model"], output_type=output_type, system_prompt=fill_brand(system_prompt) + COMMON_RULES,
                  model_settings={"temperature": cfg["temperature"]}, retries=2, name=name)
    if check is not None:
        @agent.output_validator
        def _rules(ctx: RunContext, output: Any) -> Any:
            problem = check(output)
            if problem and ctx.retry < ctx.max_retries:
                log.info(f"[{name}] asking the model to fix: {problem[:160]}")
                raise ModelRetry(problem)
            return output  # still failing after retries: validate.py drops the offending items
    last_err: Exception | None = None
    for attempt in range(2):  # first try + retry once
        try:
            result = agent.run_sync(to_prompt(payload))
            if tracker is not None:
                tracker.add_llm_usage(result)
            log.info(f"[{name}] OK")
            return result.output
        except Exception as e:  # network, rate limit, invalid output after PydanticAI's own retry
            last_err = e
            log.warning(f"[{name}] attempt {attempt + 1} failed: {type(e).__name__}: {str(e)[:150]}")
            if attempt == 0:
                time.sleep(3)
    raise LLMUnavailable(f"{name}: {type(last_err).__name__}: {str(last_err)[:200]}")