"""Flask app: tracking-tag coverage report."""

import os
from datetime import date, datetime, timedelta

from flask import Flask, render_template, request, jsonify

import capi

app = Flask(__name__)
app.config["TEMPLATES_AUTO_RELOAD"] = True


def _parse_date(value, default):
    """Accept UK (dd/mm/yyyy) or ISO (yyyy-mm-dd) input; return a date."""
    if value:
        for fmt in ("%d/%m/%Y", "%Y-%m-%d"):
            try:
                return datetime.strptime(value.strip(), fmt).date()
            except ValueError:
                continue
    return default


def _date_range():
    """Read from/to from query params (UK or ISO), return ISO strings."""
    today = date.today()
    from_date = _parse_date(request.args.get("from"), today - timedelta(days=30))
    to_date = _parse_date(request.args.get("to"), today)
    return from_date.isoformat(), to_date.isoformat()


@app.template_filter("ukdate")
def ukdate(iso):
    """Format an ISO date string (yyyy-mm-dd) as UK dd/mm/yyyy for display."""
    try:
        return datetime.strptime(iso, "%Y-%m-%d").strftime("%d/%m/%Y")
    except (ValueError, TypeError):
        return iso


@app.route("/")
def index():
    from_date, to_date = _date_range()
    data = capi.compute_coverage(from_date, to_date)
    return render_template("index.html", data=data, categories=capi.CATEGORIES)


@app.route("/api/coverage")
def api_coverage():
    from_date, to_date = _date_range()
    return jsonify(capi.compute_coverage(from_date, to_date))


def _desk_params():
    office = request.args.get("office") or None
    if office not in capi.PRODUCTION_OFFICES:
        office = None
    try:
        min_total = max(1, int(request.args.get("min_total", 5)))
    except ValueError:
        min_total = 5
    return office, min_total


@app.route("/desks")
def desks():
    from_date, to_date = _date_range()
    office, min_total = _desk_params()
    # Only crawl when the user explicitly runs it (the form sets run=1);
    # navigating to the page just shows the filters.
    data = None
    if request.args.get("run"):
        data = capi.desk_adoption(from_date, to_date, office, min_total)
    return render_template("desks.html", data=data,
                           offices=capi.PRODUCTION_OFFICES,
                           from_date=from_date, to_date=to_date,
                           office=office, min_total=min_total)


@app.route("/api/desks")
def api_desks():
    from_date, to_date = _date_range()
    office, min_total = _desk_params()
    return jsonify(capi.desk_adoption(from_date, to_date, office, min_total))


def _trend_params():
    office = request.args.get("office") or None
    if office not in capi.PRODUCTION_OFFICES:
        office = None
    try:
        min_total = max(1, int(request.args.get("min_total", 30)))
    except ValueError:
        min_total = 30
    try:
        top_n = min(20, max(1, int(request.args.get("top_n", 8))))
    except ValueError:
        top_n = 8
    return office, min_total, top_n


@app.route("/trend")
def trend():
    from_date, to_date = _date_range()
    office, min_total, top_n = _trend_params()
    # Crawl only when the user runs it (the form sets run=1).
    data = None
    if request.args.get("run"):
        data = capi.trend_adoption(from_date, to_date, office, min_total, top_n)
    return render_template("trend.html", data=data,
                           offices=capi.PRODUCTION_OFFICES,
                           from_date=from_date, to_date=to_date,
                           office=office, min_total=min_total, top_n=top_n)


@app.route("/api/trend")
def api_trend():
    from_date, to_date = _date_range()
    office, min_total, top_n = _trend_params()
    return jsonify(capi.trend_adoption(from_date, to_date, office, min_total, top_n))


if __name__ == "__main__":
    # Local development entrypoint. In production, Render runs the app through
    # gunicorn (see render.yaml), not this block.
    port = int(os.environ.get("PORT", 5000))
    app.run(debug=bool(os.environ.get("FLASK_DEBUG")), host="0.0.0.0", port=port)
