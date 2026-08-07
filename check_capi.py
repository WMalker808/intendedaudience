"""Spike: verify CAPI assumptions behind the coverage dashboard.

Run once after setting your key in the environment:
    export GUARDIAN_API_KEY=your-key-here
    python check_capi.py

Confirms:
  1. The four tracking tags exist and return counts.
  2. CAPI tag operators behave as assumed: "," = AND, "|" = OR
     (inclusion-exclusion R + G - AND == OR must hold).
  3. Which production-office codes actually return content.
  4. The four editorial states partition each office (sum == office total).
"""

import capi

WINDOW = {"from-date": "2026-06-17", "to-date": "2026-07-17"}


def main():
    # capi raises at import if GUARDIAN_API_KEY is unset, so no guard needed here.
    print("== Individual tag counts ==")
    for label, tag_id in capi.TRACKING_TAGS.items():
        n = capi._total({**WINDOW, "tag": tag_id})
        print(f"  {label:7} {tag_id:26} {n:>8,}")

    print("\n== Tag operators (uk vs global) ==")
    uk, gl = capi.TRACKING_TAGS["uk"], capi.GLOBAL_TAG
    R = capi._total({**WINDOW, "tag": uk})
    G = capi._total({**WINDOW, "tag": gl})
    AND = capi._total({**WINDOW, "tag": uk + "," + gl})
    OR = capi._total({**WINDOW, "tag": uk + "|" + gl})
    print(f"  R(uk)={R:,}  G(global)={G:,}  AND={AND:,}  OR={OR:,}")
    print("  inclusion-exclusion R+G-AND == OR:",
          "OK" if R + G - AND == OR else "!! operators unexpected")

    print("\n== Production-office codes ==")
    for code in ["uk", "us", "aus", "int", "au"]:
        n = capi._total({**WINDOW, "production-office": code})
        flag = "" if n else "  (no content -- drop this code)"
        print(f"  {code:5} {n:>8,}{flag}")

    print("\n== Four-state partition per office ==")
    d = capi.compute_coverage(WINDOW["from-date"], WINDOW["to-date"])
    for row in d["offices"]:
        s = sum(row["cats"][c]["count"] for c in capi.CATEGORIES)
        ok = "OK" if s == row["total"] else f"!! sum {s} != total {row['total']}"
        print(f"  {row['office']:5} total={row['total']:,}  states sum={s:,}  {ok}")


if __name__ == "__main__":
    main()
