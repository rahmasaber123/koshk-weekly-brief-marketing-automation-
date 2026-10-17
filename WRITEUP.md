# Koshk Weekly Brief: how it works (draft write-up)

> Fill the `[FILL]` values from `output/run_log.json` → `cost` and from `python cost.py` after your first real runs.

**How it works.** A GitHub Actions cron job runs every Monday at 06:00 UTC (09:00 Cairo). It can also be started by hand. Plain Python collectors gather the facts:
- **Ads:** active ads in Egypt for Chipsy, Doritos and Lion from the Meta Ad Library, via the Apify actor `apify/facebook-ads-scraper`. Images are downloaded at collection time.
- **Trends:** Google Trends for Egypt via SerpApi. Five Arabic terms over the past 90 days, plus rising related queries. Week-over-week change is computed in code.

Three PydanticAI agents (gpt-4o-mini, temperature 0.2, typed outputs) then add judgement only: which ads matter, which trend movements matter, and three content ideas. `validate.py` checks every agent output against the raw data and drops any invented ID, number or unsupported idea, logging each drop. The brief is rendered to a one-page HTML and PDF, emailed via Gmail, and saved as a GitHub artifact. Facts and interpretation are visually separated ("So what:"), and a one-line footer lists sources, filters and periods. Source status and cost stay in `run_log.json`, out of the CEO's view.

**What remains manual.** Choosing competitors and search terms; checking prices in `config.yaml`; reviewing the ideas before posting (audience and timing are suggestions); filling `manual_inputs/*.csv` if a source is down for a long time.

**Monthly cost (actual).** Average per run: $[FILL] over [FILL] runs, so about $[FILL]/month at 4.33 runs. Per run, from `run_log.json`:
- Apify: $[FILL] for [FILL] ads (free plan, $5.80 per 1,000 ads; plan credit covers it)
- SerpApi: [FILL] searches, $0 within the free plan, [FILL] left
- OpenAI: [FILL]k tokens, $[FILL]

Assumptions: 20 ads per brand, 3 SerpApi searches per run, weekly cadence. During development, raw API responses were saved to `fixtures/` and reused (`USE_FIXTURES=1`), so building and testing the agents, chart and template didn't repeat API costs.

**What could break and how it's handled.**
- Google Trends occasionally returns 0 for a single day (sampling gap); the chart shows raw data as-is.
- **Meta's official Ad Library API** only returns commercial ads for the EU, so a scraper is used. If the scraper fails, Apify runs out of credit, or Meta changes its page: retry 2× → `manual_inputs/ads.csv` → the ads section is marked as a gap with the reason and a ready Ad Library link (Egypt, active).
- **No relevant ads:** the brief says so and never pads to three.
- **SerpApi down or quota used up:** retry → manual CSV → gap. Thin terms are greyed out as "insufficient data".
- **LLM down or invalid output:** retry once. Facts still render from code (ad text, week-over-week numbers), and the ideas section is marked unavailable.
- **Invented IDs or numbers:** dropped by `validate.py` and logged.
- **Email failure:** the brief is still saved to `output/` and the artifact.
- **Missing cost fields:** logged as "unknown", never a crash.
- **Schedule delays:** GitHub can start cron runs late at busy times. The brief shows the actual collection time.
- **Inactivity pause:** GitHub pauses schedules after 60 days without repo activity. The weekly cost-history commit keeps the repo active.
