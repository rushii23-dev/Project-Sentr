"""Day 1 go/no-go: run the public off-the-shelf guardrail over REAL listings.

We score `protectai/deberta-v3-base-prompt-injection` -- the most widely used
free prompt-injection detector -- against 6,000 genuine Amazon.in and Flipkart
product listings. Every one of them is honest seller text. Any listing the
model calls INJECTION is a FALSE POSITIVE: a real product that an agentic
storefront would have refused to show a buyer.

This establishes the number Sentr has to beat. It is deliberately run before
any Sentr code exists, so the baseline cannot be tuned to flatter us.

Usage:  python eval/baseline.py [--limit N] [--batch 16]
"""

import argparse
import json
import os
import time
from pathlib import Path

# Must precede the torch import. torch and numpy each ship an OpenMP runtime
# on Windows; loading both segfaults the process, and the thread count has to
# be fixed in the environment before torch initialises -- torch.set_num_threads()
# afterwards is too late and still crashes.
os.environ.setdefault("KMP_DUPLICATE_LIB_OK", "TRUE")
os.environ.setdefault("OMP_NUM_THREADS", os.environ.get("SENTR_THREADS", "6"))
# Keep the HuggingFace model cache inside the project so the whole thing is
# self-contained on one drive and needs no machine-level environment setup.
os.environ.setdefault(
    "HF_HOME", str(Path(__file__).resolve().parent.parent / ".cache" / "huggingface")
)

# torch MUST be imported before numpy here. numpy's MKL build loads its own
# OpenMP runtime, and if it wins the race torch's initialisation segfaults.
import torch  # noqa: E402
import numpy as np  # noqa: E402
from transformers import AutoModelForSequenceClassification, AutoTokenizer  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
BENIGN = ROOT / "data" / "processed" / "benign_listings.jsonl"
RESULTS = ROOT / "eval" / "results"

MODEL_ID = "protectai/deberta-v3-base-prompt-injection"
MAX_LEN = 512
THRESHOLDS = [0.5, 0.6, 0.7, 0.8, 0.9, 0.95, 0.99]


def load_listings(limit=None):
    rows = []
    with open(BENIGN, encoding="utf-8") as f:
        for line in f:
            rows.append(json.loads(line))
            if limit and len(rows) >= limit:
                break
    return rows


def agent_view(rec):
    """Exactly what an AI buying agent would read for this product."""
    return f"{rec['title']}\n\n{rec['description']}"


def score_all(texts, tok, mdl, token_budget, max_batch):
    """Return P(INJECTION) for every text.

    Batches are built to a fixed TOKEN budget rather than a fixed row count.
    This machine has ~1.6GB of free RAM, and DeBERTa's disentangled attention
    is O(seq_len^2) in activation memory -- a flat batch of 16 at 512 tokens
    OOMs and takes the process down with a segfault. Budgeting on
    rows x seq_len keeps peak memory flat: ~32 rows of short listings, or 4
    rows of long ones. Same answers, no crash.
    """
    lens = [len(tok(t, truncation=True, max_length=MAX_LEN)["input_ids"]) for t in texts]
    truncated = sum(1 for L in lens if L >= MAX_LEN)

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
                  truncation=True, max_length=MAX_LEN)
        with torch.no_grad():
            p = mdl(**enc).logits.softmax(-1)[:, 1]
        for k, j in enumerate(idx):
            probs[j] = float(p[k])
        done += len(idx)
        if bi % 25 == 0 or done == len(texts):
            el = time.perf_counter() - t0
            eta = (len(texts) - done) / max(done / el, 1e-9)
            print(f"  {done}/{len(texts)}  {el:.0f}s  ({done/el:.1f}/s)  eta {eta/60:.1f}m",
                  flush=True)

    return probs, time.perf_counter() - t0, truncated


def single_item_latency(texts, tok, mdl, n=200, seed=0):
    """Honest per-listing latency: batch size 1, as it would run in the
    live path of an agent's decision."""
    rng = np.random.default_rng(seed)
    sample = [texts[i] for i in rng.choice(len(texts), size=min(n, len(texts)), replace=False)]
    lat = []
    for t in sample:
        s = time.perf_counter()
        enc = tok([t], return_tensors="pt", truncation=True, max_length=MAX_LEN)
        with torch.no_grad():
            mdl(**enc)
        lat.append((time.perf_counter() - s) * 1000)
    lat = np.array(lat)
    return {
        "p50_ms": round(float(np.percentile(lat, 50)), 1),
        "p95_ms": round(float(np.percentile(lat, 95)), 1),
        "mean_ms": round(float(lat.mean()), 1),
        "n": len(lat),
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--token-budget", type=int, default=2048)
    ap.add_argument("--max-batch", type=int, default=32)
    ap.add_argument("--tag", default="day1_baseline")
    a = ap.parse_args()

    threads = int(os.environ["OMP_NUM_THREADS"])
    torch.set_num_threads(threads)
    RESULTS.mkdir(parents=True, exist_ok=True)

    rows = load_listings(a.limit)
    texts = [agent_view(r) for r in rows]
    print(f"scoring {len(texts)} REAL listings with {MODEL_ID}")

    tok = AutoTokenizer.from_pretrained(MODEL_ID)
    mdl = AutoModelForSequenceClassification.from_pretrained(MODEL_ID)
    mdl.eval()

    probs, elapsed, truncated = score_all(texts, tok, mdl, a.token_budget, a.max_batch)
    lat = single_item_latency(texts, tok, mdl, n=min(200, len(texts)))

    sweep = {}
    for th in THRESHOLDS:
        fp = int((probs >= th).sum())
        sweep[str(th)] = {"false_positives": fp, "fpr_pct": round(100 * fp / len(probs), 2)}

    by_source = {}
    for src in sorted({r["source"] for r in rows}):
        m = np.array([r["source"] == src for r in rows])
        fp = int((probs[m] >= 0.5).sum())
        by_source[src] = {"n": int(m.sum()), "false_positives": fp,
                          "fpr_pct": round(100 * fp / int(m.sum()), 2)}

    fp_at_50 = int((probs >= 0.5).sum())
    report = {
        "run": a.tag,
        "model": MODEL_ID,
        "listings_scored": len(probs),
        "all_listings_are_benign": True,
        "note": "every listing is genuine seller text, so every INJECTION call is a false positive",
        "headline": {
            "threshold": 0.5,
            "false_positives": fp_at_50,
            "false_positive_rate_pct": round(100 * fp_at_50 / len(probs), 2),
        },
        "threshold_sweep": sweep,
        "by_source": by_source,
        "score_distribution": {
            "mean": round(float(probs.mean()), 4),
            "median": round(float(np.median(probs)), 4),
            "p90": round(float(np.percentile(probs, 90)), 4),
            "p99": round(float(np.percentile(probs, 99)), 4),
            "max": round(float(probs.max()), 4),
        },
        "throughput_listings_per_sec": round(len(probs) / elapsed, 2),
        "token_budget": a.token_budget,
        "max_batch": a.max_batch,
        "torch_threads": threads,
        "single_item_latency": lat,
        "truncated_at_512_tokens": truncated,
        "truncation_note": "listings longer than 512 tokens are cut; text past the cut is invisible to the model",
    }

    out = RESULTS / f"{a.tag}_benign.json"
    out.write_text(json.dumps(report, indent=2), encoding="utf-8")

    # Full per-listing scores. Saved so the distribution can be re-analysed
    # without paying for another 30-minute CPU pass.
    with open(RESULTS / f"{a.tag}_scores.jsonl", "w", encoding="utf-8") as f:
        for pr, r in zip(probs, rows):
            f.write(json.dumps({
                "listing_id": r["listing_id"], "source": r["source"],
                "category": r["category"], "p_injection": round(float(pr), 6),
                "title_len": len(r["title"]), "desc_len": len(r["description"]),
            }, ensure_ascii=False) + "\n")

    # The listings the baseline was most confident were attacks. These are the
    # ones a merchant would be phoning support about.
    worst = sorted(zip(probs, rows), key=lambda x: -x[0])[:50]
    with open(RESULTS / f"{a.tag}_top_false_positives.jsonl", "w", encoding="utf-8") as f:
        for p, r in worst:
            f.write(json.dumps({
                "p_injection": round(float(p), 4),
                "listing_id": r["listing_id"], "source": r["source"],
                "category": r["category"], "title": r["title"],
                "description": r["description"][:600],
            }, ensure_ascii=False) + "\n")

    print("\n" + "=" * 62)
    print(f"FALSE POSITIVES @0.5 : {fp_at_50}/{len(probs)}  = {report['headline']['false_positive_rate_pct']}%")
    print(f"throughput           : {report['throughput_listings_per_sec']}/s")
    print(f"latency p50/p95      : {lat['p50_ms']}/{lat['p95_ms']} ms")
    print(f"truncated @512       : {truncated}")
    print("=" * 62)
    for th, v in sweep.items():
        print(f"  thr {th:>5} -> {v['false_positives']:>4} FP  ({v['fpr_pct']}%)")
    print(f"\nwrote {out}")


if __name__ == "__main__":
    main()
