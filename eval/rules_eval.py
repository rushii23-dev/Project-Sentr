"""Evaluate the layer-1 rule engine on train and val.

NOT the held-out set. test.jsonl stays sealed until Day 5, when it is opened
once (SPEC.md rule 3). Passing --split test is refused here on purpose.

Recall is reported separately for the two attack subsets, because they are not
equally strong evidence:

    published : payloads copied verbatim from public research corpora. Nobody
                on this project wrote them, so this number is independent.
    authored  : commerce-framed instances of documented families, written by
                this project. Recall here partly measures our own imagination.

The published number is the one that should carry weight in the pitch.

Usage:  python eval/rules_eval.py [--split train|val]
"""

from __future__ import annotations

import argparse
import json
import statistics as st
import sys
import time
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from sentr import rules  # noqa: E402

PROCESSED = ROOT / "data" / "processed"
RESULTS = ROOT / "eval" / "results"


def evaluate(split: str) -> dict:
    rows = [json.loads(l) for l in open(PROCESSED / f"{split}.jsonl", encoding="utf-8")]
    tp = fp = tn = fn = 0
    blocked_fp = 0
    lat: list[float] = []
    fam: dict[str, list[int]] = defaultdict(lambda: [0, 0])
    sub: dict[str, list[int]] = defaultdict(lambda: [0, 0])
    pos: dict[str, list[int]] = defaultdict(lambda: [0, 0])
    false_positives = []
    misses = []

    t0 = time.perf_counter()
    for r in rows:
        text = f"{r['title']}\n{r['description']}"
        s = time.perf_counter()
        res = rules.scan(text)
        lat.append((time.perf_counter() - s) * 1000)
        caught = res.verdict in ("block", "flag")

        if r["label"] == 1:
            fam[r["attack_family"]][1] += 1
            sub[r["attack_subset"]][1] += 1
            pos[r["insert_position"]][1] += 1
            if caught:
                tp += 1
                fam[r["attack_family"]][0] += 1
                sub[r["attack_subset"]][0] += 1
                pos[r["insert_position"]][0] += 1
            else:
                fn += 1
                misses.append({
                    "listing_id": r["listing_id"], "family": r["attack_family"],
                    "subset": r["attack_subset"], "position": r["insert_position"],
                    "span": r["injected_span"][:200],
                })
        else:
            if caught:
                fp += 1
                if res.verdict == "block":
                    blocked_fp += 1
                false_positives.append({
                    "listing_id": r["listing_id"], "verdict": res.verdict,
                    "score": res.score,
                    "rules": [h.rule_id for h in res.hits],
                    "spans": [h.truncated(120) for h in res.hits],
                })
            else:
                tn += 1
    elapsed = time.perf_counter() - t0
    n_benign = fp + tn

    def rate(d):
        return {k: {"caught": v[0], "n": v[1],
                    "recall_pct": round(100 * v[0] / v[1], 1) if v[1] else None}
                for k, v in sorted(d.items())}

    return {
        "split": split,
        "n": len(rows),
        "benign": n_benign,
        "poisoned": tp + fn,
        "recall_pct": round(100 * tp / max(tp + fn, 1), 1),
        "precision_pct": round(100 * tp / max(tp + fp, 1), 1),
        "false_positive_rate_any_pct": round(100 * fp / max(n_benign, 1), 3),
        "false_positive_rate_blocked_pct": round(100 * blocked_fp / max(n_benign, 1), 3),
        "false_positives_total": fp,
        "false_positives_blocked": blocked_fp,
        "note_on_flag": (
            "A flagged listing is sanitised and still sells. Only a BLOCKED "
            "listing earns nothing, so the blocked rate is the one that carries "
            "a rupee cost."
        ),
        "recall_by_subset": rate(sub),
        "recall_by_family": rate(fam),
        "recall_by_insert_position": rate(pos),
        "latency_ms": {
            "p50": round(st.median(lat), 3),
            "p95": round(sorted(lat)[int(0.95 * len(lat))], 3),
            "mean": round(st.mean(lat), 3),
        },
        "throughput_listings_per_sec": round(len(rows) / elapsed, 1),
        "false_positive_detail": false_positives[:20],
        "miss_detail": misses[:25],
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--split", default="train", choices=["train", "val", "both"])
    # Re-running this to CHECK the committed numbers should not overwrite them.
    # verify_all.py points --out at a scratch file and diffs the two, so a
    # verification run leaves the working tree exactly as it found it.
    ap.add_argument("--out", default=str(RESULTS / "rules_layer1.json"))
    a = ap.parse_args()

    splits = ["train", "val"] if a.split == "both" else [a.split]
    out = {
        "run_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "layer": "rules only (layer 1). Layer 2 classifier lands on Day 4.",
        "held_out": "test.jsonl NOT evaluated. Sealed until Day 5.",
        "splits": {},
    }

    for split in splits:
        r = evaluate(split)
        out["splits"][split] = r
        print(f"=== {split.upper()}  n={r['n']} ({r['benign']} benign / {r['poisoned']} poisoned) ===")
        print(f"  recall              {r['recall_pct']}%")
        print(f"  precision           {r['precision_pct']}%")
        print(f"  FPR any verdict     {r['false_positive_rate_any_pct']}%  ({r['false_positives_total']} listings)")
        print(f"  FPR blocked only    {r['false_positive_rate_blocked_pct']}%  ({r['false_positives_blocked']} listings killed)")
        print(f"  latency p50/p95     {r['latency_ms']['p50']}ms / {r['latency_ms']['p95']}ms")
        print(f"  throughput          {r['throughput_listings_per_sec']}/sec")
        print("  recall by subset:")
        for k, v in r["recall_by_subset"].items():
            print(f"    {k:11} {v['caught']}/{v['n']} = {v['recall_pct']}%")
        print()

    # Not "day3_rules": these numbers are re-measured whenever the rules or the
    # dataset change, and a filename claiming a date it was not produced on is
    # a small lie in a directory whose whole purpose is committed claims.
    path = Path(a.out)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(out, indent=2), encoding="utf-8")
    print(f"wrote {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
