"""Guardian Content API client + tracking-tag coverage computation.

All figures come from the /search endpoint's `response.total` with page-size=1,
so we never crawl article lists -- one lightweight count call per figure.
"""

import os
import time
from collections import defaultdict
from datetime import date, timedelta

import requests

# --- Configuration ----------------------------------------------------------

# Guardian developer API key, read from the environment. Set GUARDIAN_API_KEY
# in your shell for local dev, or as an environment variable on Render.
GUARDIAN_API_KEY = os.environ.get("GUARDIAN_API_KEY", "")

if not GUARDIAN_API_KEY:
    raise RuntimeError(
        "GUARDIAN_API_KEY is not set. Export it locally "
        "(export GUARDIAN_API_KEY=your-key) or add it as an environment "
        "variable in the Render dashboard."
    )

BASE_URL = "https://content.guardianapis.com/search"

# The four tracking tags we're measuring, keyed by short label.
TRACKING_TAGS = {
    "au": "tracking/audience/au",
    "us": "tracking/audience/us",
    "uk": "tracking/audience/uk",
    "global": "tracking/audience/global",
}

# Production offices to break down by (CAPI `production-office` filter values),
# each mapped to the regional audience tag it uses. Confirmed by check_capi.py.
OFFICE_REGION = {"uk": "uk", "us": "us", "aus": "au"}
PRODUCTION_OFFICES = list(OFFICE_REGION)

GLOBAL_TAG = TRACKING_TAGS["global"]

# CAPI tag operators (validated live): "," = AND, "|" = OR.
# The editorial model gives four mutually-exclusive states per office:
#   for_region  = regional tag, NOT global   (e.g. "uk for uk")
#   for_global  = regional tag AND global     (e.g. "uk for global")
#   just_global = global only, no regional    ("just global")
#   none        = no tracking tag
# These sum to the office's total content.

# Commissioning desks live under this tracking namespace, e.g.
# tracking/commissioningdesk/uk-opinion.
DESK_PREFIX = "tracking/commissioningdesk/"
AUDIENCE_TAG_IDS = set(TRACKING_TAGS.values())

# Simple in-memory TTL cache so page reloads don't re-hit CAPI.
_CACHE_TTL_SECONDS = 300
_cache = {}
_desk_names_cache = None


# --- Low-level count helper -------------------------------------------------

def _total(params):
    """Return response.total for a /search query (page-size=1)."""
    query = {
        "api-key": GUARDIAN_API_KEY,
        "page-size": 1,
        "format": "json",
    }
    query.update(params)
    resp = requests.get(BASE_URL, params=query, timeout=20)
    resp.raise_for_status()
    return resp.json()["response"]["total"]


def _pct(numerator, denominator):
    return round(100 * numerator / denominator, 1) if denominator else 0.0


# --- Coverage computation ---------------------------------------------------

# The four mutually-exclusive states, in display order.
CATEGORIES = ["for_region", "for_global", "just_global", "none"]


def _office_breakdown(office, window):
    """Return the four-category breakdown for one production office."""
    region_tag = TRACKING_TAGS[OFFICE_REGION[office]]
    base = {**window, "production-office": office}

    total = _total(base)
    regional = _total({**base, "tag": region_tag})            # regional (± global)
    glob = _total({**base, "tag": GLOBAL_TAG})                # global (± regional)
    both = _total({**base, "tag": region_tag + "," + GLOBAL_TAG})  # AND

    counts = {
        "for_region": regional - both,       # regional only
        "for_global": both,                  # regional + global
        "just_global": glob - both,          # global only
    }
    counts["none"] = max(total - sum(counts.values()), 0)
    # "any" = aggregate of the three tagged states (i.e. total minus none).
    counts["any"] = total - counts["none"]

    return {
        "office": office,
        "region": OFFICE_REGION[office],
        "total": total,
        "cats": {
            c: {"count": counts[c], "pct": _pct(counts[c], total)}
            for c in CATEGORIES + ["any"]
        },
    }


def compute_coverage(from_date, to_date):
    """Coverage by production office, split into the four editorial states.

    Returns:
      {
        "from", "to",
        "overall": {"total", "cats": {cat: {"count","pct"}}},
        "offices": [ {"office","region","total",
                      "cats": {cat: {"count","pct"}}}, ... ],
      }
    Offices partition all content, so the overall row is the sum of the offices.
    """
    cache_key = (from_date, to_date)
    cached = _cache.get(cache_key)
    if cached and time.time() - cached[0] < _CACHE_TTL_SECONDS:
        return cached[1]

    window = {"from-date": from_date, "to-date": to_date}

    offices = [_office_breakdown(o, window) for o in PRODUCTION_OFFICES]

    overall_total = sum(o["total"] for o in offices)
    overall_cats = {}
    for c in CATEGORIES + ["any"]:
        count = sum(o["cats"][c]["count"] for o in offices)
        overall_cats[c] = {"count": count, "pct": _pct(count, overall_total)}

    result = {
        "from": from_date,
        "to": to_date,
        "overall": {"total": overall_total, "cats": overall_cats},
        "offices": offices,
    }

    _cache[cache_key] = (time.time(), result)
    return result


# --- Commissioning-desk adoption --------------------------------------------

def _desk_names():
    """Map commissioning-desk tag id -> human webTitle (cached)."""
    global _desk_names_cache
    if _desk_names_cache is not None:
        return _desk_names_cache
    names, page = {}, 1
    while True:
        resp = requests.get(
            "https://content.guardianapis.com/tags",
            params={"api-key": GUARDIAN_API_KEY, "q": "commissioningdesk",
                    "page-size": 50, "page": page},
            timeout=20,
        )
        resp.raise_for_status()
        data = resp.json()["response"]
        for t in data["results"]:
            if t["id"].startswith(DESK_PREFIX):
                names[t["id"]] = t.get("webTitle") or t["id"][len(DESK_PREFIX):]
        if page >= data["pages"]:
            break
        page += 1
    _desk_names_cache = names
    return names


def desk_adoption(from_date, to_date, office=None, min_total=5):
    """Per commissioning-desk tag-adoption, to surface teams not using the tag.

    Crawls the window's content once (optionally scoped to a production office)
    and tallies, per desk, total articles vs. those carrying any audience tag.
    Returns rows sorted by adoption ascending (lowest adopters first).

    An article may belong to more than one desk; it counts toward each.
    """
    cache_key = ("desks", from_date, to_date, office, min_total)
    cached = _cache.get(cache_key)
    if cached and time.time() - cached[0] < _CACHE_TTL_SECONDS:
        return cached[1]

    params = {
        "api-key": GUARDIAN_API_KEY,
        "from-date": from_date,
        "to-date": to_date,
        "page-size": 200,
        "show-tags": "tracking",
        "order-by": "newest",
    }
    if office:
        params["production-office"] = office

    tally = {}          # desk_id -> [total, tagged]
    content_total = 0
    page = 1
    while page <= 60:    # safety cap (~12k articles)
        params["page"] = page
        resp = requests.get(BASE_URL, params=params, timeout=30)
        resp.raise_for_status()
        data = resp.json()["response"]
        for art in data["results"]:
            content_total += 1
            tag_ids = [t["id"] for t in art.get("tags", [])]
            has_aud = any(t in AUDIENCE_TAG_IDS for t in tag_ids)
            for tid in tag_ids:
                if tid.startswith(DESK_PREFIX):
                    row = tally.setdefault(tid, [0, 0])
                    row[0] += 1
                    if has_aud:
                        row[1] += 1
        if page >= data["pages"]:
            break
        page += 1

    names = _desk_names()
    rows = []
    for desk_id, (total, tagged) in tally.items():
        if total < min_total:
            continue
        rows.append({
            "id": desk_id,
            "desk": names.get(desk_id, desk_id[len(DESK_PREFIX):]),
            "total": total,
            "tagged": tagged,
            "untagged": total - tagged,
            "adoption_pct": _pct(tagged, total),
        })
    # Lowest adopters first, breaking ties by most untagged articles.
    rows.sort(key=lambda r: (r["adoption_pct"], -r["untagged"]))

    result = {
        "from": from_date,
        "to": to_date,
        "office": office,
        "min_total": min_total,
        "content_total": content_total,
        "desks": rows,
    }
    _cache[cache_key] = (time.time(), result)
    return result


# --- Adoption trend (rolling average) ---------------------------------------

def trend_adoption(from_date, to_date, office=None, min_total=30,
                   top_n=8, window=7):
    """Rolling-average tag adoption over time, globally and per desk.

    Crawls the display window plus `window-1` lead-in days once, buckets each
    article by publication date, and reports, for each display day, the
    trailing `window`-day pooled adoption rate (tagged / total over the window)
    -- globally and for the `top_n` highest-volume desks (>= min_total articles).

    Returns:
      {"from","to","office","min_total","top_n","window","content_total",
       "days": [iso, ...],
       "global": [pct|null, ...],
       "desks": [{"id","desk","total","series": [pct|null, ...]}, ...]}
    """
    cache_key = ("trend", from_date, to_date, office, min_total, top_n, window)
    cached = _cache.get(cache_key)
    if cached and time.time() - cached[0] < _CACHE_TTL_SECONDS:
        return cached[1]

    start = date.fromisoformat(from_date)
    end = date.fromisoformat(to_date)
    crawl_start = start - timedelta(days=window - 1)

    params = {
        "api-key": GUARDIAN_API_KEY,
        "from-date": crawl_start.isoformat(),
        "to-date": to_date,
        "page-size": 200,
        "show-tags": "tracking",
        "order-by": "newest",
    }
    if office:
        params["production-office"] = office

    day_total, day_tagged = defaultdict(int), defaultdict(int)
    desk_total = defaultdict(lambda: defaultdict(int))   # desk -> day -> count
    desk_tagged = defaultdict(lambda: defaultdict(int))
    desk_volume = defaultdict(int)
    content_total = 0

    page = 1
    while page <= 80:
        params["page"] = page
        resp = requests.get(BASE_URL, params=params, timeout=30)
        resp.raise_for_status()
        data = resp.json()["response"]
        for art in data["results"]:
            content_total += 1
            day = art["webPublicationDate"][:10]
            tag_ids = [t["id"] for t in art.get("tags", [])]
            has_aud = any(t in AUDIENCE_TAG_IDS for t in tag_ids)
            day_total[day] += 1
            if has_aud:
                day_tagged[day] += 1
            for tid in tag_ids:
                if tid.startswith(DESK_PREFIX):
                    desk_total[tid][day] += 1
                    desk_volume[tid] += 1
                    if has_aud:
                        desk_tagged[tid][day] += 1
        if page >= data["pages"]:
            break
        page += 1

    display_days = [start + timedelta(days=i)
                    for i in range((end - start).days + 1)]

    def rolling(tot_map, tag_map):
        series = []
        for d in display_days:
            keys = [(d - timedelta(days=k)).isoformat() for k in range(window)]
            t = sum(tot_map[k] for k in keys)
            g = sum(tag_map[k] for k in keys)
            series.append(round(100 * g / t, 1) if t else None)
        return series

    global_series = rolling(day_total, day_tagged)

    names = _desk_names()
    desks = []
    for desk_id, vol in sorted(desk_volume.items(), key=lambda kv: -kv[1]):
        if vol < min_total:
            continue
        desks.append({
            "id": desk_id,
            "desk": names.get(desk_id, desk_id[len(DESK_PREFIX):]),
            "total": vol,
            "series": rolling(desk_total[desk_id], desk_tagged[desk_id]),
        })
        if len(desks) >= top_n:
            break

    result = {
        "from": from_date,
        "to": to_date,
        "office": office,
        "min_total": min_total,
        "top_n": top_n,
        "window": window,
        "content_total": content_total,
        "days": [d.isoformat() for d in display_days],
        "global": global_series,
        "desks": desks,
    }
    _cache[cache_key] = (time.time(), result)
    return result
