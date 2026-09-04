"""Day 5. The held-out set, opened once.

    python eval/final_eval.py

This is the ONLY script permitted to read data/processed/test.jsonl. Every other
evaluation path in this repo refuses the split by construction:

    eval/rules_eval.py           argparse choices exclude "test"
    eval/evaluate.py             argparse choices exclude "test"
    sentr/train_classifier.py    load_split() raises on "test"
    notebooks/pack_for_colab.py  refuses to build an archive containing it

RUNNING IT LEAVES A RECEIPT
    The first run writes eval/results/HELD_OUT_OPENED.json -- timestamp, git
    commit, and the exact configuration evaluated. A second run is refused
    unless --rerun is passed, and every rerun is appended to that receipt with
    its reason. The file is committed.

    The point is not that a rerun is forbidden. Code changes and honest reasons
    exist. The point is that a rerun cannot happen quietly, and that anyone
    reading the repo can see how many times the held-out set was looked at
    before the reported number was chosen. That is the difference between a
    held-out set and a second validation set.

WHAT IS NOT ALLOWED AFTER THIS RUNS
    Tuning. No threshold, rule, or hyperparameter moves in response to what this
    prints. If the result is disappointing, the disappointing result is what
    goes in the README (CLAUDE.md rule 12).
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "eval"))

import evaluate as ev  # noqa: E402
from sentr import classifier as clf  # noqa: E402

RESULTS = ROOT / "eval" / "results"
RECEIPT = RESULTS / "HELD_OUT_OPENED.json"
TEST = ROOT / "data" / "processed" / "test.jsonl"


def git_commit() -> str:
    try:
        return subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT,
                              capture_output=True, text=True, timeout=10
                              ).stdout.strip() or "unknown"
    except Exception:
        return "unknown"


def read_receipt() -> dict | None:
    if RECEIPT.exists():
        try:
            return json.loads(RECEIPT.read_text(encoding="utf-8"))
        except Exception:
            return {"openings": [], "corrupt": True}
    return None


def write_receipt(existing: dict | None, config: dict, reason: str) -> dict:
    entry = {
        "opened_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "git_commit": git_commit(),
        "config": config,
        "reason": reason,
    }
    doc = existing or {
        "what": "Every read of data/processed/test.jsonl by eval/final_eval.py.",
        "why": ("A held-out set is only held out if you can show how often it was "
                "looked at. This file is the evidence."),
        "openings": [],
    }
    doc.setdefault("openings", []).append(entry)
    doc["times_opened"] = len(doc["openings"])
    RESULTS.mkdir(parents=True, exist_ok=True)
    RECEIPT.write_text(json.dumps(doc, indent=2), encoding="utf-8")
    return doc


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--rerun", action="store_true",
                    help="open the held-out set again; requires --reason")
    ap.add_argument("--reason", default="",
                    help="recorded verbatim in the receipt, and worth writing honestly")
    ap.add_argument("--no-baseline", dest="baseline", action="store_false", default=True)
    ap.add_argument("--no-classifier", dest="use_classifier", action="store_false",
                    default=True)
    ap.add_argument("--chunk", type=int, default=500)
    ap.add_argument("--latency-sample", type=int, default=50)
    ap.add_argument("--tag", default="day5_final")
    a = ap.parse_args()

    prior = read_receipt()
    if prior and not a.rerun:
        n = prior.get("times_opened", len(prior.get("openings", [])))
        first = prior["openings"][0]
        print(f"REFUSED. The held-out set has already been opened {n} time(s).")
        print(f"  first opened {first['opened_at']} at commit {first['git_commit'][:8]}")
        print(f"  result: eval/results/{a.tag}.json")
        print("\nIf you genuinely need to open it again -- the classifier landed, the "
              "pipeline changed -- then say so and it will be recorded:")
        print('  python eval/final_eval.py --rerun --reason "..."')
        return 1
    if a.rerun and not a.reason.strip():
        raise SystemExit("--rerun requires --reason. It goes in the committed receipt.")

    if not TEST.exists():
        raise SystemExit(f"missing {TEST}")

    model = clf.shared()
    config = {
        "layers": ("rules + classifier + sanitiser"
                   if (model.available and a.use_classifier)
                   else "rules + sanitiser only"),
        "classifier_available": model.available and a.use_classifier,
        "classifier_model_dir": str(model.model_dir),
        "classifier_thresholds": model.thresholds if model.available else None,
        "classifier_base": model.meta.get("base_model") if model.meta else None,
        "baseline_scored": a.baseline,
    }

    print("=" * 72)
    print("OPENING THE HELD-OUT SET. This is the measurement, not a rehearsal.")
    print(f"  layers     {config['layers']}")
    print(f"  classifier {config['classifier_model_dir'] if config['classifier_available'] else 'NOT PRESENT'}")
    if config["classifier_available"]:
        print(f"  thresholds {model.thresholds}")
    print("=" * 72 + "\n")

    # Read directly. evaluate.load() refuses "test" and should keep refusing --
    # this script is the single sanctioned exception, and it is easier to audit
    # as one open() here than as a flag threaded through the shared helper.
    rows = [json.loads(l) for l in open(TEST, encoding="utf-8")]

    print(f"=== TEST  n={len(rows)} ===")
    sentr = ev.score_sentr(rows, chunk=a.chunk,
                           audit_log=str(RESULTS / f"audit_{a.tag}.jsonl"),
                           use_classifier=a.use_classifier)
    sentr["single_item_latency"] = ev.single_item_latency(
        rows, a.latency_sample, use_classifier=a.use_classifier)
    ev.print_block("SENTR", sentr)

    entry = {"sentr": sentr}
    if a.baseline:
        base = ev.score_baseline(rows)
        ev.print_block(f"BASELINE ({ev.BASELINE_MODEL})", base)
        entry["baseline"] = base

    receipt = write_receipt(prior, config,
                            a.reason.strip() or "first and intended opening (Day 5)")

    out = {
        "run_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "tag": a.tag,
        "split": "test (HELD OUT)",
        "git_commit": git_commit(),
        "config": config,
        "times_held_out_set_opened": receipt["times_opened"],
        "discipline": (
            "test.jsonl was written on Day 3, rebuilt blind on Day 4 when the "
            "splits were made payload-disjoint, and read for the first time "
            "here. Nothing was tuned after this ran."
        ),
        "splits": {"test": entry},
    }
    path = RESULTS / f"{a.tag}.json"
    path.write_text(json.dumps(out, indent=2), encoding="utf-8")
    print(f"wrote {path}")
    print(f"receipt: {RECEIPT}  (opened {receipt['times_opened']}x)")
    print("\nThese numbers are frozen. Nothing gets tuned in response to them.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
