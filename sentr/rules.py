"""Layer 1 -- the rule engine.

This module contains NO rules. It loads sentr/rules.yaml and applies whatever is
declared there (CLAUDE.md rule 5), so the detection logic can be audited and
extended without touching Python.

Three rule types are supported:

    regex           a pattern, case-insensitive by default
    hidden_unicode  invisible codepoints that survive into the token stream
    base64_text     a base64 (or hex) run that actually DECODES to readable
                    text -- the decode check is what keeps model numbers and
                    SKUs from firing the rule

Every hit carries the exact span that triggered it, so the audit record can
point at the offending characters rather than just asserting a verdict.
"""

from __future__ import annotations

import base64
import binascii
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parent.parent
RULES_FILE = Path(__file__).resolve().parent / "rules.yaml"

_B64_RUN = re.compile(r"[A-Za-z0-9+/]{16,}={0,2}")
_HEX_RUN = re.compile(r"\b(?:[0-9a-fA-F]{2}){12,}\b")


@dataclass
class Hit:
    rule_id: str
    family: str
    severity: str          # "block" | "flag"
    weight: float
    span: str
    start: int
    end: int

    def truncated(self, limit: int = 160) -> str:
        s = self.span.replace("\n", " ")
        return s if len(s) <= limit else s[: limit - 1] + "…"


@dataclass
class RuleResult:
    verdict: str                 # allow | flag | block
    score: float
    hits: list[Hit]

    @property
    def families(self) -> list[str]:
        seen, out = set(), []
        for h in self.hits:
            if h.family not in seen:
                seen.add(h.family)
                out.append(h.family)
        return out


def _printable_ratio(b: bytes) -> float:
    if not b:
        return 0.0
    ok = sum(1 for c in b if 32 <= c < 127 or c in (9, 10, 13))
    return ok / len(b)


@lru_cache(maxsize=4)
def load_rules(path: str | None = None) -> dict[str, Any]:
    p = Path(path) if path else RULES_FILE
    doc = yaml.safe_load(p.read_text(encoding="utf-8"))

    for rule in doc["rules"]:
        if rule.get("type", "regex") == "regex":
            flags = 0 if rule.get("case_sensitive") else re.IGNORECASE
            flags |= re.MULTILINE
            rule["_compiled"] = [re.compile(pat, flags) for pat in rule["patterns"]]
    return doc


def _scan_regex(rule: dict, text: str) -> list[Hit]:
    hits = []
    for rx in rule["_compiled"]:
        for m in rx.finditer(text):
            hits.append(Hit(rule["id"], rule["family"], rule["severity"],
                            float(rule["weight"]), m.group(0), m.start(), m.end()))
    return hits


def _scan_hidden_unicode(rule: dict, text: str) -> list[Hit]:
    chars = set(rule["codepoints"])
    start_at = 1 if rule.get("skip_leading") else 0
    positions = [i for i, c in enumerate(text) if c in chars and i >= start_at]
    if len(positions) < int(rule.get("min_count", 1)):
        return []
    # Report one hit with readable context, not one per invisible character.
    i = positions[0]
    lo, hi = max(0, i - 30), min(len(text), i + 30)
    names = sorted({f"U+{ord(text[p]):04X}" for p in positions})
    span = f"{len(positions)} invisible char(s) {' '.join(names[:4])} near: {text[lo:hi]!r}"
    return [Hit(rule["id"], rule["family"], rule["severity"],
                float(rule["weight"]), span, i, i + 1)]


def _scan_encoded(rule: dict, text: str, kind: str) -> list[Hit]:
    runs = _B64_RUN if kind == "base64_text" else _HEX_RUN
    min_len = int(rule.get("min_len", 24))
    min_ratio = float(rule.get("min_printable_ratio", 0.9))
    min_decoded = int(rule.get("min_decoded_len", 8))

    hits = []
    for m in runs.finditer(text):
        blob = m.group(0)
        if len(blob) < min_len:
            continue
        try:
            if kind == "base64_text":
                raw = base64.b64decode(blob + "=" * (-len(blob) % 4), validate=False)
            else:
                raw = binascii.unhexlify(blob[: len(blob) // 2 * 2])
        except Exception:
            continue
        if len(raw) < min_decoded or _printable_ratio(raw) < min_ratio:
            continue
        decoded = raw.decode("ascii", "replace")
        # A run of readable text that decodes to more readable text is the
        # signal. Require at least one space, so hex colour codes and part
        # numbers that happen to decode do not fire.
        if " " not in decoded:
            continue
        span = f"{blob[:60]}... decodes to: {decoded[:90]!r}"
        hits.append(Hit(rule["id"], rule["family"], rule["severity"],
                        float(rule["weight"]), span, m.start(), m.end()))
    return hits


def scan(text: str, rules_path: str | None = None) -> RuleResult:
    """Apply every rule to `text` and return a verdict with its evidence."""
    doc = load_rules(rules_path)
    hits: list[Hit] = []

    for rule in doc["rules"]:
        kind = rule.get("type", "regex")
        if kind == "regex":
            hits += _scan_regex(rule, text)
        elif kind == "hidden_unicode":
            hits += _scan_hidden_unicode(rule, text)
        elif kind in ("base64_text", "hex_text"):
            hits += _scan_encoded(rule, text, kind)

    # Two patterns inside one rule can match the same characters. Reporting the
    # span twice tells a reviewer nothing and clutters the audit record, which
    # is the artefact the track actually asks to see.
    seen: set[tuple[str, int, int]] = set()
    deduped = []
    for h in hits:
        key = (h.rule_id, h.start, h.end)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(h)
    hits = deduped

    # Score is the strongest single rule plus a small bump per additional
    # distinct rule, so several weak signals can add up without a long listing
    # accumulating score just for being long.
    if hits:
        by_rule = {h.rule_id: h.weight for h in hits}
        score = max(by_rule.values()) + 0.05 * (len(by_rule) - 1)
        score = min(score, 1.0)
    else:
        score = 0.0

    th = doc["thresholds"]
    if any(h.severity == "block" for h in hits) or score >= th["block"]:
        verdict = "block"
    elif hits or score >= th["flag"]:
        verdict = "flag"
    else:
        verdict = "allow"

    hits.sort(key=lambda h: (-h.weight, h.start))
    return RuleResult(verdict=verdict, score=round(score, 3), hits=hits)
