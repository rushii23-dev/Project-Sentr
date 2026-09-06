"""Sentr orchestration -- the single entry point the demo and API call.

    screen(listing)            -> Screened       (one listing)
    screen_catalog(listings)   -> list[Screened] (a whole catalogue)

DAY 4 STATUS: all three layers are live.

    layer 1  rules       deterministic, sub-millisecond, explainable
    layer 2  classifier  runs ONLY on what the rules allowed
    layer 3  sanitiser   runs on flag verdicts

The ordering is the design (SPEC.md section 5). Rules settle the obvious
cases for free and hand the classifier a much smaller pile, so the expensive
layer never sees most of the catalogue. Across a catalogue the classifier is
also batched: every listing the rules allowed is scored in one pass, because
per-listing model calls waste most of their time on padding.

If the classifier weights are absent the pipeline runs on rules alone and every
audit record says so. A degraded run is visible, never silent.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Iterable

from . import audit, classifier as clf, rules, sanitizer
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


def _rule_stage(text: str):
    """Layer 1. Returns (result, triggers, rule_latency_ms)."""
    t0 = time.perf_counter()
    result = rules.scan(text)
    latency = (time.perf_counter() - t0) * 1000
    triggers = [
        Trigger(layer="rules", rule_id=h.rule_id, span=h.truncated(),
                start=h.start, end=h.end)
        for h in result.hits
    ]
    return result, triggers, latency


def _classifier_trigger(res: "clf.ClassifierResult") -> list[Trigger]:
    """The window that scored highest, as evidence.

    The classifier cannot localise the way a regex can -- it scores a window,
    not a span -- so this is evidence, not something the sanitiser cuts. Cutting
    a 384-token window out of an honest listing would do more damage than the
    flag it came from.
    """
    top = res.top
    if top is None:
        return []
    return [Trigger(layer="classifier", rule_id="classifier",
                    span=f"score {top.score:.3f} in window {top.index}: "
                         f"{top.truncated(120)}",
                    start=top.start, end=top.end)]


def screen_catalog(
    listings: Iterable[dict[str, Any]],
    *,
    enabled: bool = True,
    log_path: str | None = None,
    use_classifier: bool = True,
) -> list[Screened]:
    """Screen a whole catalogue. Rules per listing, classifier in one batch."""
    listings = list(listings)

    if not enabled:
        # The demo's "Sentr off" switch: the status quo we are arguing against.
        return [
            Screened(listing=x, verdict="allow", confidence=0.0,
                     decided_by="sentr_disabled")
            for x in listings
        ]

    texts = [_screened_text(x) for x in listings]
    stages = [_rule_stage(t) for t in texts]

    # Only what the rules allowed reaches layer 2.
    model = clf.shared() if use_classifier else None
    pending = [i for i, (r, _t, _l) in enumerate(stages) if r.verdict == "allow"]
    scores: dict[int, "clf.ClassifierResult"] = {}
    clf_latency = 0.0
    if model is not None and model.available and pending:
        model.warm()          # keep weight loading out of the measured window
        t0 = time.perf_counter()
        out = model.score_texts([texts[i] for i in pending])
        clf_latency = (time.perf_counter() - t0) * 1000 / len(pending)
        scores = dict(zip(pending, out))

    screened: list[Screened] = []
    for i, listing in enumerate(listings):
        result, triggers, rule_ms = stages[i]
        verdict, confidence = result.verdict, result.score
        hits = result.hits
        decided_by = "rules" if result.hits else "rules_clean"
        this_clf_ms = 0.0

        cres = scores.get(i)
        if cres is not None:
            this_clf_ms = clf_latency
            cverdict = model.verdict(cres.score)
            if cverdict != "allow":
                verdict = cverdict
                confidence = cres.score
                decided_by = "classifier"
                triggers = triggers + _classifier_trigger(cres)
            else:
                decided_by = "rules+classifier_clean"
        elif model is not None and not model.available and verdict == "allow":
            # Not a fault. The classifier slot is empty by decision -- see
            # eval/results/layer2_decision.json -- so say that rather than
            # implying something failed to load.
            decided_by = "rules_clean_no_classifier"

        screened.append(
            _finish(listing, texts[i], verdict, confidence, decided_by,
                    triggers, hits, rule_ms, this_clf_ms, log_path)
        )
    return screened


def _finish(listing, text, verdict, confidence, decided_by, triggers, hits,
            rule_ms, clf_ms, log_path) -> Screened:
    """Sanitise if flagged, write the audit record, return the result."""
    out_listing = listing
    sanitized = False
    text_after = text
    notes: list[str] = []

    if verdict == "flag":
        cleaned = sanitizer.sanitise(text, hits)
        notes = cleaned.notes
        if cleaned.changed:
            sanitized = True
            text_after = cleaned.text
            # Split the cleaned text back into title and description on the
            # same boundary screen() joined them on.
            head, _, tail = cleaned.text.partition("\n")
            out_listing = dict(listing)
            out_listing["title"] = head
            out_listing["description"] = tail

    record = AuditRecord(
        listing_id=str(listing.get("item_id", listing.get("listing_id", "unknown"))),
        verdict=verdict,
        confidence=confidence,
        decided_by=decided_by,
        triggers=triggers,
        sanitized=sanitized,
        text_before=text,
        text_after=text_after,
        latency_ms=round(rule_ms + clf_ms, 3),
        latency_rules_ms=round(rule_ms, 3),
        latency_classifier_ms=round(clf_ms, 3),
    )
    if notes:
        record.sanitiser_notes = notes
    audit.write(record, log_path)

    return Screened(
        listing=out_listing,
        verdict=verdict,
        confidence=confidence,
        decided_by=decided_by,
        triggers=triggers,
        record=record,
    )


def screen(
    listing: dict[str, Any],
    *,
    enabled: bool = True,
    log_path: str | None = None,
    use_classifier: bool = True,
) -> Screened:
    """Screen one listing.

    `enabled=False` is the demo's "Sentr off" switch: the listing passes
    through untouched and no audit record is written.
    """
    return screen_catalog([listing], enabled=enabled, log_path=log_path,
                          use_classifier=use_classifier)[0]


def catalog_for_agent(screened: list[Screened]) -> list[dict[str, Any]]:
    """The catalogue as the buying agent will actually see it -- blocked
    listings removed, flagged listings in their sanitised form."""
    return [s.listing for s in screened if s.reaches_agent]
