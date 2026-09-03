"""Build Sentr's full dataset: real benign listings, poisoned listings, and the
train/val/test split.

BENIGN -- real, never synthetic (CLAUDE.md section 6)
    amazon_india_30k.csv       28,010 usable Amazon.in listings (2019 crawl)
    flipkart_raw_subset.csv     1,000 raw Flipkart.com listings
    Raw seller text: original casing, punctuation, marketing copy, encoding
    artefacts. A lowercased, lemmatised corpus would understate the
    false-positive rate, which is the headline metric for this track.

POISONED -- a published or documented payload placed inside a REAL listing
    Two subsets, kept separate and reported separately, because they are not
    equally strong evidence:

      published : payloads copied verbatim from public research corpora. Nobody
                  on this project wrote them, so recall here is independent
                  evidence.
      authored  : commerce-framed instances of documented families, written by
                  this project because no public corpus of catalogue-shaped
                  injections exists. Recall here partly measures our own
                  imagination and must be reported with that caveat.

WHAT THIS DOES NOT DO (CLAUDE.md section 9)
    No payload is composed, mutated, paraphrased or optimised. Payload strings
    are copied verbatim from the two frozen fixture files. This module only
    chooses WHICH real listing carries WHICH payload at WHICH of three fixed,
    documented positions. There is no search and no feedback from any detector
    back into the data.

THE HELD-OUT SET
    20% is split off and written to test.jsonl before any detector work begins.
    Do not read, evaluate against, or tune on it until Day 5 (CLAUDE.md rule 3).

DAY 4 CORRECTION -- payload-disjoint splits
    The first version of this script split ROWS. Because a payload can be
    carried by more than one row, the same payload string landed in train and
    val at once: measured on the Day 3 files, 68% of val poisoned rows (and
    100% of the authored ones) used a payload that appeared verbatim in train.
    That did not affect Days 1-3 -- the rule layer is deterministic and learns
    nothing -- but it makes any classifier metric a measure of memorisation.

    Splits are now allocated over PAYLOADS first, before a single row is built,
    so cross-split overlap is impossible by construction. `verify_disjoint()`
    re-proves it on every run and writes the proof to split_integrity.json.
    The rebuild is blind: test.jsonl is regenerated from the seed, never read.

Usage:  python data/build_dataset.py
"""

from __future__ import annotations

import argparse
import hashlib
import json
import random
import re
from collections import Counter
from pathlib import Path

import pandas as pd
import yaml

ROOT = Path(__file__).resolve().parent.parent
RAW = ROOT / "data" / "raw"
FIXTURES = ROOT / "data" / "fixtures"
PROCESSED = ROOT / "data" / "processed"

MIN_DESC_CHARS = 40
MAX_DESC_CHARS = 4000

# Fixed, documented insertion points. These are properties of how a seller might
# lay out a listing, not an evasion search: the set is hardcoded, uniform, and
# never adjusted in response to detector performance.
POSITIONS = ("end", "middle", "start")


# --------------------------------------------------------------------- benign
def _clean(text: str) -> str:
    """Whitespace-normalise only. We deliberately do NOT strip case,
    punctuation, emoji or mojibake -- those are the signal."""
    text = str(text).replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"[ \t\f\v]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _row_id(source: str, native_id: str) -> str:
    h = hashlib.sha1(f"{source}:{native_id}".encode()).hexdigest()[:12]
    return f"{source}-{h}"


def load_amazon_india() -> pd.DataFrame:
    d = pd.read_csv(RAW / "amazon_india_30k.csv")
    return pd.DataFrame({
        "listing_id": [_row_id("amzin", x) for x in d["Uniq Id"]],
        "source": "amazon_in",
        "title": d["Product Title"].map(_clean),
        "description": d["Product Description"].map(_clean),
        "category": d["Category"].fillna("").map(_clean),
    })


def load_flipkart() -> pd.DataFrame:
    d = pd.read_csv(RAW / "flipkart_raw_subset.csv")
    cat = (d["product_category_tree"].astype(str)
           .str.extract(r'\["(.*?)(?:>>|")', expand=False).fillna("").str.strip())
    return pd.DataFrame({
        "listing_id": [_row_id("flip", x) for x in d["uniq_id"]],
        "source": "flipkart",
        "title": d["product_name"].map(_clean),
        "description": d["description"].map(_clean),
        "category": cat,
    })


def build_benign(n: int, seed: int) -> pd.DataFrame:
    df = pd.concat([load_flipkart(), load_amazon_india()], ignore_index=True)
    before = len(df)
    df = df[df["description"].notna()]
    df = df[df["description"].str.len().between(MIN_DESC_CHARS, MAX_DESC_CHARS)]
    df = df[df["title"].str.len() > 3]
    df = df.drop_duplicates(subset="description").drop_duplicates(subset="listing_id")
    print(f"  {before} raw rows -> {len(df)} usable after filter/dedupe")

    flip = df[df.source == "flipkart"]
    amzn = df[df.source == "amazon_in"]
    take = max(0, n - len(flip))
    if take < len(amzn):
        amzn = amzn.sample(n=take, random_state=seed)
    out = pd.concat([flip, amzn], ignore_index=True)
    return out.sample(frac=1.0, random_state=seed).reset_index(drop=True)


# -------------------------------------------------------------------- payloads
def load_payloads() -> list[dict]:
    """Both fixture files, flattened, each tagged with its provenance."""
    payloads: list[dict] = []

    pub = FIXTURES / "attack_patterns.yaml"
    if pub.exists():
        doc = yaml.safe_load(pub.read_text(encoding="utf-8"))
        for p in doc["patterns"]:
            if p["family"] == "other_published":
                continue  # unclassifiable chat prompts; not a meaningful family
            # cm-000 comes from this project's own CLAUDE.md, not from a public
            # corpus, so it counts as authored however it is filed.
            subset = "authored" if p["id"].startswith("cm-") else "published"
            payloads.append({
                "payload_id": p["id"], "family": p["family"],
                "subset": subset, "source": p["source"], "text": p["text"],
            })

    auth = FIXTURES / "commerce_patterns.yaml"
    if auth.exists():
        doc = yaml.safe_load(auth.read_text(encoding="utf-8"))
        for fam in doc["families"]:
            for i, text in enumerate(fam["patterns"]):
                payloads.append({
                    "payload_id": f"cm-{fam['family']}-{i:02d}",
                    "family": fam["family"], "subset": "authored",
                    "source": "authored_by_project", "text": text,
                })
    return payloads


def dedupe_by_text(payloads: list[dict]) -> tuple[list[dict], int]:
    """Two fixture entries can carry the same string under different ids.
    Deduplicating on TEXT rather than id is what makes the disjointness
    guarantee real -- an id split cleanly would still leak the string."""
    seen: set[str] = set()
    out, dropped = [], 0
    for p in payloads:
        if p["text"] in seen:
            dropped += 1
            continue
        seen.add(p["text"])
        out.append(p)
    return out, dropped


def containment_clusters(payloads: list[dict]) -> list[list[dict]]:
    """Group payloads where one string contains another.

    The public corpora contain near-duplicates -- the same instruction with a
    sentence bolted on. Splitting those apart would leak just as effectively as
    splitting an exact duplicate: a classifier that saw the longer one in
    training has already seen the shorter one. Clusters are allocated whole.
    """
    n = len(payloads)
    parent = list(range(n))

    def find(x: int) -> int:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a: int, b: int) -> None:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[max(ra, rb)] = min(ra, rb)

    # Compare short-to-long: a string can only be contained in a longer one.
    order = sorted(range(n), key=lambda i: len(payloads[i]["text"]))
    for pos, i in enumerate(order):
        ti = payloads[i]["text"]
        for j in order[pos + 1:]:
            if ti in payloads[j]["text"]:
                union(i, j)

    groups: dict[int, list[dict]] = {}
    for i in range(n):
        groups.setdefault(find(i), []).append(payloads[i])
    return [groups[k] for k in sorted(groups)]


def allocate_payloads(payloads: list[dict], seed: int,
                      val_frac: float, test_frac: float) -> dict[str, list[dict]]:
    """Assign every payload to exactly one split, before any row is built.

    The unit of allocation is a containment cluster, not a payload, so no
    string can reach two splits by equality or by being embedded in a longer
    variant. Allocation happens inside each (subset, family) bucket so all
    three splits keep the same mix. A family with only three clusters gives one
    to each split -- thin, and the eval reports distinct-payload counts
    alongside row counts so nobody mistakes 21 rows for 21 pieces of evidence.
    """
    rng = random.Random(seed + 1)
    buckets: dict[tuple, list[list[dict]]] = {}
    for cluster in containment_clusters(payloads):
        # A cluster takes the subset/family of its first member. Clusters that
        # straddle two families are rare and go wherever the first one points;
        # keeping them together matters more than the family label.
        head = cluster[0]
        buckets.setdefault((head["subset"], head["family"]), []).append(cluster)

    out: dict[str, list[dict]] = {"train": [], "val": [], "test": []}
    for key in sorted(buckets):
        g = buckets[key][:]
        rng.shuffle(g)
        n = len(g)
        if n < 3:
            # Too few to divide without emptying a split. Keep them for
            # training; the family then has no val/test evidence at all, which
            # the eval reports as such rather than hiding.
            out["train"] += [p for c in g for p in c]
            continue
        n_test = max(1, int(round(n * test_frac)))
        n_val = max(1, int(round(n * val_frac)))
        if n_test + n_val >= n:
            n_test = n_val = 1
        out["test"] += [p for c in g[:n_test] for p in c]
        out["val"] += [p for c in g[n_test:n_test + n_val] for p in c]
        out["train"] += [p for c in g[n_test + n_val:] for p in c]
    return out


def inject(description: str, payload: str, position: str) -> tuple[str, int]:
    """Place `payload` into `description`. Returns (new_text, start_offset)."""
    if position == "start":
        new = f"{payload}\n\n{description}"
        return new, 0
    if position == "end":
        new = f"{description}\n\n{payload}"
        return new, len(description) + 2
    # middle: after the sentence boundary nearest the midpoint
    bounds = [m.end() for m in re.finditer(r"[.!?]\s+", description)]
    if not bounds:
        new = f"{description}\n\n{payload}"
        return new, len(description) + 2
    cut = min(bounds, key=lambda b: abs(b - len(description) // 2))
    new = f"{description[:cut]}{payload} {description[cut:]}"
    return new, cut


def build_poisoned(carriers: pd.DataFrame, payloads: list[dict],
                   split: str) -> list[dict]:
    """One row per payload per insert position, for a single split.

    Every payload appears at all three documented positions, so per-position
    recall is measured over one payload set rather than three different ones.
    Each carrier listing is consumed at most once, and the carrier pools are
    already disjoint across splits, so no listing text crosses a split either.
    """
    carrier_rows = carriers.to_dict("records")
    need = len(payloads) * len(POSITIONS)
    if len(carrier_rows) < need:
        raise ValueError(f"{split}: need {need} carriers, have {len(carrier_rows)}")

    rows = []
    for k, (pay, pos) in enumerate(
        (p, pos) for p in payloads for pos in POSITIONS
    ):
        carrier = carrier_rows[k]
        new_desc, offset = inject(carrier["description"], pay["text"], pos)
        rows.append({
            "listing_id": f"{carrier['listing_id']}-pz{k:04d}",
            "source": carrier["source"],
            "title": carrier["title"],
            "description": new_desc,
            "category": carrier["category"],
            "label": 1,
            "attack_subset": pay["subset"],
            "attack_family": pay["family"],
            "payload_id": pay["payload_id"],
            "payload_source": pay["source"],
            "insert_position": pos,
            "injected_span": pay["text"],
            "span_start": offset,
        })
    return rows


# ------------------------------------------------------------------ integrity
def verify_disjoint(splits: dict[str, list[dict]]) -> dict:
    """Prove no payload string and no carrier listing is shared across splits.

    Runs at generation time on in-memory rows, so it never opens test.jsonl
    (CLAUDE.md rule 3). Substring containment is checked as well as equality:
    a val payload that is a prefix of a train payload would leak just as badly
    as an identical one. Raises on any violation -- a dataset that cannot prove
    this is not one we can report a classifier number from.
    """
    names = ["train", "val", "test"]
    spans = {s: {r["injected_span"] for r in splits[s] if r["label"] == 1}
             for s in names}
    # Carrier identity, with the -pzNNNN suffix stripped back off.
    listings = {s: {r["listing_id"].split("-pz")[0] for r in splits[s]}
                for s in names}

    report: dict = {"pairs": {}, "distinct_payloads": {s: len(spans[s]) for s in names}}
    for i, a in enumerate(names):
        for b in names[i + 1:]:
            shared = spans[a] & spans[b]
            contained = sorted(
                x for x in spans[a] if any(x != y and x in y for y in spans[b])
            ) + sorted(
                y for y in spans[b] if any(y != x and y in x for x in spans[a])
            )
            overlap_listings = listings[a] & listings[b]
            report["pairs"][f"{a}|{b}"] = {
                "shared_payload_strings": len(shared),
                "substring_containments": len(contained),
                "shared_listings": len(overlap_listings),
            }
            if shared or contained or overlap_listings:
                raise AssertionError(
                    f"split leak {a}|{b}: {len(shared)} identical payloads, "
                    f"{len(contained)} contained, {len(overlap_listings)} shared listings"
                )
    report["verified"] = True
    report["note"] = (
        "No payload string appears in more than one split, by equality or "
        "containment, and no source listing is shared. Checked in memory at "
        "generation time; test.jsonl is never read."
    )
    return report


def write_jsonl(path: Path, rows: list[dict]) -> None:
    with open(path, "w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")


def summarise(name: str, rows: list[dict]) -> dict:
    lab = Counter(r["label"] for r in rows)
    fam = Counter(r["attack_family"] for r in rows if r["label"] == 1)
    sub = Counter(r["attack_subset"] for r in rows if r["label"] == 1)
    # Rows are cheap; distinct payloads are the evidence. Report both, always.
    pay_fam: dict[str, set] = {}
    pay_sub: dict[str, set] = {}
    for r in rows:
        if r["label"] == 1:
            pay_fam.setdefault(r["attack_family"], set()).add(r["injected_span"])
            pay_sub.setdefault(r["attack_subset"], set()).add(r["injected_span"])
    print(f"  {name:6} n={len(rows):5}  benign={lab[0]:5}  poisoned={lab[1]:4}  "
          f"{ {k: f'{v} rows / {len(pay_sub[k])} payloads' for k, v in sorted(sub.items())} }")
    if fam:
        print(f"         families: "
              f"{ {k: f'{v}r/{len(pay_fam[k])}p' for k, v in sorted(fam.items(), key=lambda kv: -kv[1])} }")
    return {
        "n": len(rows), "benign": lab[0], "poisoned": lab[1],
        "by_subset": dict(sub), "by_family": dict(fam),
        "distinct_payloads_by_subset": {k: len(v) for k, v in sorted(pay_sub.items())},
        "distinct_payloads_by_family": {k: len(v) for k, v in sorted(pay_fam.items())},
    }


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--benign", type=int, default=6000)
    p.add_argument("--seed", type=int, default=20260824)
    p.add_argument("--val-frac", type=float, default=0.20)
    p.add_argument("--test-frac", type=float, default=0.20)
    a = p.parse_args()

    PROCESSED.mkdir(parents=True, exist_ok=True)
    rng = random.Random(a.seed + 3)
    names = ["train", "val", "test"]

    print("1. payloads")
    payloads, dropped = dedupe_by_text(load_payloads())
    by_sub = Counter(x["subset"] for x in payloads)
    by_fam = Counter(x["family"] for x in payloads)
    print(f"  {len(payloads)} distinct payload strings  {dict(by_sub)}"
          f"  ({dropped} dropped as duplicate text)")
    for fam, n in sorted(by_fam.items(), key=lambda kv: -kv[1]):
        print(f"    {fam:22} {n:4}")

    print("2. payload allocation -- splits assigned BEFORE any row is built")
    alloc = allocate_payloads(payloads, a.seed, a.val_frac, a.test_frac)
    n_carriers = {s: len(alloc[s]) * len(POSITIONS) for s in names}
    for s in names:
        print(f"  {s:6} {len(alloc[s]):4} payloads -> {n_carriers[s]:5} poisoned rows")

    print("3. benign listings (real)")
    pool = build_benign(a.benign + sum(n_carriers.values()), a.seed)
    benign_pool = pool.iloc[:a.benign].reset_index(drop=True)
    carrier_pool = pool.iloc[a.benign:].reset_index(drop=True)
    print(f"  {len(benign_pool)} benign + {len(carrier_pool)} held aside as "
          f"poison carriers (carriers never appear as benign rows)")

    # Benign and carrier listings are both split by slicing an already-shuffled
    # pool, so a source listing belongs to exactly one split whichever class it
    # ends up in.
    n_test_b = int(round(a.benign * a.test_frac))
    n_val_b = int(round(a.benign * a.val_frac))
    benign_at = {"test": (0, n_test_b),
                 "val": (n_test_b, n_test_b + n_val_b),
                 "train": (n_test_b + n_val_b, a.benign)}
    cursor = 0
    carrier_at = {}
    for s in names:
        carrier_at[s] = (cursor, cursor + n_carriers[s])
        cursor += n_carriers[s]

    print("4. poisoned listings")
    splits: dict[str, list[dict]] = {}
    for s in names:
        lo, hi = benign_at[s]
        benign_rows = benign_pool.iloc[lo:hi].to_dict("records")
        for r in benign_rows:
            r["label"] = 0
            r["attack_subset"] = "-"
            r["attack_family"] = "-"

        clo, chi = carrier_at[s]
        poisoned = build_poisoned(
            carrier_pool.iloc[clo:chi].reset_index(drop=True), alloc[s], s)

        rows = benign_rows + poisoned
        rng.shuffle(rows)
        splits[s] = rows
        print(f"  {s:6} {len(poisoned):5} poisoned rows built")

    print("5. integrity")
    integrity = verify_disjoint(splits)
    for pair, r in integrity["pairs"].items():
        print(f"  {pair:12} shared payloads={r['shared_payload_strings']}  "
              f"contained={r['substring_containments']}  "
              f"shared listings={r['shared_listings']}")
    print("  VERIFIED: no payload string or source listing crosses a split")

    print("6. splits")
    stats = {s: summarise(s, splits[s]) for s in names}

    for s in names:
        write_jsonl(PROCESSED / f"{s}.jsonl", splits[s])

    meta = {
        "seed": a.seed,
        "benign_source": ["amazon_india_30k.csv", "flipkart_raw_subset.csv"],
        "payload_fixtures": ["attack_patterns.yaml", "commerce_patterns.yaml"],
        "distinct_payloads": len(payloads),
        "duplicate_payload_texts_dropped": dropped,
        "payloads_by_subset": dict(by_sub),
        "payloads_by_family": dict(by_fam),
        "insert_positions": list(POSITIONS),
        "rows_per_payload": len(POSITIONS),
        "payload_split": "disjoint -- allocated per (subset, family) before rows exist",
        "payloads_per_split": {s: len(alloc[s]) for s in names},
        "split_integrity": integrity,
        "splits": stats,
        "families_authored_only": sorted(
            {f for f, _ in by_fam.items()}
            - {x["family"] for x in payloads if x["subset"] == "published"}
        ),
        "day4_correction": (
            "Splits are now payload-disjoint. The previous row-level split put "
            "the same payload string in train and val for 68% of val poisoned "
            "rows, which would have made any classifier metric a measure of "
            "memorisation. Rules-layer results from Day 3 predate this rebuild "
            "and were re-run against it."
        ),
        "HELD_OUT": (
            "test.jsonl is the held-out set. Do not read, evaluate against, or "
            "tune on it until Day 5, then evaluate ONCE (CLAUDE.md section 6)."
        ),
    }
    (PROCESSED / "dataset_meta.json").write_text(json.dumps(meta, indent=2),
                                                 encoding="utf-8")
    (PROCESSED / "split_integrity.json").write_text(json.dumps(integrity, indent=2),
                                                    encoding="utf-8")

    print(f"\nwrote train/val/test + dataset_meta.json + split_integrity.json -> {PROCESSED}")
    print("test.jsonl is LOCKED until Day 5.")


if __name__ == "__main__":
    main()
