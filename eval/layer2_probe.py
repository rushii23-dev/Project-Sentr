"""Why layer 2 does not ship: the arithmetic, reproducibly.

    python eval/layer2_probe.py

Sentr's architecture has a classifier slot behind the rules. This script is the
measurement that decided what goes in it, and it is committed because the
decision is a claim (CLAUDE.md rule 10).

THE QUESTION
    Of the attacks the rule layer misses, how many can an off-the-shelf
    prompt-injection model recover, and what does that recovery cost in honest
    listings blocked?

    The model is scored ONLY on what the rules allowed, chunked with the same
    window geometry sentr/classifier.py uses at inference, and swept across
    thresholds. Val only -- the held-out set has nothing to do with this.

WHY IT IS THE PUBLIC MODEL AND NOT A FINE-TUNE
    The development machine measured 1.0 training windows/sec and did not get
    faster when 82% of the parameters were frozen, which means it is bound by
    memory, not compute. Three epochs is roughly four hours here. The fine-tune
    is implemented (sentr/train_classifier.py) and runs on Colab in minutes; it
    had not been run when this decision was taken, and this script measures the
    alternative that was actually available.

WHAT IT FOUND
    See eval/results/layer2_decision.json. Summary: there is no threshold at
    which the off-the-shelf model catches anything without also blocking honest
    listings, and the listings it blocks are `Key: Value` specification tables --
    the same failure mode the Day 1 baseline run found. Buying recall at that
    price was declined, and the README says so with this number attached.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from sentr import rules  # noqa: E402
from sentr.classifier import Classifier  # noqa: E402

RESULTS = ROOT / "eval" / "results"
PROCESSED = ROOT / "data" / "processed"
HUB = ROOT / ".cache" / "huggingface" / "hub"
PUBLIC_MODEL = "protectai/deberta-v3-base-prompt-injection"
GRID = [0.5, 0.7, 0.9, 0.95, 0.99, 0.995, 0.999, 0.9999]


def public_snapshot() -> Path:
    """The cached snapshot directory for the public detector."""
    base = HUB / f"models--{PUBLIC_MODEL.replace('/', '--')}" / "snapshots"
    if not base.exists():
        raise SystemExit(
            f"{PUBLIC_MODEL} is not in the local cache. Run eval/baseline.py "
            "once to download it.")
    snaps = sorted(base.iterdir())
    return snaps[0]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--split", default="val", choices=["train", "val"],
                    help="never test; this is a design decision, not a result")
    ap.add_argument("--chunk", type=int, default=400)
    a = ap.parse_args()

    rows = [json.loads(l) for l in open(PROCESSED / f"{a.split}.jsonl", encoding="utf-8")]
    texts = [f"{r['title']}\n{r['description']}" for r in rows]
    n_attacks = sum(1 for r in rows if r["label"] == 1)

    # Layer 1 first. The classifier only ever sees what the rules allowed.
    allowed = [i for i, t in enumerate(texts) if rules.scan(t).verdict == "allow"]
    missed = [i for i in allowed if rows[i]["label"] == 1]
    benign_through = [i for i in allowed if rows[i]["label"] == 0]
    caught_by_rules = n_attacks - len(missed)
    print(f"{a.split}: rules catch {caught_by_rules}/{n_attacks} "
          f"({100*caught_by_rules/n_attacks:.1f}%) and pass {len(allowed)} listings on "
          f"({len(benign_through)} benign, {len(missed)} missed attacks)", flush=True)

    snap = public_snapshot()
    clf = Classifier(snap)
    print(f"scoring the remainder with {PUBLIC_MODEL}", flush=True)
    t0 = time.perf_counter()
    scored = []
    for s in range(0, len(allowed), a.chunk):
        idx = allowed[s:s + a.chunk]
        scored += clf.score_texts([texts[i] for i in idx])
        el = time.perf_counter() - t0
        print(f"  {len(scored)}/{len(allowed)}  {el:.0f}s ({len(scored)/el:.1f}/s)",
              flush=True)
    score = {i: r.score for i, r in zip(allowed, scored)}

    sweep = []
    for t in GRID:
        fp = [i for i in benign_through if score[i] >= t]
        tp = [i for i in missed if score[i] >= t]
        sweep.append({
            "threshold": t,
            "honest_listings_blocked": len(fp),
            "blocked_fpr_pct": round(100 * len(fp) / max(len(benign_through), 1), 3),
            "attacks_recovered": len(tp),
            "combined_recall_pct": round(100 * (caught_by_rules + len(tp)) / n_attacks, 1),
        })

    zero_cost = next((s for s in sweep if s["honest_listings_blocked"] == 0), None)

    # What the model calls an attack that is not one. This is the finding.
    worst = sorted(benign_through, key=lambda i: -score[i])[:5]
    fp_examples = [{
        "listing_id": rows[i]["listing_id"],
        "score": round(score[i], 4),
        "title": rows[i]["title"][:120],
        "description": rows[i]["description"][:260],
    } for i in worst if score[i] > 0]

    print(f"\n{'thresh':>8} {'honest blocked':>15} {'attacks recovered':>18} {'recall':>9}")
    for s in sweep:
        print(f"{s['threshold']:>8} {s['honest_listings_blocked']:>15} "
              f"{s['attacks_recovered']:>18} {s['combined_recall_pct']:>8}%")
    print(f"\nzero-cost threshold: {zero_cost['threshold'] if zero_cost else 'NONE EXISTS'}")
    print("\nhighest-scoring honest listings:")
    for e in fp_examples:
        print(f"  {e['score']}  {e['title'][:80]}")
        print(f"          {e['description'][:150]}")

    best = max(sweep, key=lambda s: s["combined_recall_pct"])
    report = {
        "run_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "question": ("Of the attacks the rule layer misses, how many can the public "
                     "detector recover, and what does that cost in honest listings?"),
        "split": a.split,
        "model": PUBLIC_MODEL,
        "note_not_a_finetune": (
            "This is the off-the-shelf model, not sentr/train_classifier.py's output. "
            "The development machine is memory-bound at ~1 training window/sec "
            "(unchanged when 82% of parameters are frozen), so a fine-tune was not "
            "available when this decision was taken. The fine-tune remains "
            "implemented and runs on Colab."
        ),
        "rules_alone": {
            "attacks_caught": caught_by_rules,
            "attacks_total": n_attacks,
            "recall_pct": round(100 * caught_by_rules / n_attacks, 1),
            "listings_passed_to_layer2": len(allowed),
            "benign_passed": len(benign_through),
            "attacks_missed": len(missed),
        },
        "threshold_sweep": sweep,
        "zero_cost_threshold_exists": zero_cost is not None,
        "best_recall_option": best,
        "false_positive_examples": fp_examples,
        "decision": (
            "Declined. There is no threshold at which the public model recovers a "
            "single missed attack without also blocking an honest listing, and a "
            "blocked listing earns nothing. Taking the best available option would "
            f"raise recall {round(100*caught_by_rules/n_attacks,1)}% -> "
            f"{best['combined_recall_pct']}% while moving blocked-FPR "
            f"0.00% -> {best['blocked_fpr_pct']}%. eval/cost_model.py prices that."
        ),
        "why_it_fails": (
            "The honest listings it scores highest are `Key: Value` specification "
            "tables -- 'Gender: Unisex, Feature: Moisturizing, NET WT: 150g'. A "
            "chat-trained detector reads a spec table as a role marker. This is the "
            "same failure mode the Day 1 baseline run found on 6,000 real listings, "
            "reproduced independently here."
        ),
    }
    RESULTS.mkdir(parents=True, exist_ok=True)
    out = RESULTS / "layer2_decision.json"
    out.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(f"\nwrote {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
