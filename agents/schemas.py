"""Typed contracts between collectors, agents, validator and renderer."""
from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

# ---------- Collector output ----------

class SourceResult(BaseModel):
    """What every collector returns. Collectors never raise; failures become status='gap'."""
    status: Literal["ok", "partial", "gap"]
    reason: str | None = None           # why partial/gap (shown in the brief)
    source: str | None = None           # e.g. "serpapi", "apify", "manual_csv", "fixture"
    synthetic: bool = False             # True only for tests/sample_fixtures (never real data)
    collected_at: datetime
    filters: dict[str, Any] = Field(default_factory=dict)   # geo, active_status, terms…
    period: str | None = None           # reporting period, e.g. "Past 90 days (2026-07-17 → 2026-10-17)"
    data: Any = None                    # collector-specific raw payload


# ---------- Agent 1: Ads Analyst ----------

class AdPick(BaseModel):
    ad_id: str = Field(description="Short ID from the input data, e.g. 'A3'. Must exist in the input.")
    relevance_reason: str = Field(description="Why this ad matters for a snack brand in Egypt.")
    message_fact: str = Field(description="FACT: what the ad says/shows. One line, no opinion.")
    interpretation: str = Field(description="INTERPRETATION: the 'so what' for Koshk. One line.")


class AdsAnalysis(BaseModel):
    ads: list[AdPick] = Field(default_factory=list, max_length=3)
    note: str | None = Field(default=None, description="Why fewer than 3 (or none) were picked.")


# ---------- Agent 2: Trends Analyst ----------

class TrendFinding(BaseModel):
    finding_id: str = Field(description="F1, F2 or F3.")
    term: str = Field(description="Search term exactly as in the input data.")
    values: list[float | str] = Field(
        default_factory=list,
        description="Exact values copied from the input (e.g. 34, 51, 'Breakout', '<1'). Never computed or rounded.",
    )
    period: str = Field(description="Dates the values cover, as given in the input.")
    fact: str = Field(description="FACT: what the data shows. One line, no opinion.")
    interpretation: str = Field(description="INTERPRETATION: the 'so what' for Koshk. One line.")
    insufficient_data: bool = False


class TrendsAnalysis(BaseModel):
    findings: list[TrendFinding] = Field(default_factory=list, max_length=3)
    note: str | None = None


# ---------- Agent 3: Content Strategist ----------

class ContentIdea(BaseModel):
    title: str               # what the post is, max ~8 words
    hook_ar: str             # ready-to-use opening line in Egyptian Arabic (عامية), max ~10 words
    why_now: str             # 1–2 sentences linking the idea to this week's evidence
    based_on: list[str]      # IDs of findings/ads it uses, e.g. ["F2", "A3"]; must exist in validated data
    format: str              # e.g. "15-sec Reel / TikTok", "Story poll", "TikTok duet challenge"
    audience: str            # who it targets, e.g. "Parents, kids 8–14"  (suggestion)
    post_timing: str         # suggested timing, e.g. "During the next big match"  (suggestion)


class ContentPlan(BaseModel):
    ideas: list[ContentIdea] = Field(default_factory=list, max_length=3)
    note: str | None = None
