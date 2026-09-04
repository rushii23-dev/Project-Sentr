"""False positives, in rupees.

    python eval/cost_model.py --results eval/results/day5_final.json --split test

The track asks for "honest metrics including false-positive cost". A rate is not
a cost, so this converts one into the other, with every assumption named and
overridable on the command line.

THE MODEL (CLAUDE.md section 7)

    monthly cost = FPR_blocked x listings x conversion_rate x average_order_value

    FPR_blocked      measured, from the eval results file
    listings         assumption: catalogue size screened per month
    conversion_rate  assumption: orders per listing per month
    AOV              assumption: average order value in rupees

WHY ONLY *BLOCKED* FALSE POSITIVES COST MONEY
    Sentr has three verdicts, not two. A flagged listing is sanitised and still
    reaches the buyer, so it still sells; only a blocked listing earns nothing.
    Pricing flags as if they were blocks would overstate our own cost and
    understate the baseline's -- the baseline is binary, so every false positive
    it produces is a block. The report prices both ways and says which is which.

WHY THE INTERVAL MATTERS MORE THAN THE POINT ESTIMATE
    Zero false positives in 1,200 listings is not a 0% error rate. It is a rate
    whose 95% upper bound is around 0.31%, and on a large catalogue that upper
    bound is the number a merchant would actually budget against. Reporting
    0.00% alone would be the most flattering reading of a small sample, so every
    figure here carries a Wilson score interval and the cost is priced at the
    point estimate AND at the upper bound.

    The interval is about sampling, not about generalisation. It says nothing
    about listings unlike the ones we sampled.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RESULTS = ROOT / "eval" / "results"

# --------------------------------------------------------------- assumptions
# Stated, not measured. A judge should be able to disagree with any of these and
# recompute in one command rather than distrusting the whole number.
DEFAULTS = {
    "listings": 50_000,
    "conversion_rate": 0.02,
    "average_order_value_inr": 1_200.0,
}
ASSUMPTION_NOTES = {
    "listings": "catalogue listings screened per month; mid-size Indian merchant",
    "conversion_rate": "orders per listing per month, blended across the catalogue",
    "average_order_value_inr": "average order value; near the demo catalogue's own prices",
}


def wilson(k: int, n: int, z: float = 1.96) -> tuple[float, float]:
    """95% Wilson score interval for a proportion.

    Wilson rather than the normal approximation because the counts here are
    small and often zero, where the normal interval collapses to [0, 0] and
    tells a merchant something false.
    """
    if n == 0:
        return 0.0, 1.0
    p = k / n
    d = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / d
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return max(0.0, centre - half), min(1.0, centre + half)


def money(rate: float, a: dict) -> float:
    return rate * a["listings"] * a["conversion_rate"] * a["average_order_value_inr"]


def price(name: str, blocked: int, flagged_only: int, n_benign: int, a: dict) -> dict:
    """Cost of one detector's false positives, at the estimate and the bound."""
    lo, hi = wilson(blocked, n_benign)
    p = blocked / n_benign if n_benign else 0.0
    out = {
        "detector": name,
        "benign_listings_measured": n_benign,
        "blocked_false_positives": blocked,
        "flagged_but_still_selling": flagged_only,
        "blocked_fpr_pct": round(100 * p, 4),
        "blocked_fpr_95ci_pct": [round(100 * lo, 4), round(100 * hi, 4)],
        "monthly_cost_inr": round(money(p, a)),
        "monthly_cost_inr_95ci": [round(money(lo, a)), round(money(hi, a))],
        "annual_cost_inr": round(12 * money(p, a)),
        "annual_cost_inr_upper_bound": round(12 * money(hi, a)),
    }
    return out


def load_side(results: dict, split: str, key: str) -> dict | None:
    entry = results.get("splits", {}).get(split, {})
    return entry.get(key)


def extract(side: dict) -> tuple[int, int, int]:
    """(blocked FPs, flagged-only FPs, benign listings) from an eval block."""
    blocked = side["false_positives_blocked"]
    total = side["false_positives_total"]
    return blocked, total - blocked, side["benign"]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--results", default=str(RESULTS / "day4_rules_only.json"))
    ap.add_argument("--split", default="val")
    ap.add_argument("--listings", type=int, default=DEFAULTS["listings"])
    ap.add_argument("--conversion-rate", type=float, default=DEFAULTS["conversion_rate"])
    ap.add_argument("--aov", type=float, default=DEFAULTS["average_order_value_inr"])
    ap.add_argument("--out", default=str(RESULTS / "cost_model.json"))
    a = ap.parse_args()

    assumptions = {
        "listings": a.listings,
        "conversion_rate": a.conversion_rate,
        "average_order_value_inr": a.aov,
    }

    results = json.loads(Path(a.results).read_text(encoding="utf-8"))
    sentr = load_side(results, a.split, "sentr")
    base = load_side(results, a.split, "baseline")
    if sentr is None:
        raise SystemExit(f"no sentr block for split '{a.split}' in {a.results}")

    # Name the detector by the layers that actually ran, not by the layers the
    # architecture has. A cost attributed to a classifier that was switched off
    # would be a quiet misattribution.
    layers = (results.get("layers")
              or results.get("config", {}).get("layers")
              or "layers unrecorded")
    priced = [price(f"Sentr [{layers}]", *extract(sentr), assumptions)]
    if base:
        priced.append(price(f"Baseline ({base.get('model', 'published guardrail')})",
                            *extract(base), assumptions))

    print(f"source   {a.results}  [{a.split}]")
    print("assumptions (all overridable):")
    for k, v in assumptions.items():
        print(f"  {k:26} {v:<12} {ASSUMPTION_NOTES[k]}")
    print(f"\n  monthly cost = blocked_FPR x {assumptions['listings']:,} listings"
          f" x {assumptions['conversion_rate']} x Rs {assumptions['average_order_value_inr']:,.0f}\n")

    for p in priced:
        ci = p["blocked_fpr_95ci_pct"]
        print(f"  {p['detector']}")
        print(f"    blocked false positives   {p['blocked_false_positives']} of "
              f"{p['benign_listings_measured']} real listings")
        print(f"    also flagged (still sell) {p['flagged_but_still_selling']}")
        print(f"    blocked FPR               {p['blocked_fpr_pct']}%   "
              f"95% CI [{ci[0]}%, {ci[1]}%]")
        print(f"    lost revenue / month      Rs {p['monthly_cost_inr']:,}   "
              f"(up to Rs {p['monthly_cost_inr_95ci'][1]:,} at the CI bound)")
        print(f"    lost revenue / year       Rs {p['annual_cost_inr']:,}   "
              f"(up to Rs {p['annual_cost_inr_upper_bound']:,})")
        print()

    report = {
        "run_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "source_results": a.results,
        "split": a.split,
        "formula": "monthly cost = blocked_FPR x listings x conversion_rate x AOV",
        "assumptions": assumptions,
        "assumption_notes": ASSUMPTION_NOTES,
        "detectors": priced,
        "note_flag_vs_block": (
            "Only blocked listings are priced. A flagged listing is sanitised and "
            "still sells, so it carries review cost, not lost revenue. The "
            "baseline is binary and has no flag path, so every false positive it "
            "produces is priced as a block."
        ),
        "note_interval": (
            "Wilson 95% intervals. Zero false positives in a sample of this size "
            "is not a zero rate; the upper bound is the figure to budget against. "
            "The interval covers sampling error only -- it says nothing about "
            "listings unlike the ones sampled."
        ),
        "note_not_priced": (
            "The cost of a MISSED attack is not priced here. It is not a rate "
            "times a margin: it is an unauthorised charge on a real buyer, plus "
            "the chargeback and the trust. Recall is reported separately and "
            "deliberately not converted into rupees, because a number that small "
            "would understate it."
        ),
    }
    Path(a.out).write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(f"wrote {a.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
