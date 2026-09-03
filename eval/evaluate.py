"""Sentr end to end -- rules + classifier -- against the published baseline.

    python eval/evaluate.py --split both
    python eval/evaluate.py --split val --baseline

NOT the held-out set. `--split test` is refused here on purpose; test.jsonl is
opened once, on Day 5 (CLAUDE.md rule 3).

WHAT IT REPORTS, AND WHY EACH ONE IS HERE

    recall / precision          did we stop the attack
    FPR on real listings        the headline number for this track
    FPR *blocked* separately    a flagged listing is sanitised and still sells;
                                only a blocked one earns nothing, so only that
                                one carries a rupee cost on Day 5
    distinct payloads           beside every row count. Three rows built from
                                one payload are one piece of evidence, not
                                three, and a family with a single val payload
                                is reported as such
    published vs authored       no public corpus of catalogue-shaped injections
                                exists, so some payloads were written by this
                                project. Recall on those partly measures our own
                                imagination. Never blended into one figure
    layer attribution           what the classifier added over the rules alone.
                                If it added nothing, that is the finding
    rows over 512 tokens        how many listings an unchunked detector would
                                have read only the first half of

Every number here is reproducible by re-running this script (CLAUDE.md rule 10).
"""

from __future__ import annotations

import argparse
import json
import os
import statistics as st
import sys
import time
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from sentr import classifier as clf, pipeline  # noqa: E402

PROCESSED = ROOT / "data" / "processed"
RESULTS = ROOT / "eval" / "results"
BASELINE_MODEL = "protectai/deberta-v3-base-prompt-injection"
BASELINE_MAX_LEN = 512


def load(split: str) -> list[dict]:
    if split == "test":
        raise SystemExit(
            "test.jsonl is held out and is not evaluated here. Day 5 opens it "
            "once, with eval/final_eval.py (CLAUDE.md rule 3)."
        )
    return [json.loads(l) for l in open(PROCESSED / f"{split}.jsonl", encoding="utf-8")]


def agent_view(r: dict) -> str:
    return f"{r['title']}\n{r['description']}"


# ------------------------------------------------------------------ scoring
def _rate(d: dict) -> dict:
    """caught / n / distinct payloads, for a family, subset or position."""
    return {
        k: {
            "caught": v["caught"],
            "rows": v["rows"],
            "distinct_payloads": len(v["payloads"]),
            "recall_pct": round(100 * v["caught"] / v["rows"], 1) if v["rows"] else None,
        }
        for k, v in sorted(d.items())
    }


def _bucket() -> dict:
    return {"caught": 0, "rows": 0, "payloads": set()}


def score_sentr(rows: list[dict], *, chunk: int, audit_log: str,
                use_classifier: bool) -> dict:
    """Run the real pipeline over the split, exactly as the demo calls it."""
    model = clf.shared()
    warmed = model.warm() if use_classifier else False

    verdicts: list[str] = []
    decided: list[str] = []
    confid: list[float] = []
    rule_ms: list[float] = []
    clf_ms: list[float] = []

    t0 = time.perf_counter()
    for i in range(0, len(rows), chunk):
        batch = rows[i:i + chunk]
        out = pipeline.screen_catalog(batch, log_path=audit_log,
                                      use_classifier=use_classifier)
        for s in out:
            verdicts.append(s.verdict)
            decided.append(s.decided_by)
            confid.append(s.confidence)
            rule_ms.append(s.record.latency_rules_ms)
            clf_ms.append(s.record.latency_classifier_ms)
        print(f"  screened {min(i+chunk, len(rows))}/{len(rows)}", flush=True)
    elapsed = time.perf_counter() - t0

    return summarise(rows, verdicts, decided, confid, rule_ms, clf_ms, elapsed,
                     classifier_available=warmed)


def summarise(rows, verdicts, decided, confid, rule_ms, clf_ms, elapsed,
              *, classifier_available: bool) -> dict:
    tp = fp = tn = fn = 0
    blocked_fp = 0
    fam: dict = defaultdict(_bucket)
    sub: dict = defaultdict(_bucket)
    pos: dict = defaultdict(_bucket)
    by_layer: dict = defaultdict(int)
    misses, false_positives = [], []

    for r, v, d in zip(rows, verdicts, decided):
        caught = v in ("block", "flag")
        if r["label"] == 1:
            span = r["injected_span"]
            for bucket, key in ((fam, r["attack_family"]), (sub, r["attack_subset"]),
                                (pos, r["insert_position"])):
                bucket[key]["rows"] += 1
                bucket[key]["payloads"].add(span)
            if caught:
                tp += 1
                by_layer[d] += 1
                for bucket, key in ((fam, r["attack_family"]), (sub, r["attack_subset"]),
                                    (pos, r["insert_position"])):
                    bucket[key]["caught"] += 1
            else:
                fn += 1
                misses.append({
                    "listing_id": r["listing_id"], "family": r["attack_family"],
                    "subset": r["attack_subset"], "position": r["insert_position"],
                    "span": span[:200],
                })
        else:
            if caught:
                fp += 1
                if v == "block":
                    blocked_fp += 1
                false_positives.append({
                    "listing_id": r["listing_id"], "verdict": v, "decided_by": d,
                    "text": agent_view(r)[:300],
                })
            else:
                tn += 1

    n_benign = fp + tn
    missed_payloads = {m["span"] for m in misses}

    return {
        "n": len(rows),
        "benign": n_benign,
        "poisoned": tp + fn,
        "classifier_available": classifier_available,
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
        "note_on_precision": (
            f"Precision is prevalence-dependent and this split is "
            f"{round(100*(tp+fn)/max(len(rows),1))}% poisoned by construction, far above "
            "any real catalogue. Recall and FPR are the transferable numbers."
        ),
        "caught_by_layer": dict(sorted(by_layer.items(), key=lambda kv: -kv[1])),
        "distinct_payloads_missed": len(missed_payloads),
        "recall_by_subset": _rate(sub),
        "recall_by_family": _rate(fam),
        "recall_by_insert_position": _rate(pos),
        "latency_ms": {
            "rules_p50": round(st.median(rule_ms), 3),
            "rules_p95": round(sorted(rule_ms)[int(0.95 * len(rule_ms))], 3),
            "classifier_p50_batched": round(st.median(clf_ms), 3),
            "total_p50": round(st.median([a + b for a, b in zip(rule_ms, clf_ms)]), 3),
        },
        "throughput_listings_per_sec": round(len(rows) / elapsed, 1),
        "wall_seconds": round(elapsed, 1),
        "false_positive_detail": false_positives[:20],
        "miss_detail": misses[:25],
    }


def single_item_latency(rows, n=50, seed=0, *, use_classifier=True) -> dict:
    """Per-listing latency with no batching, as it runs in the live path of an
    agent's decision. The batched figure above is the bulk-screening cost; this
    is the one an agent waits for."""
    import random
    rng = random.Random(seed)
    sample = rng.sample(rows, min(n, len(rows)))
    if use_classifier:
        clf.shared().warm()
    lat = []
    for r in sample:
        t0 = time.perf_counter()
        pipeline.screen(r, log_path=os.devnull, use_classifier=use_classifier)
        lat.append((time.perf_counter() - t0) * 1000)
    lat.sort()
    return {"p50_ms": round(lat[len(lat) // 2], 1),
            "p95_ms": round(lat[int(0.95 * len(lat))], 1),
            "n": len(lat)}


# ---------------------------------------------------------------- baseline
def score_baseline(rows: list[dict], token_budget=2048, max_batch=32) -> dict:
    """The public off-the-shelf guardrail on the same split.

    Binary by design: it has no flag-and-sanitise path, so every positive is a
    block. That difference is a result, not an unfairness -- it is why its false
    positives cost money and ours mostly do not.
    """
    clf._prepare_env()
    import torch  # noqa: E402
    import numpy as np  # noqa: E402
    from transformers import AutoModelForSequenceClassification, AutoTokenizer

    torch.set_num_threads(int(os.environ["OMP_NUM_THREADS"]))
    tok = AutoTokenizer.from_pretrained(BASELINE_MODEL)
    mdl = AutoModelForSequenceClassification.from_pretrained(BASELINE_MODEL)
    mdl.eval()

    texts = [agent_view(r) for r in rows]
    lens = [len(tok(t, truncation=True, max_length=BASELINE_MAX_LEN)["input_ids"])
            for t in texts]
    truncated = sum(1 for L in lens if L >= BASELINE_MAX_LEN)

    order = sorted(range(len(texts)), key=lambda i: lens[i])
    batches, cur = [], []
    for i in order:
        peak = (len(cur) + 1) * max([lens[i]] + [lens[j] for j in cur])
        if cur and (peak > token_budget or len(cur) >= max_batch):
            batches.append(cur)
            cur = []
        cur.append(i)
    if cur:
        batches.append(cur)

    probs = np.zeros(len(texts), dtype=np.float32)
    t0 = time.perf_counter()
    done = 0
    for bi, idx in enumerate(batches):
        enc = tok([texts[j] for j in idx], return_tensors="pt", padding=True,
                  truncation=True, max_length=BASELINE_MAX_LEN)
        with torch.no_grad():
            p = mdl(**enc).logits.softmax(-1)[:, 1]
        for k, j in enumerate(idx):
            probs[j] = float(p[k])
        done += len(idx)
        if bi % 40 == 0 or done == len(texts):
            el = time.perf_counter() - t0
            print(f"  baseline {done}/{len(texts)}  {el:.0f}s  ({done/el:.1f}/s)",
                  flush=True)
    elapsed = time.perf_counter() - t0

    verdicts = ["block" if p >= 0.5 else "allow" for p in probs]
    out = summarise(rows, verdicts, ["baseline_model"] * len(rows), list(map(float, probs)),
                    [0.0] * len(rows), [0.0] * len(rows), elapsed,
                    classifier_available=True)
    out["model"] = BASELINE_MODEL
    out["threshold"] = 0.5
    out["listings_truncated_at_512_tokens"] = truncated
    out["note_truncation"] = (
        f"{truncated} listing(s) hit the 512-token limit and were read only in "
        "part. Sentr's classifier windows the full text instead, which is the "
        "point of chunking: on a long listing an unchunked detector never sees "
        "a payload written at the end."
    )
    return out


# -------------------------------------------------------------------- main
def print_block(name: str, r: dict) -> None:
    print(f"--- {name} ---")
    print(f"  recall            {r['recall_pct']}%   "
          f"({r['poisoned']} poisoned rows)")
    print(f"  precision         {r['precision_pct']}%")
    print(f"  FPR any verdict   {r['false_positive_rate_any_pct']}%  "
          f"({r['false_positives_total']} of {r['benign']} real listings)")
    print(f"  FPR blocked only  {r['false_positive_rate_blocked_pct']}%  "
          f"({r['false_positives_blocked']} listings killed)")
    print(f"  throughput        {r['throughput_listings_per_sec']}/sec")
    print("  recall by subset:")
    for k, v in r["recall_by_subset"].items():
        print(f"    {k:11} {v['caught']}/{v['rows']} rows = {v['recall_pct']}%"
              f"   ({v['distinct_payloads']} distinct payloads)")
    if r.get("caught_by_layer"):
        print(f"  caught by:        {r['caught_by_layer']}")
    print()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--split", default="val", choices=["train", "val", "both"])
    ap.add_argument("--baseline", action="store_true",
                    help="also score the published guardrail (slow: ~3 listings/sec)")
    ap.add_argument("--no-classifier", dest="use_classifier", action="store_false",
                    default=True, help="layer 1 only, for an ablation")
    ap.add_argument("--chunk", type=int, default=500)
    ap.add_argument("--limit", type=int, default=0,
                    help="debug only: cap rows. Never use for a reported number")
    ap.add_argument("--latency-sample", type=int, default=50)
    ap.add_argument("--audit-log", default="",
                    help="where pipeline audit records go (default: a temp file; "
                         "eval writes thousands and they are not a claim)")
    ap.add_argument("--tag", default="day4_sentr")
    a = ap.parse_args()

    audit_log = a.audit_log or str(
        Path(os.environ.get("TEMP", "/tmp")) / f"sentr_eval_audit_{a.tag}.jsonl")
    splits = ["train", "val"] if a.split == "both" else [a.split]

    model = clf.shared()
    print(f"classifier: {'LOADED from ' + str(model.model_dir) if model.available else 'NOT PRESENT -- rules only'}")
    if model.available:
        print(f"  thresholds {model.thresholds}")
        print(f"  base {model.meta.get('base_model', '?')}, "
              f"trained {model.meta.get('trained_at', '?')}")
    print()

    out = {
        "run_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "tag": a.tag,
        "layers": "rules (1) + classifier (2) + sanitiser (3)"
                  if (model.available and a.use_classifier) else "rules (1) + sanitiser (3) only",
        "classifier_model_dir": str(model.model_dir),
        "classifier_available": model.available and a.use_classifier,
        "classifier_thresholds": model.thresholds if model.available else None,
        "classifier_training_meta": model.meta or None,
        "held_out": "test.jsonl NOT evaluated. Sealed until Day 5.",
        "audit_log": audit_log,
        "splits": {},
    }

    for split in splits:
        rows = load(split)
        if a.limit:
            rows = rows[: a.limit]
            out["DEBUG_LIMITED"] = f"only the first {a.limit} rows -- not a reportable number"
        print(f"=== {split.upper()}  n={len(rows)} ===")
        sentr = score_sentr(rows, chunk=a.chunk, audit_log=audit_log,
                            use_classifier=a.use_classifier)
        sentr["single_item_latency"] = single_item_latency(
            rows, a.latency_sample, use_classifier=a.use_classifier)
        print_block("SENTR", sentr)
        entry = {"sentr": sentr}

        if a.baseline:
            base = score_baseline(rows)
            print_block(f"BASELINE ({BASELINE_MODEL})", base)
            entry["baseline"] = base

        out["splits"][split] = entry

    RESULTS.mkdir(parents=True, exist_ok=True)
    path = RESULTS / f"{a.tag}.json"
    path.write_text(json.dumps(out, indent=2), encoding="utf-8")
    print(f"wrote {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
