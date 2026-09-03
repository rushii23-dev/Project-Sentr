"""Layer 2 -- the fine-tuned classifier.

Runs only on what the rules allowed, so the fast path stays fast (CLAUDE.md
section 5). Its job is the ~13% of attacks the rules miss: paraphrased
instruction overrides and authorisation claims with no keyword to grab.

WHY THIS CHUNKS
    An off-the-shelf detector reads the first 512 tokens and stops. The ACP
    listing spec allows 5,000 characters of description, so on a long listing
    an attacker just writes the payload at the end and the model never sees it.
    The rule layer has no such limit, which makes truncation a layer-2-only
    hole -- and one worth closing, because it is trivially exploitable and
    completely invisible in an aggregate accuracy number.

    So every listing is tokenised into overlapping windows and scored window by
    window; the listing's score is the maximum over its windows. Training uses
    the same windows, with each window labelled by whether it actually overlaps
    the injected span, so the model is never asked to call a window malicious
    on evidence that was truncated away.

NO MODEL, NO CRASH
    If the weights are absent -- a fresh clone, or the demo machine -- this
    reports `available = False` and the pipeline runs on rules alone rather
    than failing. The audit record says which layers actually ran, so a
    degraded run is visible rather than silent.
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_MODEL_DIR = ROOT / "models" / "sentr-classifier"

# Window geometry. 384 tokens covers a typical listing whole (p50 is ~90
# tokens); the 64-token overlap means a payload landing on a window boundary is
# still seen intact by one of the two windows.
MAX_LENGTH = 384
STRIDE = 64

# Batches are built to a token budget, not a row count: attention is O(n^2) in
# activation memory and this machine has under a gigabyte free. Learned on Day 1
# when a flat batch of 16 x 512 segfaulted the process (see eval/baseline.py).
TOKEN_BUDGET = 2048
MAX_BATCH = 32

# Used only when the model directory ships no calibration file. Training writes
# real ones, chosen on val.
FALLBACK_THRESHOLDS = {"flag": 0.50, "block": 0.95}


def _prepare_env() -> None:
    """Must run before torch is imported anywhere in the process.

    torch and numpy each ship an OpenMP runtime on Windows; loading both
    segfaults, and the thread count has to be fixed in the environment because
    torch.set_num_threads() afterwards is too late. This module is imported by
    pipeline.py before any torch import, which is what makes it the right place
    for this. torch itself is imported lazily, inside _ensure_loaded().
    """
    os.environ.setdefault("KMP_DUPLICATE_LIB_OK", "TRUE")
    os.environ.setdefault("OMP_NUM_THREADS", os.environ.get("SENTR_THREADS", "6"))
    os.environ.setdefault("HF_HOME", str(ROOT / ".cache" / "huggingface"))


_prepare_env()


@dataclass
class Chunk:
    """One scored window, with the characters it covers."""

    index: int
    start: int          # char offset into the screened text
    end: int
    score: float
    text: str = ""

    def truncated(self, limit: int = 160) -> str:
        s = self.text.replace("\n", " ")
        return s if len(s) <= limit else s[: limit - 1] + "…"


@dataclass
class ClassifierResult:
    score: float
    chunks: list[Chunk] = field(default_factory=list)
    available: bool = True
    reason: str = ""            # why it did not run, when it did not

    @property
    def top(self) -> Chunk | None:
        return max(self.chunks, key=lambda c: c.score) if self.chunks else None

    @property
    def n_chunks(self) -> int:
        return len(self.chunks)


class Classifier:
    """Lazy-loading wrapper around the fine-tuned sequence classifier."""

    def __init__(
        self,
        model_dir: str | Path | None = None,
        *,
        max_length: int = MAX_LENGTH,
        stride: int = STRIDE,
        token_budget: int = TOKEN_BUDGET,
        max_batch: int = MAX_BATCH,
    ) -> None:
        self.model_dir = Path(
            model_dir or os.environ.get("SENTR_CLASSIFIER_DIR") or DEFAULT_MODEL_DIR
        )
        self.max_length = max_length
        self.stride = stride
        self.token_budget = token_budget
        self.max_batch = max_batch
        self._tok = None
        self._mdl = None
        self._torch = None
        self.thresholds = self._read_thresholds()
        self.meta = self._read_json("training_meta.json") or {}

    # ------------------------------------------------------------- loading
    def _read_json(self, name: str) -> dict | None:
        p = self.model_dir / name
        if not p.exists():
            return None
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            return None

    def _read_thresholds(self) -> dict:
        doc = self._read_json("thresholds.json") or {}
        return {
            "flag": float(doc.get("flag", FALLBACK_THRESHOLDS["flag"])),
            "block": float(doc.get("block", FALLBACK_THRESHOLDS["block"])),
            "calibrated_on": doc.get("calibrated_on", "defaults (no calibration file)"),
        }

    @property
    def available(self) -> bool:
        return (self.model_dir / "config.json").exists()

    def _ensure_loaded(self) -> None:
        if self._mdl is not None:
            return
        import torch  # noqa: E402  -- deliberately lazy; see _prepare_env
        import numpy  # noqa: F401,E402  -- torch first, always
        from transformers import AutoModelForSequenceClassification, AutoTokenizer

        torch.set_num_threads(int(os.environ["OMP_NUM_THREADS"]))
        self._torch = torch
        self._tok = AutoTokenizer.from_pretrained(str(self.model_dir))
        self._mdl = AutoModelForSequenceClassification.from_pretrained(str(self.model_dir))
        self._mdl.eval()

    def warm(self) -> bool:
        """Load the weights now rather than on the first scored listing.

        Loading is seconds of work on a memory-starved machine. Paying it
        inside the first screening call would bill one unlucky listing for the
        whole thing and make the latency numbers meaningless -- callers that
        measure should warm first.
        """
        if not self.available:
            return False
        self._ensure_loaded()
        return True

    # ------------------------------------------------------------- scoring
    def windows(self, text: str) -> list[tuple[list[int], int, int]]:
        """Split `text` into overlapping windows.

        Returns (token_ids, char_start, char_end) per window. A window whose
        offsets are all special tokens is dropped -- it carries no text.
        """
        self._ensure_loaded()
        enc = self._tok(
            text,
            truncation=True,
            max_length=self.max_length,
            stride=self.stride,
            return_overflowing_tokens=True,
            return_offsets_mapping=True,
            add_special_tokens=True,
        )
        out = []
        for ids, offsets in zip(enc["input_ids"], enc["offset_mapping"]):
            real = [o for o in offsets if o != (0, 0)]
            if not real:
                continue
            out.append((ids, real[0][0], real[-1][1]))
        return out

    def score_texts(self, texts: list[str]) -> list[ClassifierResult]:
        """Score many listings. Windows from every listing are batched
        together, so a catalogue of short listings does not pay for the one
        long one in it."""
        if not self.available:
            return [
                ClassifierResult(score=0.0, available=False,
                                 reason=f"no model at {self.model_dir}")
                for _ in texts
            ]
        self._ensure_loaded()
        torch = self._torch

        flat: list[tuple[int, int, list[int], int, int]] = []
        for ti, t in enumerate(texts):
            for wi, (ids, s, e) in enumerate(self.windows(t)):
                flat.append((ti, wi, ids, s, e))
        if not flat:
            return [ClassifierResult(score=0.0) for _ in texts]

        order = sorted(range(len(flat)), key=lambda i: len(flat[i][2]))
        batches: list[list[int]] = []
        cur: list[int] = []
        for i in order:
            peak = (len(cur) + 1) * max([len(flat[i][2])] + [len(flat[j][2]) for j in cur])
            if cur and (peak > self.token_budget or len(cur) >= self.max_batch):
                batches.append(cur)
                cur = []
            cur.append(i)
        if cur:
            batches.append(cur)

        pad = self._tok.pad_token_id or 0
        scores = [0.0] * len(flat)
        for idx in batches:
            width = max(len(flat[i][2]) for i in idx)
            ids = torch.full((len(idx), width), pad, dtype=torch.long)
            mask = torch.zeros((len(idx), width), dtype=torch.long)
            for k, i in enumerate(idx):
                w = flat[i][2]
                ids[k, : len(w)] = torch.tensor(w, dtype=torch.long)
                mask[k, : len(w)] = 1
            with torch.no_grad():
                logits = self._mdl(input_ids=ids, attention_mask=mask).logits
                p = logits.softmax(-1)[:, 1]
            for k, i in enumerate(idx):
                scores[i] = float(p[k])

        results = [ClassifierResult(score=0.0) for _ in texts]
        for i, (ti, wi, _ids, s, e) in enumerate(flat):
            results[ti].chunks.append(
                Chunk(index=wi, start=s, end=e, score=round(scores[i], 6),
                      text=texts[ti][s:e])
            )
        for r in results:
            r.score = round(max((c.score for c in r.chunks), default=0.0), 6)
        return results

    def score(self, text: str) -> ClassifierResult:
        return self.score_texts([text])[0]

    # ------------------------------------------------------------ verdicts
    def verdict(self, score: float) -> str:
        if score >= self.thresholds["block"]:
            return "block"
        if score >= self.thresholds["flag"]:
            return "flag"
        return "allow"


_SHARED: Classifier | None = None


def shared() -> Classifier:
    """One process-wide instance. The weights are ~280MB and this machine has
    under a gigabyte free -- loading them per request is not an option."""
    global _SHARED
    if _SHARED is None:
        _SHARED = Classifier()
    return _SHARED


def reset_shared() -> None:
    """Drop the cached instance. Used by the eval scripts when they point the
    classifier at a different checkpoint."""
    global _SHARED
    _SHARED = None
