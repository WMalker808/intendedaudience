# Tracking-tag coverage dashboard

A minimal Flask app that reports how much Guardian content carries the
`tracking/audience/*` tags (`au`, `us`, `uk`, `global`), overall and broken
down by production office.

## What it shows

- Headline: % of all content in the window that carries any tracking tag.
- Table: for each production office, its content split into four mutually
  exclusive editorial states (which sum to 100%):
  - **Domestic for domestic** — the office's own regional tag only (e.g. UK for `uk`).
  - **Domestic for global** — regional tag *and* `global` (e.g. UK for global).
  - **Global** — `global` only, no regional tag.
  - **No tag** — no tracking tag.

Offices map to regions: `uk`→`uk`, `us`→`us`, `aus`→`au`. An article can hold a
regional tag and `global` together, so these states are computed with CAPI's
tag operators (`,` = AND, `|` = OR), not by summing per-tag counts.

The office coverage figures come from the Guardian Content API `/search`
endpoint's `response.total` count (one lightweight call per figure, ~13 per
refresh). Results are cached in memory for 5 minutes; no database.

## Commissioning-desk adoption (`/desks`)

To find teams not applying the tag, `/desks` lists each commissioning desk
(`tracking/commissioningdesk/*`) ranked by adoption — the share of the desk's
articles carrying any audience tag — lowest first, with 0%-adoption desks
highlighted. Filter by production office and a minimum article count.

The crawl does **not** run on page load — the page shows the filters and only
crawls when you click **Run crawl**. It scans the window's content once
(~38 pages for a month, ~15s) and tallies per desk, which is far cheaper than
querying each of the 150+ desks separately. An article can belong to more than
one desk, so it counts toward each; desk totals therefore need not sum to total
content.

## Setup

1. Set your Guardian developer key as an environment variable:
   ```
   export GUARDIAN_API_KEY=your-key-here
   ```
2. Install deps:
   ```
   pip install -r requirements.txt
   ```
3. Verify CAPI assumptions (tag operators, office codes, state partition):
   ```
   python check_capi.py
   ```
   Adjust `OFFICE_REGION` in `capi.py` if the spike flags anything.
4. Run:
   ```
   python app.py
   ```
   Open http://localhost:5000 — use the date pickers to change the window
   (default: last 30 days).

## Deploying to Render

The repo ships a `render.yaml` blueprint, so deployment is:

1. Push this repo to GitHub/GitLab.
2. In Render, **New → Blueprint** and point it at the repo. Render reads
   `render.yaml` and creates a Python web service that runs the app under
   gunicorn.
3. When prompted, set the `GUARDIAN_API_KEY` environment variable (it is marked
   `sync: false`, so it is never stored in the repo — you enter it in the
   dashboard).

Notes:

- The service runs a **single** gunicorn worker on purpose: `capi.py`'s TTL
  cache lives in process memory, so extra workers would each hold a separate
  cache. Bump `--workers` in `render.yaml` only if you add a shared cache.
- The worker `--timeout` is raised to 120s because the `/desks` and `/trend`
  crawls can take 15s+.

## Files

- `capi.py` — CAPI client, coverage + desk-adoption computation, TTL cache.
- `app.py` — Flask routes (`/`, `/desks`, and JSON `/api/*`).
- `templates/index.html` — office coverage page.
- `templates/desks.html` — commissioning-desk adoption page.
- `check_capi.py` — one-off verification spike.

## Notes / v1 limits

- Snapshot only — no historical trend storage (that would be v2).
- Offices partition all content (`uk` + `us` + `aus` = total), so the "All
  content" row is the sum of the office rows.
- Validated on live data: ~27% of tagged articles carry both a regional tag and
  `global`, which is exactly the "for global" state.
