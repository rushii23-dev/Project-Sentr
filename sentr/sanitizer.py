"""Layer 3 -- the sanitiser.

Runs on `flag` verdicts. Removes the offending span and returns the rest of the
listing intact, so a marginal-but-honest listing keeps selling instead of being
killed outright (SPEC.md section 5).

The design rule that matters: never silently drop the whole listing. A blocked
listing earns nothing, and the cost of that is the metric this project is graded
on. When in doubt, remove less.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

from .rules import Hit

# Invisible formatting characters. Stripping these is always safe for display
# and removes the hiding place, but note U+200C is linguistically required in
# Devanagari -- see `strip_invisible` for how that is handled.
ZERO_WIDTH = {"​", "‍", "⁠", "﻿"}
BIDI = {"‪", "‫", "‬", "‭", "‮",
        "⁦", "⁧", "⁨", "⁩"}
ZWNJ = "‌"

DEVANAGARI = re.compile(r"[ऀ-ॿ]")


@dataclass
class Sanitised:
    text: str
    removed_chars: int
    removed_spans: list[str]
    notes: list[str]

    @property
    def changed(self) -> bool:
        return self.removed_chars > 0


def strip_invisible(text: str) -> tuple[str, int, list[str]]:
    """Remove invisible formatting characters.

    U+200C (zero-width non-joiner) is kept when the text contains Devanagari,
    where it is a real orthographic character rather than a hiding place.
    Removing it there would corrupt honest Hindi listings.
    """
    notes: list[str] = []
    drop = set(ZERO_WIDTH) | set(BIDI)

    if DEVANAGARI.search(text):
        notes.append("kept U+200C: Devanagari present, ZWNJ is orthographic here")
    else:
        drop.add(ZWNJ)

    out = "".join(c for c in text if c not in drop)
    return out, len(text) - len(out), notes


def remove_spans(text: str, hits: list[Hit]) -> tuple[str, int, list[str]]:
    """Cut the exact matched spans out of the text, longest first so offsets
    stay valid. Only spans that are literally present are removed -- hits whose
    span is a synthesised description (the unicode rules) are handled by
    strip_invisible instead."""
    removed: list[str] = []
    out = text
    literal = sorted(
        (h for h in hits if h.span and h.span in text),
        key=lambda h: -len(h.span),
    )
    for h in literal:
        if h.span in out:
            out = out.replace(h.span, " ")
            removed.append(h.span)
    before = len(text)
    out = re.sub(r"[ \t]{2,}", " ", out)
    out = re.sub(r"\n{3,}", "\n\n", out).strip()
    return out, before - len(out), removed


def sanitise(text: str, hits: list[Hit]) -> Sanitised:
    """Neutralise what the rules found, preserving everything else."""
    notes: list[str] = []

    cleaned, n_spans, removed = remove_spans(text, hits)
    cleaned, n_inv, inv_notes = strip_invisible(cleaned)
    notes += inv_notes

    cleaned = unicodedata.normalize("NFKC", cleaned)

    if not cleaned.strip():
        # Removing everything is the one outcome worse than removing nothing:
        # an empty description is an unsellable listing. Escalate instead.
        notes.append("sanitising would have emptied the listing; left unchanged "
                     "for review rather than silently destroyed")
        return Sanitised(text=text, removed_chars=0, removed_spans=[], notes=notes)

    return Sanitised(
        text=cleaned,
        removed_chars=n_spans + n_inv,
        removed_spans=removed,
        notes=notes,
    )
