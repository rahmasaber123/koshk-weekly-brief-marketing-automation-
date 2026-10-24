"""Agent 1 — picks up to 3 relevant competitor ads and separates fact from 'so what'."""
from __future__ import annotations

from typing import Any

from agents.base import run_agent
from agents.schemas import AdsAnalysis, SourceResult

SYSTEM_PROMPT = """You are the Ads Analyst for {BRAND}, {BRAND_DESC} that competes with
{COMPETITORS}. You receive ads that were ACTIVE in the Meta Ad Library for Egypt at collection time.

Task: pick up to 3 ads that tell {BRAND} the most about what competitors are doing this week.
- `ad_id` must be copied exactly from an ad's `id` field (e.g. "A2").
- Skip ads whose advertiser is clearly not the {CATEGORY} brand (the searches are keyword-based, so unrelated
  companies can appear). Pick at most ONE ad per competitor when other competitors have relevant ads; two
  from the same brand only if they carry clearly different messages.
- `relevance_reason`: why this ad matters for a {CATEGORY} brand in Egypt (one short sentence).
- `message_fact`: FACT — what the ad text says or offers, based only on `ad_text`. Max ~20 words, no opinion.
  Any number you mention must appear in that ad's text.
- `interpretation`: INTERPRETATION — the "so what" for {BRAND}'s CONTENT, one sentence, max ~18 words.
  Be specific: name the moment, audience or emotion the competitor is claiming and a concrete different
  angle {BRAND}'s posts could take. No generic phrases ("leverage", "strengthen brand identity", "connect
  with consumers"). Never assume anything about {BRAND} itself (its origin, sourcing, quality, popularity),
  and never suggest {BRAND} copy the competitor's theme (e.g. if {FIRST_COMPETITOR} talks about local sourcing, the
  angle is NOT "{BRAND} should talk about its sourcing" — it's a different moment, tone or audience).
  No product, packaging or pricing advice. Read numbers carefully: "5-pound bag" means a price of 5 EGP.
- Each interpretation must be grounded in THIS ad: refer to its actual message, offer or moment (quote a short
  phrase if useful) and say what it tells {BRAND} about the competitor's strategy, in one sentence.
  Pattern: <Brand> is pushing <specific message/moment>, which means {BRAND} could <different angle or gap>.
  Never write a sentence that could apply to any ad (e.g. 'shows strong branding', 'engages the audience').
- Write in English; you may quote a short Arabic phrase from the ad.
- Every ad comes from a competitor's official page, so it is relevant. If several ads repeat the same
  message, pick one of them. Return an empty list only if there are no ads.
"""


def build_payload(ads_result: SourceResult) -> dict:
    ads = [{k: a[k] for k in ("id", "competitor", "page_name", "start_date", "ad_text", "platforms")}
           for a in ads_result.data.get("ads", [])]
    return {"source": "Meta Ad Library", "filters": ads_result.filters,
            "collected_at": ads_result.collected_at.isoformat(), "ads": ads}


def analyse_ads(ads_result: SourceResult, tracker: Any = None, model: Any = None) -> AdsAnalysis:
    from validate import ads_problem
    return run_agent("ads_analyst", SYSTEM_PROMPT, AdsAnalysis, build_payload(ads_result), tracker, model,
                     check=ads_problem)