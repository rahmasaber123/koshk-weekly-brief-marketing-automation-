"""Active competitor ads in Egypt from the Meta Ad Library via Apify actor apify/facebook-ads-scraper.

Input fields used (checked against the actor's input schema): startUrls, resultsLimit, activeStatus,
isDetailsPerAd, onlyTotal, includeAboutPage. Country=EG and active status are set in each Ad Library URL.
Fallback chain: Apify (retry 2x) -> manual_inputs/ads.csv -> gap with a ready Ad Library search link.
Images are downloaded at collection time because Facebook CDN links expire.
"""
from __future__ import annotations

import logging
import os
import shutil
import time
from datetime import datetime, timezone
from typing import Any

import requests

from agents.schemas import SourceResult
from collectors.common import (PermanentError, TransientError, check_response, load_fixture,
                               read_manual_csv, save_fixture, with_retry)
from config import (IMAGES_DIR, MANUAL_DIR, REAL_FIXTURES_DIR, ROOT, fixtures_dir, fixtures_mode,
                    load_config, now_cairo, simulate_fail)

log = logging.getLogger(__name__)
API = "https://api.apify.com/v2"
ITEMS_FIXTURE = "ads_items.json"
RUN_FIXTURE = "ads_run.json"
TERMINAL = {"SUCCEEDED", "FAILED", "TIMED-OUT", "ABORTED"}


# ---------------- public ----------------

def collect_ads(tracker: Any = None) -> SourceResult:
    """Never raises. Returns status ok | partial | gap."""
    cfg = load_config()
    try:
        return _collect(cfg, tracker)
    except Exception as e:
        log.exception("[ads] unexpected error")
        return _gap(cfg, f"unexpected error: {type(e).__name__}: {str(e)[:200]}")


def ad_library_link(archive_id: str) -> str:
    return f"https://www.facebook.com/ads/library/?id={archive_id}"


# ---------------- flow ----------------

def _collect(cfg: dict, tracker: Any) -> SourceResult:
    mode = fixtures_mode()
    try:
        if simulate_fail() == "ads":
            raise PermanentError("SIMULATE_FAIL=ads (simulated Apify outage)")
        if mode:
            items = load_fixture(ITEMS_FIXTURE)
            run = _safe_fixture(RUN_FIXTURE)
            source = f"fixture ({mode})"
        else:
            if tracker is not None:
                tracker.mark_apify_attempted()
            run, items = with_retry(lambda: _apify_run(cfg), "ads:apify")
            missing = _missing(items, cfg)
            if missing:
                log.info(f"[ads] no ads for {[c['name'] for c in missing]}; retrying those once")
                try:
                    run2, items2 = _apify_run(cfg, missing)
                    items = items + items2
                    run = {**run, "usageTotalUsd": (run.get("usageTotalUsd") or 0) + (run2.get("usageTotalUsd") or 0)}
                except (PermanentError, TransientError) as e:
                    log.warning(f"[ads] retry for missing competitors failed: {e}")
            save_fixture(ITEMS_FIXTURE, items)
            save_fixture(RUN_FIXTURE, _run_cost_fields(run))
            source = "apify"
        if tracker is not None:
            tracker.set_apify(run_cost_fields=_run_cost_fields(run), items=len(items),
                              from_fixture=bool(mode))
        ads, img_failures = _normalise(items, cfg, from_fixture=bool(mode))
        return _ok_result(cfg, ads, source, img_failures, synthetic=any(i.get("_synthetic") for i in items))
    except (PermanentError, TransientError, KeyError, ValueError, TypeError) as e:
        primary_error = str(e)
        log.warning(f"[ads] primary source failed: {primary_error}")

    manual = _from_manual_csv(cfg, primary_error)
    if manual:
        return manual
    return _gap(cfg, f"Apify failed ({primary_error}); no manual_inputs/ads.csv")


def _safe_fixture(name: str) -> dict:
    try:
        return load_fixture(name)
    except PermanentError:
        return {}


# ---------------- Apify REST ----------------

def _headers() -> dict:
    token = os.getenv("APIFY_TOKEN")
    if not token:
        raise PermanentError("APIFY_TOKEN not set")
    return {"Authorization": f"Bearer {token}"}


def _missing(items: list[dict], cfg: dict) -> list[dict]:
    """Competitor entries with no active official-page ad in this batch."""
    found = {_competitor_for(i, cfg) for i in items
             if i.get("isActive") is not False and _official_page(i, i.get("snapshot") or {}, cfg)}
    names = {c["name"] for c in cfg["ads"]["competitors"]} - found
    return [c for c in cfg["ads"]["competitors"] if c["name"] in names]


def _apify_run(cfg: dict, competitors: list[dict] | None = None) -> tuple[dict, list[dict]]:
    acfg = cfg["ads"]
    competitors = competitors or acfg["competitors"]
    n_urls = len(competitors)
    run_input = {
        "startUrls": [{"url": c["ad_library_url"]} for c in competitors],
        "resultsLimit": int(acfg["results_per_competitor"]),   # per URL
        "activeStatus": "active",
        "isDetailsPerAd": False,
        "onlyTotal": False,
        "includeAboutPage": False,
    }
    params = {"waitForFinish": 60,
              "maxItems": int(acfg["results_per_competitor"]) * n_urls,      # caps charged results
              "maxTotalChargeUsd": acfg["max_total_charge_usd"]}             # hard spend cap
    resp = requests.post(f"{API}/acts/{acfg['actor_id']}/runs", headers=_headers(),
                         params=params, json=run_input, timeout=90)
    check_response(resp, "Apify")
    run = resp.json()["data"]
    log.info(f"[ads] Apify run started ({run.get('id')}), waiting…")
    run = _wait_for_run(run, int(acfg["run_timeout_seconds"]))
    status = run.get("status")
    if status in {"FAILED", "TIMED-OUT"}:
        raise TransientError(f"Apify run {status} (Meta page may have changed or run hit its limit)")
    if status != "SUCCEEDED":
        raise PermanentError(f"Apify run ended with status {status}")
    items = requests.get(f"{API}/datasets/{run['defaultDatasetId']}/items",
                         headers=_headers(), params={"clean": "true", "format": "json"}, timeout=90)
    check_response(items, "Apify dataset")
    return run, items.json()


def _wait_for_run(run: dict, timeout_s: int) -> dict:
    deadline = time.monotonic() + timeout_s
    while run.get("status") not in TERMINAL:
        if time.monotonic() > deadline:
            raise TransientError(f"Apify run still {run.get('status')} after {timeout_s}s")
        r = requests.get(f"{API}/actor-runs/{run['id']}", headers=_headers(),
                         params={"waitForFinish": 60}, timeout=90)
        check_response(r, "Apify")
        run = r.json()["data"]
    return run


def _run_cost_fields(run: dict) -> dict:
    """Only the cost-related, non-secret fields of the run object."""
    pricing = run.get("pricingInfo") or {}
    return {"id": run.get("id"), "status": run.get("status"),
            "usageTotalUsd": run.get("usageTotalUsd"),
            "pricingModel": pricing.get("pricingModel"),
            "chargedEventCounts": run.get("chargedEventCounts")}


# ---------------- normalising ----------------

def _start_date(item: dict) -> str:
    if item.get("startDateFormatted"):
        return str(item["startDateFormatted"])[:10]
    if item.get("startDate"):
        return datetime.fromtimestamp(int(item["startDate"]), tz=timezone.utc).strftime("%Y-%m-%d")
    return ""


def _ad_text(snap: dict) -> str:
    """Body text; skips dynamic-catalog placeholders like '{{product.brand}}'."""
    candidates = [(snap.get("body") or {}).get("text"), snap.get("title"), snap.get("linkDescription")]
    for card in snap.get("cards") or []:
        candidates += [card.get("body"), card.get("title")]
    for c in candidates:
        if c and "{{" not in c:
            return " ".join(str(c).split())
    return ""


def _image_url(snap: dict) -> str | None:
    for img in snap.get("images") or []:
        u = img.get("originalImageUrl") or img.get("resizedImageUrl")
        if u:
            return u
    for card in snap.get("cards") or []:
        u = card.get("originalImageUrl") or card.get("resizedImageUrl") or card.get("videoPreviewImageUrl")
        if u:
            return u
    for vid in snap.get("videos") or []:
        if vid.get("videoPreviewImageUrl"):
            return vid["videoPreviewImageUrl"]
    return None


def _competitor_for(item: dict, cfg: dict) -> str:
    url = item.get("inputUrl") or item.get("url") or ""
    for c in cfg["ads"]["competitors"]:
        if url and url == c["ad_library_url"]:
            return c["name"]
    text = (url + " " + (item.get("pageName") or "")).lower()
    for c in cfg["ads"]["competitors"]:
        if c["name"].lower() in text:
            return c["name"]
    return "unknown"


def _download_image(url: str | None, archive_id: str, from_fixture: bool) -> str | None:
    """Saves to output/images/<id>.jpg (and a copy in fixtures/images/ on real runs). Returns relative path."""
    IMAGES_DIR.mkdir(parents=True, exist_ok=True)
    dest = IMAGES_DIR / f"{archive_id}.jpg"
    if from_fixture:
        src = fixtures_dir() / "images" / f"{archive_id}.jpg"
        if src.exists():
            shutil.copyfile(src, dest)
            return str(dest.relative_to(ROOT))
        return None
    if not url:
        return None
    try:

        r = requests.get(url, timeout=30, headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                          "(KHTML, like Gecko) Chrome/126.0 Safari/537.36",
            "Referer": "https://www.facebook.com/"})
        if not r.ok or not r.headers.get("content-type", "").startswith("image"):
            return None
        dest.write_bytes(r.content)
        (REAL_FIXTURES_DIR / "images").mkdir(parents=True, exist_ok=True)
        shutil.copyfile(dest, REAL_FIXTURES_DIR / "images" / dest.name)
        return str(dest.relative_to(ROOT))
    except requests.RequestException:
        return None

def _official_page(item: dict, snap: dict, cfg: dict) -> bool:
    """Keyword searches also return resellers/unrelated pages. Keep only ads from a competitor's official pages
    (config `pages`, case-insensitive substring). Competitors without `pages` are not filtered."""
    comp = _competitor_for(item, cfg)
    pages = next((c.get("pages") for c in cfg["ads"]["competitors"] if c["name"] == comp), None)
    if not pages:
        return True
    name = (item.get("pageName") or snap.get("pageName") or "").lower()
    if any(p.lower() in name for p in pages):
        return True
    log.info(f"[ads] skipped ad from unofficial page '{name}' (search: {comp})")
    return False
def _normalise(items: list[dict], cfg: dict, from_fixture: bool) -> tuple[list[dict], int]:
    seen: set[str] = set()
    ads: list[dict] = []
    img_failures = 0
    for item in items:
        archive_id = str(item.get("adArchiveID") or item.get("adArchiveId") or "")
        if not archive_id or archive_id in seen or item.get("isActive") is False:
            continue
        seen.add(archive_id)
        snap = item.get("snapshot") or {}
        if not _official_page(item, snap, cfg):
            continue
        img = _download_image(_image_url(snap), archive_id, from_fixture)
        img_failures += img is None
        ads.append({
            "id": f"A{len(ads) + 1}",
            "ad_archive_id": archive_id,
            "competitor": _competitor_for(item, cfg),
            "page_name": item.get("pageName") or snap.get("pageName") or "",
            "start_date": _start_date(item),
            "ad_text": _ad_text(snap)[: cfg["ads"]["ad_text_max_chars"]],
            "platforms": item.get("publisherPlatform") or [],
            "image_path": img,
            "ad_library_link": ad_library_link(archive_id),
        })
    return ads, img_failures


# ---------------- results ----------------

def _filters(cfg: dict) -> dict:
    return {"country": cfg["geo"], "active_status": "active", "ad_type": "all",
            "competitors": [c["name"] for c in cfg["ads"]["competitors"]],
            "results_per_competitor": cfg["ads"]["results_per_competitor"]}


def _ok_result(cfg: dict, ads: list[dict], source: str, img_failures: int, synthetic: bool) -> SourceResult:
    by_comp = {c["name"]: sum(a["competitor"] == c["name"] for a in ads) for c in cfg["ads"]["competitors"]}
    reasons = []
    if not ads:
        reasons.append("no active ads found for the competitor searches")
    missing = [k for k, v in by_comp.items() if v == 0]
    if ads and missing:
        reasons.append(f"no active ads found for: {', '.join(missing)}")
    if img_failures:
        reasons.append(f"{img_failures} ad image(s) could not be downloaded")
    status = "ok" if not img_failures else "partial"
    log.info(f"[ads] {status.upper()} via {source}, {len(ads)} active ads {by_comp}")
    return SourceResult(status=status, reason="; ".join(reasons) or None, source=source,
                        synthetic=synthetic, collected_at=now_cairo(), filters=_filters(cfg),
                        period="Active at collection time",
                        data={"ads": ads, "by_competitor": by_comp,
                              "search_url": cfg["ads"]["fallback_search_url"]})


def _from_manual_csv(cfg: dict, primary_error: str) -> SourceResult | None:
    """manual_inputs/ads.csv columns: competitor, page_name, ad_archive_id, start_date, ad_text,
    image_path (optional, relative to repo root), ad_library_link (optional)."""
    rows = read_manual_csv(MANUAL_DIR / "ads.csv")
    if not rows:
        return None
    ads = []
    for r in rows:
        aid = r.get("ad_archive_id", "")
        if not aid:
            continue
        img = r.get("image_path") or None
        ads.append({"id": f"A{len(ads) + 1}", "ad_archive_id": aid, "competitor": r.get("competitor", ""),
                    "page_name": r.get("page_name", ""), "start_date": r.get("start_date", ""),
                    "ad_text": r.get("ad_text", ""), "platforms": [],
                    "image_path": img if img and (ROOT / img).exists() else None,
                    "ad_library_link": r.get("ad_library_link") or ad_library_link(aid)})
    if not ads:
        return None
    log.info(f"[ads] PARTIAL via manual CSV, {len(ads)} ads")
    return SourceResult(status="partial", reason=f"Apify failed ({primary_error}); used manual_inputs/ads.csv",
                        source="manual_csv", collected_at=now_cairo(), filters=_filters(cfg),
                        period="Active when manually checked",
                        data={"ads": ads, "search_url": cfg["ads"]["fallback_search_url"]})


def _gap(cfg: dict, reason: str) -> SourceResult:
    log.warning(f"[ads] GAP: {reason}")
    return SourceResult(status="gap", reason=reason, collected_at=now_cairo(), filters=_filters(cfg),
                        period="Active at collection time",
                        data={"ads": [], "search_url": cfg["ads"]["fallback_search_url"]})
