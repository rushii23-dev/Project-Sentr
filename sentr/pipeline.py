"""Sentr orchestration -- the single entry point the demo and API call.

DAY 2 STATUS: this is deliberately a STUB. `screen()` always returns `allow`.

Its only job today is to prove the plumbing: the demo agent calls Sentr, gets
a verdict object back, and an audit record lands on disk. Days 3-4 replace the
body of `_decide()` with the real rule layer and classifier without changing
this file's public shape:

    screen(listing)            -> Screened      (one listing)
    screen_catalog(listings)   -> list[Screened] (a whole catalogue)

Keeping the interface fixed now is the point of Day 2 -- integration pain gets
found today, not on Day 4 when there is no slack left.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Iterable

from . import audit
from .audit import AuditRecord, Trigger

# Verdicts that must never reach the buying agent's context.
WITHHELD = ("block",)


@dataclass
class Screened:
    """One listing after screening, plus why."""

    listing: dict[str, Any]      # the listing, sanitised if verdict == "flag"
    verdict: str                 # allow | flag | block
    confidence: float
    decided_by: str
    triggers: list[Trigger] = field(default_factory=list)
    record: AuditRecord | None = None

    @property
    def reaches_agent(self) -> bool:
        return self.verdict not in WITHHELD

    @property
    def agent_text(self) -> str:
        """Exactly the text the buying agent will read for this product."""
        return f"{self.listing.get('title', '')}\n{self.listing.get('description', '')}".strip()


def _screened_text(listing: dict[str, Any]) -> str:
    return f"{listing.get('title', '')}\n{listing.get('description', '')}".strip()


def _decide(text: str) -> tuple[str, float, str, list[Trigger]]:
    """DAY 2 STUB -- always allows.

    Day 3 replaces this with: rules first, then the classifier on whatever the
    rules let through. Returns (verdict, confidence, decided_by, triggers).
    """
    return "allow", 0.0, "stub", []


def screen(
    listing: dict[str, Any],
    *,
    enabled: bool = True,
    log_path: str | None = None,
) -> Screened:
    """Screen one listing.

    `enabled=False` is the demo's "Sentr off" switch: the listing passes
    through untouched and no audit record is written, which is precisely the
    status quo we are arguing against.
    """
    if not enabled:
        return Screened(
            listing=listing,
            verdict="allow",
            confidence=0.0,
            decided_by="sentr_disabled",
        )

    t0 = time.perf_counter()
    text = _screened_text(listing)
    verdict, confidence, decided_by, triggers = _decide(text)
    latency_ms = (time.perf_counter() - t0) * 1000

    out_listing = listing
    sanitized = False
    text_after = text
    if verdict == "flag":
        # Day 3 wires in the real sanitiser (layer 3).
        pass

    record = AuditRecord(
        listing_id=str(listing.get("item_id", listing.get("listing_id", "unknown"))),
        verdict=verdict,
        confidence=confidence,
        decided_by=decided_by,
        triggers=triggers,
        sanitized=sanitized,
        text_before=text,
        text_after=text_after,
        latency_ms=round(latency_ms, 3),
    )
    audit.write(record, log_path)

    return Screened(
        listing=out_listing,
        verdict=verdict,
        confidence=confidence,
        decided_by=decided_by,
        triggers=triggers,
        record=record,
    )


def screen_catalog(
    listings: Iterable[dict[str, Any]],
    *,
    enabled: bool = True,
    log_path: str | None = None,
) -> list[Screened]:
    return [screen(x, enabled=enabled, log_path=log_path) for x in listings]


def catalog_for_agent(screened: list[Screened]) -> list[dict[str, Any]]:
    """The catalogue as the buying agent will actually see it -- blocked
    listings removed, flagged listings in their sanitised form."""
    return [s.listing for s in screened if s.reaches_agent]
