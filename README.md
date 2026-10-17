# Koshk Weekly Brief

Every Monday at 09:00 Cairo this produces a one-page brief for Koshk, a fictional Egyptian snack brand. The brief covers what Chipsy, Doritos and Lion are running in the Meta Ad Library (Egypt, active), what Egypt is searching on Google Trends (Arabic terms), and three content ideas, each tied to a specific finding. The brief is saved to `output/` (HTML + PDF), emailed, and uploaded as a GitHub Actions artifact.

**Code collects the facts. AI agents only add judgement. Every AI output is checked against the raw data.**

```
GitHub Actions cron (Mon 06:00 UTC) / manual run
  → collectors/   ads.py (Apify → manual CSV → gap)   trends.py (SerpApi → manual CSV → gap)
  → agents/       Ads Analyst · Trends Analyst · Content Strategist   (PydanticAI, gpt-4o-mini, temp 0.2)
  → validate.py   drops any ID / number / idea not backed by the raw data, logs every drop
  → charts.py → render.py (HTML + one-page PDF) → cost.py → deliver.py (Gmail) → output/run_log.json
```

## Setup (about 15 minutes)

1. **Accounts (free tiers).** You need an [Apify](https://apify.com) API token, a [SerpApi](https://serpapi.com) API key, an OpenAI API key, and a Gmail [App Password](https://myaccount.google.com/apppasswords) (2-step verification must be on).
2. **Local run**
   ```bash
   python3.12 -m venv .venv && source .venv/bin/activate
   pip install -r requirements.txt          # Linux also needs: libpango-1.0-0 libpangoft2-1.0-0 libharfbuzz-subset0 (WeasyPrint); macOS: brew install pango
   cp .env.example .env                     # fill in the keys
   ```
   Fonts are bundled in `assets/fonts/` (IBM Plex Sans Arabic, IBM Plex Mono, Bricolage Grotesque; OFL licence), so Arabic renders the same on any machine.
3. **GitHub.** Push the repo. Under *Settings → Secrets and variables → Actions*, add `APIFY_TOKEN`, `SERPAPI_KEY`, `OPENAI_API_KEY`, `SMTP_USER`, `SMTP_PASSWORD` and `BRIEF_RECIPIENTS`. Then run it once from *Actions → Koshk weekly brief → Run workflow*.
4. **Optional:** put your agency logo at `assets/logo.png`. The header hides the slot if the file is missing.

## How to run

| What | Command | API cost |
|---|---|---|
| Full real run | `python main.py` | Apify ~$0.09 at 5 ads/brand, 3 SerpApi searches, <$0.01 OpenAI |
| Re-run on saved real data | `USE_FIXTURES=1 python main.py` | OpenAI only |
| Synthetic sample (offline demo) | `USE_FIXTURES=sample python main.py` | none (AI section shows "unavailable" without a key) |
| Failure demos | `SIMULATE_FAIL=ads` / `trends` / `llm` + any of the above | — |

Step-by-step checks (each prints what it got):

```bash
python -m tests.test_trends              # step 2: real Egypt Trends data (≈3 SerpApi searches)
python -m tests.test_ads                 # step 3: real active ads + images (small cap)
USE_FIXTURES=1 python -m tests.test_agents   # step 4: real agents on saved data
python -m tests.test_validate            # step 4: injects a fake ID/number → shows them dropped (offline)
python cost.py                           # average cost per run + monthly projection from cost_history.csv
```

Each real call saves its raw response to `fixtures/`, so later runs with `USE_FIXTURES=1` cost nothing. `tests/sample_fixtures/` holds **synthetic** data (`python -m tests.make_sample_fixtures`). Briefs built from it carry a "SAMPLE DATA · NOT REAL" badge.

Before the real weekly runs, set `ads.results_per_competitor: 20` in `config.yaml` (5 is the testing value).

## Outputs (`output/`)

- `brief.html` (dark theme, also the email body) and `brief.pdf` (print theme, always one A4 page; type and image sizes step down automatically if needed)
- `images/`: ad images downloaded at collection time (Facebook CDN links expire) and the chart
- `run_log.json`: source status, filters, periods, agent errors, every validation drop, cost breakdown, email result
- `cost_history.csv`: one row per run. In Actions, the workflow commits it back to the repo.

## What stays manual

- **Choosing competitors and keywords** (`config.yaml`). Ad Library URLs are exact-phrase keyword searches (Egypt, active), so the Ads Analyst skips unrelated advertisers. For more precision, swap in each brand's page ID (format shown in `config.yaml`).
- **Checking prices** in the `pricing:` block (last checked 2026-10-17) and the SerpApi plan quota. `output/run_log.json` shows remaining searches and the full cost breakdown (kept out of the CEO brief).
- **Manual fallbacks.** If a source is down, you can fill in `manual_inputs/*.csv` (see `manual_inputs/README.md`).
- **Reviewing the ideas.** Audience and timing are suggestions. Koshk is fictional, so the agents are told never to invent flavours, prices or promotions.
- **Keeping the schedule alive.** GitHub pauses scheduled workflows after 60 days without repo activity. The weekly cost-history commit counts as activity. If it's ever paused, re-enable it in the Actions tab.

## Notes

- Google Trends values are **relative interest (0–100), not search volumes**. The brief says so under the chart. Values like `<1` are kept as text, never as numbers. Week-over-week change is the mean of the last 7 complete days vs the 7 days before. A term with fewer than 4 days of interest ≥1 in either week is reported as *insufficient data* and greyed out.
- `pytrends` was left out as a fallback: it's unofficial, archived, and Google routinely blocks it with 429 errors.
- Apify's docs don't say whether a run's `usageTotalUsd` includes pay-per-result charges. `cost.py` logs both `usageTotalUsd` and items × price, and reports the higher one. After the first real run, compare it with your Apify billing page.
