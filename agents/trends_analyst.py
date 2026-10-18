"""Agent 2 — picks up to 3 trend facts (written by code from Google Trends numbers) and adds the 'so what'.

The facts are ground truth computed by code; the agent never writes or edits a fact or a number.
"""
from __future__ import annotations

from typing import Any

from agents.base import run_agent
from agents.schemas import SourceResult, TrendsAnalysis

SYSTEM_PROMPT = """You are the Trends Analyst for Koshk, a fictional Egyptian snack brand that competes with
Chipsy, Doritos and Lion. You receive CANDIDATE FACTS about Google Trends in Egypt (Arabic search terms).
Each candidate was computed by code from the raw data and is ground truth: you cannot change it.

Google Trends values are RELATIVE interest (0–100), not search volumes. Never call them searches,
volume or people.

Task: choose up to 3 candidates that matter most for a snack brand THIS WEEK and add an interpretation.
- Priority: (1) "weekly_change" candidates with the biggest moves, (2) "rising_query" candidates.
- Return 3 whenever 3 candidates exist. Prefer a mix (not three of the same term) when possible.
  Return fewer only if fewer candidates exist, and explain in `note`.
- `candidate_id`: copy exactly (e.g. "C2").
- `interpretation`: the "so what" for Koshk's CONTENT, one sentence, max ~18 words: what people seem to be
  interested in and which conversation Koshk could join. No numbers, no product or flavour development
  advice, no generic phrases ("leverage", "engage with trends").
- Each interpretation is ONE concrete sentence about what this means for Koshk (an opportunity or a risk).
  Never write generic advice like 'explore this trend' or 'investigate for content ideas'.
- A rising query can mean something other than snacks. If its meaning is unclear, say in the
  interpretation that it should be checked before acting. Never guess what it refers to.
"""


def build_payload(trends_result: SourceResult) -> dict:
    d = trends_result.data
    return {"source": "Google Trends", "geo": trends_result.filters.get("geo"),
            "period": trends_result.period, "scale_note": d.get("scale_note"),
            "candidates": [{k: c[k] for k in ("id", "kind", "term", "fact", "period")}
                           for c in d.get("candidates", [])],
            "terms_with_insufficient_data": [t for t, s in (d.get("summary") or {}).items()
                                             if s.get("status") != "ok"]}


def analyse_trends(trends_result: SourceResult, tracker: Any = None, model: Any = None) -> TrendsAnalysis:
    return run_agent("trends_analyst", SYSTEM_PROMPT, TrendsAnalysis, build_payload(trends_result), tracker, model)
