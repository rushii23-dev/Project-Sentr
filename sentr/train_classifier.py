"""Fine-tune the layer-2 classifier on catalogue-shaped data.

    python -m sentr.train_classifier --epochs 3

Runs on Colab's free GPU or on CPU here; the only difference is the token
budget per batch. Nothing about the data or the geometry changes between them.

WHAT IT TRAINS ON
    Windows, not listings. Each listing is tokenised with the exact geometry
    classifier.py uses at inference, and each window is labelled by whether it
    actually overlaps the injected span:

        benign listing                        -> every window is 0
        poisoned, window holds >=60% of span  -> 1
        poisoned, window holds none of it     -> 0   (carrier text; a useful
                                                      hard negative -- the same
                                                      seller prose as a clean
                                                      listing)
        poisoned, window holds some of it     -> dropped, too ambiguous to
                                                 label either way

    Labelling by overlap is what stops the model learning "long listing ==
    attack". A poisoned listing contributes genuine negatives too.

WHAT IT NEVER TOUCHES
    test.jsonl. The split argument does not accept it and the file is never
    opened here (CLAUDE.md rule 3).

FROZEN EMBEDDINGS
    On by default. deberta-v3-xsmall is 71M parameters, 49M of which are the
    128k-token embedding matrix. Freezing it removes ~590MB of gradient and
    optimiser state -- the difference between training on this machine and
    swapping -- and a 5,259-row dataset was never going to retrain a vocabulary
    usefully anyway. Pass --no-freeze-embeddings on Colab if you want it warm.
"""

from __future__ import annotations

import argparse
import json
import os
import random
import sys
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from .classifier import MAX_LENGTH, STRIDE, ROOT, _prepare_env

_prepare_env()

import torch  # noqa: E402  -- after _prepare_env, before numpy
import numpy as np  # noqa: E402
from transformers import (  # noqa: E402
    AutoModelForSequenceClassification,
    AutoTokenizer,
    get_linear_schedule_with_warmup,
)

PROCESSED = ROOT / "data" / "processed"
DEFAULT_BASE = "microsoft/deberta-v3-xsmall"
DEFAULT_OUT = ROOT / "models" / "sentr-classifier"

# Fraction of the injected span a window must contain before it counts as a
# positive. Below this and above zero the window is dropped rather than guessed.
MIN_SPAN_OVERLAP = 0.60

THRESHOLD_GRID = [round(x, 3) for x in np.arange(0.50, 1.0, 0.01)] + [0.995, 0.999]


# ----------------------------------------------------------------- data
def load_split(split: str) -> list[dict]:
    if split == "test":
        raise SystemExit(
            "test.jsonl is the held-out set and is not readable from training "
            "(CLAUDE.md rule 3). It is opened once, on Day 5."
        )
    path = PROCESSED / f"{split}.jsonl"
    return [json.loads(l) for l in open(path, encoding="utf-8")]


def agent_view(row: dict) -> str:
    """Exactly the text screened at inference -- pipeline._screened_text()."""
    return f"{row['title']}\n{row['description']}"


def span_range(row: dict) -> tuple[int, int] | None:
    """Where the payload sits in the agent-visible text, or None if benign.

    span_start is an offset into the description; the screened text is
    title + "\\n" + description, so the title and its newline shift it.
    """
    if row["label"] != 1:
        return None
    start = len(row["title"]) + 1 + row["span_start"]
    return start, start + len(row["injected_span"])


def build_windows(rows: list[dict], tok, max_length: int, stride: int):
    """Window every listing and label each window by span overlap."""
    ids_out, labels, dropped, per_row = [], [], 0, []
    for row in rows:
        text = agent_view(row)
        enc = tok(text, truncation=True, max_length=max_length, stride=stride,
                  return_overflowing_tokens=True, return_offsets_mapping=True,
                  add_special_tokens=True)
        span = span_range(row)
        kept = 0
        for ids, offsets in zip(enc["input_ids"], enc["offset_mapping"]):
            real = [o for o in offsets if o != (0, 0)]
            if not real:
                continue
            ws, we = real[0][0], real[-1][1]
            if span is None:
                label = 0
            else:
                ps, pe = span
                overlap = max(0, min(we, pe) - max(ws, ps))
                frac = overlap / max(pe - ps, 1)
                if frac >= MIN_SPAN_OVERLAP:
                    label = 1
                elif overlap == 0:
                    label = 0
                else:
                    dropped += 1
                    continue
            ids_out.append(ids)
            labels.append(label)
            kept += 1
        per_row.append(kept)
    return ids_out, labels, dropped, per_row


def batches_by_token_budget(order: list[int], lengths: list[int],
                            budget: int, max_batch: int):
    """Group indices so rows x padded_width stays under `budget`.

    Row-count batching is what OOMs: attention memory is quadratic in width, so
    a batch of 16 short windows and a batch of 16 long ones differ by an order
    of magnitude. Budgeting the product keeps peak memory flat.
    """
    out, cur, width = [], [], 0
    for i in order:
        w = max(width, lengths[i])
        if cur and ((len(cur) + 1) * w > budget or len(cur) >= max_batch):
            out.append(cur)
            cur, width = [], 0
            w = lengths[i]
        cur.append(i)
        width = w
    if cur:
        out.append(cur)
    return out


def collate(idx: list[int], ids: list[list[int]], labels: list[int], pad: int):
    width = max(len(ids[i]) for i in idx)
    x = torch.full((len(idx), width), pad, dtype=torch.long)
    m = torch.zeros((len(idx), width), dtype=torch.long)
    for k, i in enumerate(idx):
        w = ids[i]
        x[k, : len(w)] = torch.tensor(w, dtype=torch.long)
        m[k, : len(w)] = 1
    y = torch.tensor([labels[i] for i in idx], dtype=torch.long)
    return x, m, y


# ----------------------------------------------------------- evaluation
@torch.no_grad()
def score_listings(rows: list[dict], model, tok, device, args) -> np.ndarray:
    """Listing-level score = max over its windows, as at inference.

    Windowed without labels: build_windows() drops ambiguous windows, which is
    right for training and wrong for scoring -- at inference no span is known,
    so every window has to be looked at.
    """
    model.eval()
    flat, owner = [], []
    for ri, row in enumerate(rows):
        text = agent_view(row)
        enc = tok(text, truncation=True, max_length=args.max_length,
                  stride=args.stride, return_overflowing_tokens=True,
                  add_special_tokens=True)
        for w in enc["input_ids"]:
            flat.append(w)
            owner.append(ri)

    lengths = [len(w) for w in flat]
    order = sorted(range(len(flat)), key=lambda i: lengths[i])
    pad = tok.pad_token_id or 0
    scores = np.zeros(len(flat), dtype=np.float32)
    for idx in batches_by_token_budget(order, lengths, args.eval_token_budget, 64):
        x, m, _ = collate(idx, flat, [0] * len(flat), pad)
        p = model(input_ids=x.to(device), attention_mask=m.to(device)
                  ).logits.softmax(-1)[:, 1]
        for k, i in enumerate(idx):
            scores[i] = float(p[k])

    out = np.zeros(len(rows), dtype=np.float32)
    for i, ri in enumerate(owner):
        out[ri] = max(out[ri], scores[i])
    return out


def sweep(scores: np.ndarray, labels: np.ndarray) -> list[dict]:
    pos, neg = scores[labels == 1], scores[labels == 0]
    rows = []
    for t in THRESHOLD_GRID:
        tp = int((pos >= t).sum())
        fp = int((neg >= t).sum())
        rows.append({
            "threshold": float(t),
            "recall_pct": round(100 * tp / max(len(pos), 1), 2),
            "benign_hits": fp,
            "fpr_pct": round(100 * fp / max(len(neg), 1), 3),
        })
    return rows


def calibrate(sw: list[dict], max_flag_fpr: float) -> dict:
    """Pick the two thresholds on val.

    block: the lowest threshold at which NO benign listing is caught. A blocked
           listing earns nothing, so recall bought with blocked false positives
           is not recall worth having.
    flag:  the lowest threshold whose benign rate stays under max_flag_fpr. A
           flagged listing is sanitised and still sells, so it can be cheaper.
    """
    clean = [r for r in sw if r["benign_hits"] == 0]
    block = min((r["threshold"] for r in clean), default=0.999)
    ok = [r for r in sw if r["fpr_pct"] <= max_flag_fpr]
    flag = min((r["threshold"] for r in ok), default=block)
    return {
        "flag": float(min(flag, block)),
        "block": float(block),
        "calibrated_on": "val.jsonl",
        "rule": (
            f"block = lowest threshold with zero benign val listings caught; "
            f"flag = lowest threshold with benign val rate <= {max_flag_fpr}%. "
            "Calibrated on val, so the held-out rate may differ -- that gap is "
            "the honest cost of choosing a threshold at all."
        ),
    }


# ---------------------------------------------------------------- train
def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base-model", default=DEFAULT_BASE)
    ap.add_argument("--out", default=str(DEFAULT_OUT))
    ap.add_argument("--epochs", type=int, default=3)
    ap.add_argument("--lr", type=float, default=3e-5)
    ap.add_argument("--seed", type=int, default=20260824)
    ap.add_argument("--max-length", type=int, default=MAX_LENGTH)
    ap.add_argument("--stride", type=int, default=STRIDE)
    ap.add_argument("--token-budget", type=int, default=0, help="0 = pick by device")
    ap.add_argument("--eval-token-budget", type=int, default=0)
    ap.add_argument("--max-batch", type=int, default=32)
    ap.add_argument("--max-flag-fpr", type=float, default=1.0)
    ap.add_argument("--freeze-embeddings", dest="freeze", action="store_true", default=True)
    ap.add_argument("--no-freeze-embeddings", dest="freeze", action="store_false")
    ap.add_argument("--limit", type=int, default=0, help="debug: cap rows per split")
    a = ap.parse_args()

    random.seed(a.seed)
    np.random.seed(a.seed)
    torch.manual_seed(a.seed)

    device = "cuda" if torch.cuda.is_available() else "cpu"
    if not a.token_budget:
        a.token_budget = 16384 if device == "cuda" else 3072
    if not a.eval_token_budget:
        a.eval_token_budget = 16384 if device == "cuda" else 3072
    torch.set_num_threads(int(os.environ["OMP_NUM_THREADS"]))

    print(f"device={device}  base={a.base_model}  epochs={a.epochs}  "
          f"token_budget={a.token_budget}  freeze_embeddings={a.freeze}")

    train_rows = load_split("train")
    val_rows = load_split("val")
    if a.limit:
        train_rows, val_rows = train_rows[: a.limit], val_rows[: a.limit]
    print(f"train {len(train_rows)} listings | val {len(val_rows)} listings")

    tok = AutoTokenizer.from_pretrained(a.base_model)
    ids, labels, dropped, per_row = build_windows(
        train_rows, tok, a.max_length, a.stride)
    dist = Counter(labels)
    print(f"train windows {len(ids)}  {dict(dist)}  "
          f"({dropped} dropped as partial-span)  "
          f"max windows on one listing = {max(per_row)}")

    # deberta-v3-xsmall ships fp16 weights and transformers now honours the
    # checkpoint dtype. Half precision has no CPU kernel for this and the loss
    # would not even build, so pin fp32 explicitly on both devices. The keyword
    # was renamed in transformers 5; Colab may still be on 4.x.
    try:
        model = AutoModelForSequenceClassification.from_pretrained(
            a.base_model, num_labels=2, dtype=torch.float32)
    except TypeError:
        model = AutoModelForSequenceClassification.from_pretrained(
            a.base_model, num_labels=2, torch_dtype=torch.float32)
    if a.freeze:
        emb = model.base_model.embeddings
        for p in emb.parameters():
            p.requires_grad = False
    model.to(device)
    trainable = sum(p.numel() for p in model.parameters() if p.requires_grad)
    total = sum(p.numel() for p in model.parameters())
    print(f"parameters {total/1e6:.1f}M total, {trainable/1e6:.1f}M trainable")

    # Inverse-frequency weights, so the majority class does not simply win.
    w = torch.tensor(
        [len(labels) / (2 * max(dist[0], 1)), len(labels) / (2 * max(dist[1], 1))],
        dtype=torch.float, device=device)
    loss_fn = torch.nn.CrossEntropyLoss(weight=w)
    print(f"class weights {w.tolist()}")

    lengths = [len(x) for x in ids]
    rng = random.Random(a.seed)
    params = [p for p in model.parameters() if p.requires_grad]
    opt = torch.optim.AdamW(params, lr=a.lr, weight_decay=0.01)

    order = list(range(len(ids)))
    rng.shuffle(order)
    steps_per_epoch = len(batches_by_token_budget(
        order, lengths, a.token_budget, a.max_batch))
    total_steps = steps_per_epoch * a.epochs
    sched = get_linear_schedule_with_warmup(
        opt, int(0.1 * total_steps), total_steps)
    print(f"~{steps_per_epoch} steps/epoch, {total_steps} total")

    pad = tok.pad_token_id or 0
    history = []
    t_start = time.perf_counter()

    for epoch in range(1, a.epochs + 1):
        model.train()
        order = list(range(len(ids)))
        rng.shuffle(order)
        batches = batches_by_token_budget(order, lengths, a.token_budget, a.max_batch)
        running, seen, t0 = 0.0, 0, time.perf_counter()
        for bi, idx in enumerate(batches, 1):
            x, m, y = collate(idx, ids, labels, pad)
            out = model(input_ids=x.to(device), attention_mask=m.to(device))
            loss = loss_fn(out.logits, y.to(device))
            loss.backward()
            torch.nn.utils.clip_grad_norm_(params, 1.0)
            opt.step()
            sched.step()
            opt.zero_grad(set_to_none=True)
            running += float(loss) * len(idx)
            seen += len(idx)
            if bi % 50 == 0 or bi == len(batches):
                el = time.perf_counter() - t0
                print(f"  epoch {epoch} {bi}/{len(batches)}  loss {running/seen:.4f}"
                      f"  {seen/el:.1f} win/s  eta {(len(batches)-bi)*el/bi/60:.1f}m",
                      flush=True)

        val_scores = score_listings(val_rows, model, tok, device, a)
        val_labels = np.array([r["label"] for r in val_rows])
        sw = sweep(val_scores, val_labels)
        cal = calibrate(sw, a.max_flag_fpr)
        at_block = next(r for r in sw if r["threshold"] == cal["block"])
        at_flag = next(r for r in sw if r["threshold"] == cal["flag"])
        print(f"  epoch {epoch} VAL  block@{cal['block']}: recall {at_block['recall_pct']}%"
              f"  |  flag@{cal['flag']}: recall {at_flag['recall_pct']}%"
              f", benign {at_flag['benign_hits']}")
        history.append({"epoch": epoch, "train_loss": round(running / seen, 5),
                        "thresholds": cal, "at_block": at_block, "at_flag": at_flag})

    elapsed = time.perf_counter() - t_start

    # Final calibration on the finished model.
    val_scores = score_listings(val_rows, model, tok, device, a)
    val_labels = np.array([r["label"] for r in val_rows])
    sw = sweep(val_scores, val_labels)
    cal = calibrate(sw, a.max_flag_fpr)

    out_dir = Path(a.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    model.save_pretrained(out_dir)
    tok.save_pretrained(out_dir)
    (out_dir / "thresholds.json").write_text(json.dumps(cal, indent=2), encoding="utf-8")

    meta = {
        "trained_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "base_model": a.base_model,
        "device": device,
        "epochs": a.epochs,
        "lr": a.lr,
        "seed": a.seed,
        "freeze_embeddings": a.freeze,
        "window": {"max_length": a.max_length, "stride": a.stride,
                   "min_span_overlap": MIN_SPAN_OVERLAP},
        "train_listings": len(train_rows),
        "train_windows": len(ids),
        "window_label_counts": {str(k): v for k, v in dist.items()},
        "windows_dropped_partial_span": dropped,
        "parameters_total_m": round(total / 1e6, 2),
        "parameters_trainable_m": round(trainable / 1e6, 2),
        "minutes": round(elapsed / 60, 2),
        "history": history,
        "val_threshold_sweep": sw,
        "thresholds": cal,
        "held_out": "test.jsonl never opened during training (CLAUDE.md rule 3).",
    }
    (out_dir / "training_meta.json").write_text(json.dumps(meta, indent=2),
                                                encoding="utf-8")
    print(f"\nsaved to {out_dir}")
    print(f"thresholds  flag={cal['flag']}  block={cal['block']}")
    print(f"trained in {elapsed/60:.1f} min")
    return 0


if __name__ == "__main__":
    sys.exit(main())
