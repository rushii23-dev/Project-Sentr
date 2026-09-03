"""Structured audit records for every screening decision.

CLAUDE.md rule 6: no silent verdicts. Every listing that passes through Sentr
emits one of these, whether it was allowed, flagged or blocked. Track 2 asks
for an audit trail; this is it.

Records are appended to a JSONL file so they can be tailed live during the
demo and diffed afterwards. No database (rule 4).
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_LOG = ROOT / "eval" / "results" / "audit_log.jsonl"

VERDICTS = ("allow", "flag", "block")


@dataclass
class Trigger:
    """The specific thing that fired. Populated by the rule layer on Day 3;
    the classifier fills in `rule_id='classifier'` with no span."""

    layer: str                 # "rules" | "classifier" | "stub"
    rule_id: str               # e.g. "fake_role_marker"
    span: str = ""             # the exact offending text
    start: int = -1            # char offset into the screened text
    end: int = -1


@dataclass
class AuditRecord:
    listing_id: str
    verdict: str
    confidence: float
    decided_by: str                      # which layer produced the verdict
    triggers: list[Trigger] = field(default_factory=list)
    sanitized: bool = False
    text_before: str = ""
    text_after: str = ""
    latency_ms: float = 0.0
    # Split by layer, so the cost of the model is visible rather than buried in
    # a single figure. Across a catalogue the classifier is batched, and its
    # time is then the batch's cost divided by the listings in it -- the honest
    # per-listing figure for bulk screening. Screening one listing on its own
    # pays the whole thing, which is what single_item latency in the eval
    # reports separately.
    latency_rules_ms: float = 0.0
    latency_classifier_ms: float = 0.0
    sanitiser_notes: list[str] = field(default_factory=list)
    timestamp: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat(timespec="milliseconds")
    )
    sentr_version: str = "0.3.0-day4-rules+classifier"

    def __post_init__(self) -> None:
        if self.verdict not in VERDICTS:
            raise ValueError(f"verdict must be one of {VERDICTS}, got {self.verdict!r}")

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def diff(self) -> str:
        """Human-readable summary of what the sanitiser removed."""
        if not self.sanitized:
            return ""
        removed = len(self.text_before) - len(self.text_after)
        return f"-{removed} chars"


def write(record: AuditRecord, path: Path | str | None = None) -> None:
    """Append one record. Creates the log file and parent dirs on first use."""
    p = Path(path) if path else DEFAULT_LOG
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, "a", encoding="utf-8") as f:
        f.write(json.dumps(record.to_dict(), ensure_ascii=False) + "\n")


def read_all(path: Path | str | None = None) -> list[dict[str, Any]]:
    p = Path(path) if path else DEFAULT_LOG
    if not p.exists():
        return []
    with open(p, encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def clear(path: Path | str | None = None) -> None:
    """Reset the log. Used between demo runs so the screen shows only the
    current run's decisions."""
    p = Path(path) if path else DEFAULT_LOG
    if p.exists():
        os.remove(p)
