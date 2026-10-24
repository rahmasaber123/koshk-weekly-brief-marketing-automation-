"""Agent 3 — turns VALIDATED ads and findings into up to 3 content ideas for the brand."""
from __future__ import annotations

from typing import Any

from agents.base import run_agent
from agents.schemas import ContentPlan

SYSTEM_PROMPT = """You are the Content Strategist for {BRAND}, {BRAND_DESC} competing with
{COMPETITORS}. You receive this week's VALIDATED competitor ads (IDs like "A3") and Google Trends
findings (IDs like "F2"). Propose up to 3 social content ideas for {BRAND}.

Ground truth vs opinion: `ad_text`, `fact` and `values` are collected data (ground truth). `interpretation`
is an analyst's opinion — use it as a hint, but build each idea on the ground truth it cites.

Rules:
- Every idea must use at least one of the provided IDs in `based_on`, copied exactly. Use only IDs that exist.
- Return 3 ideas whenever the evidence supports 3, and spread them across ads AND trend findings (at least
  one idea should cite a trend finding "F#" when findings exist). Fewer only if the evidence is too thin.
- These are CONTENT ideas (posts), not product, packaging or pricing advice.
- Never adopt a competitor's theme for {BRAND}: if a competitor's ad is about heritage, local sourcing or
  premium quality, {BRAND}'s idea must take a different angle (moment, mood, audience), not the same theme.
- Differentiate {BRAND} from competitors — react to what they do, never copy their ads or slogans, and never
  name {COMPETITORS_OR} in {BRAND}'s post (title or hook). Competitors are context, not the topic.
- {BRAND} is fictional and new: NEVER mention or imply any {BRAND} flavour, new product, price, pack size, offer,
  promotion, ingredient, origin or sourcing, and never claim people already know, love or ask about {BRAND}.
- Don't build an idea on a rising query or term whose meaning the findings say is unclear.
- Each idea must start from its evidence: the moment, audience, topic or search interest named in the
  ads/findings it cites. If the evidence doesn't point to a clear angle, return fewer ideas.
- Each idea must be strategic and specific to THIS week's evidence: `why_now` names the exact query (quoted
  in Arabic) or the competitor's actual message it reacts to, and the gap {BRAND} fills.
  Pattern: <evidence> shows <what people want / what the competitor claims>, so {BRAND} owns <a different
  moment, mood or audience>. Never propose an idea any {CATEGORY} brand could post any week (e.g. 'share your
  {CATEGORY} story', 'try something new', 'how do you eat your {CATEGORY_PLURAL}').
- `title`: what the post is, max ~8 words, English.
- `hook_ar`: a ready-to-use opening line in Egyptian colloquial Arabic (عامية مصرية), casual and
  conversational, as young Egyptians type on social media — not formal Arabic (فصحى), not a translated
  English slogan. It must speak to the specific topic/moment from the evidence the idea cites
  (e.g. the search term, the rising query, or the moment a competitor is targeting). Max ~10 words,
  no quote marks.
- `why_now`: 1–2 short sentences linking the idea to this week's evidence (no new numbers).
  Google Trends values are relative interest, not search volumes.
- `format`: suited to Egyptian social platforms, e.g. "15-sec Reel / TikTok", "Instagram Story poll",
  "TikTok duet challenge", "Facebook carousel".
- `audience` and `post_timing` are SUGGESTIONS. Timing must be a real recurring moment (e.g. "Thursday
  night", "Before the next league match", "School days 2–4 pm") — never an invented event.
"""


def build_payload(valid_ads: list[dict], valid_findings: list[dict], gaps: list[str]) -> dict:
    ads = [{"id": a["id"], "competitor": a.get("competitor"), "page_name": a.get("page_name"),
            "start_date": a.get("start_date"), "ad_text": (a.get("ad_text") or "")[:400],   # ground truth
            "message_fact": a.get("message_fact"), "interpretation": a.get("interpretation")}
           for a in valid_ads]
    findings = [{k: f.get(k) for k in ("finding_id", "term", "fact", "values", "period", "interpretation")}
                for f in valid_findings if not f.get("greyed")]
    from config import load_config
    b = load_config()["brand"]
    desc = b["description"].removeprefix("a ").removeprefix("an ")
    return {"brand": f"{b['name']} ({desc}; no known flavours, prices or promotions)",
            "market": "Egypt", "ads": ads, "findings": findings, "data_gaps_this_week": gaps}


def plan_content(valid_ads: list[dict], valid_findings: list[dict], gaps: list[str],
                 tracker: Any = None, model: Any = None) -> ContentPlan:
    from validate import plan_problem
    need = min(3, len(valid_ads) + len([f for f in valid_findings if not f.get("greyed")]))

    def check(plan: ContentPlan) -> str | None:
        if len(plan.ideas) < need:
            return (f"Return {need} ideas, not {len(plan.ideas)}: there is enough evidence. Each idea can cite a "
                    "different ad or finding, or take a clearly different angle on the same one.")
        return plan_problem(plan)
    return run_agent("strategist", SYSTEM_PROMPT, ContentPlan,
                     build_payload(valid_ads, valid_findings, gaps), tracker, model, check=check)
