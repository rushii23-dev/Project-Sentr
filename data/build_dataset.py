"""Build Sentr's benign listing corpus from real, public e-commerce data.

Day 1 scope: benign listings only. The poisoned set and the train/val/test
split are added on Day 3 (see CLAUDE.md section 11).

Sources -- both public HuggingFace mirrors of published Kaggle dumps:

  amazon_india_30k.csv       28,010 usable Amazon.in listings (2019 crawl)
  flipkart_raw_subset.csv     1,000 raw Flipkart.com listings

Both are RAW seller text: original casing, punctuation, marketing copy,
encoding artefacts. That messiness is the point -- a corpus that has been
lowercased and lemmatised would understate the false-positive rate, which
is the headline metric for this track.
"""

import argparse
import hashlib
import json
import re
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
RAW = ROOT / "data" / "raw"
PROCESSED = ROOT / "data" / "processed"

MIN_DESC_CHARS = 40
MAX_DESC_CHARS = 4000


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
    out = pd.DataFrame(
        {
            "listing_id": [_row_id("amzin", x) for x in d["Uniq Id"]],
            "source": "amazon_in",
            "title": d["Product Title"].map(_clean),
            "description": d["Product Description"].map(_clean),
            "category": d["Category"].fillna("").map(_clean),
            "price_inr": pd.to_numeric(d["Price"], errors="coerce"),
        }
    )
    return out


def load_flipkart() -> pd.DataFrame:
    d = pd.read_csv(RAW / "flipkart_raw_subset.csv")
    cat = (
        d["product_category_tree"]
        .astype(str)
        .str.extract(r'\["(.*?)(?:>>|")', expand=False)
        .fillna("")
        .str.strip()
    )
    out = pd.DataFrame(
        {
            "listing_id": [_row_id("flip", x) for x in d["uniq_id"]],
            "source": "flipkart",
            "title": d["product_name"].map(_clean),
            "description": d["description"].map(_clean),
            "category": cat,
            "price_inr": pd.to_numeric(d["discounted_price"], errors="coerce"),
        }
    )
    return out


def build(n: int, seed: int) -> pd.DataFrame:
    frames = [load_flipkart(), load_amazon_india()]
    df = pd.concat(frames, ignore_index=True)
    before = len(df)

    df = df[df["description"].notna()]
    df = df[df["description"].str.len().between(MIN_DESC_CHARS, MAX_DESC_CHARS)]
    df = df[df["title"].str.len() > 3]
    df = df.drop_duplicates(subset="description")
    df = df.drop_duplicates(subset="listing_id")
    print(f"  {before} raw rows -> {len(df)} usable after filter/dedupe")

    # Take every Flipkart row (only ~1k, and it is the most stylistically
    # distinct source), then top up from Amazon India to reach n.
    flip = df[df.source == "flipkart"]
    amzn = df[df.source == "amazon_in"]
    take_amzn = max(0, n - len(flip))
    if take_amzn < len(amzn):
        amzn = amzn.sample(n=take_amzn, random_state=seed)
    out = pd.concat([flip, amzn], ignore_index=True)
    out = out.sample(frac=1.0, random_state=seed).reset_index(drop=True)
    out["label"] = 0  # 0 = benign. Poisoned rows get label 1 on Day 3.
    return out


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--n", type=int, default=6000)
    p.add_argument("--seed", type=int, default=20260823)
    p.add_argument("--out", default=str(PROCESSED / "benign_listings.jsonl"))
    a = p.parse_args()

    PROCESSED.mkdir(parents=True, exist_ok=True)
    print("Building benign corpus...")
    df = build(a.n, a.seed)

    with open(a.out, "w", encoding="utf-8") as f:
        for rec in df.to_dict(orient="records"):
            if pd.isna(rec.get("price_inr")):
                rec["price_inr"] = None
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")

    print(f"\nwrote {len(df)} benign listings -> {a.out}")
    print(df.source.value_counts().to_string())
    L = df.description.str.len()
    print(f"desc chars: median {int(L.median())}, p95 {int(L.quantile(0.95))}, max {int(L.max())}")


if __name__ == "__main__":
    main()
