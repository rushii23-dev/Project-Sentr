"""Download product photography for the demo storefront.

Uses the Pexels API (free, no card). Photos are downloaded ONCE and stored in
demo/static/img/ so the demo never touches the network while it is running --
a live demo that depends on a third-party image host is a live demo that can
fail in front of judges.

Setup:
    1. Get a free key at https://www.pexels.com/api/
    2. Put PEXELS_API_KEY=... in .env
    3. python demo/fetch_photos.py

Photos are cropped square and normalised so the product grid looks like one
coherent storefront rather than a pile of stock images. Photographer credits
are written to demo/static/img/credits.json -- the Pexels licence does not
require attribution, and the page no longer lists vendor names on screen, but
keeping the record of whose work this is remains the right default.
"""

from __future__ import annotations

import hashlib
import io
import json
import os
import sys
from pathlib import Path

import requests
from dotenv import load_dotenv
from PIL import Image, ImageOps

ROOT = Path(__file__).resolve().parent.parent
IMG_DIR = ROOT / "demo" / "static" / "img"
load_dotenv(ROOT / ".env")

SIZE = 900
API = "https://api.pexels.com/v1/search"

# product id -> (search query, which result to take)
#
# The index matters. Four wireless chargers all taking result 0 of the same
# query come back as four photographs of the same white disc, which makes the
# storefront look like a placeholder rather than a catalogue. Different queries
# where the products genuinely differ, different indices where they do not.
QUERIES: dict[str, tuple[str, int]] = {
    # wireless chargers
    "CHG-MAG": ("wireless charger phone", 0),
    "CHG-HAL": ("wireless charging pad", 1),
    "CHG-DUO": ("phone charging stand", 2),
    "CHG-PUCK": ("wireless charger minimal", 3),
    # earbuds
    "EAR-TWS": ("wireless earbuds", 0),
    "EAR-BUD": ("earbuds charging case", 2),
    "EAR-CLIP": ("earphones white background", 3),
    # cables
    "CBL-USB": ("usb cable braided", 0),
    "CBL-FAST": ("usb type c cable", 2),
    "CBL-SHORT": ("charging cable coiled", 3),
    # everything else
    "PWB-10K": ("power bank battery", 1),
    "HUB-7IN1": ("usb hub adapter", 0),
    "SPK-BT": ("bluetooth speaker", 0),
    "STD-LAP": ("laptop stand desk", 0),
    "CBL-PRO": ("cable charger black", 2),
    "CSE-PHN": ("phone case clear", 1),
}


def square(img: Image.Image, size: int = SIZE) -> Image.Image:
    """Centre-crop to a square and resize, on a white ground."""
    img = ImageOps.exif_transpose(img).convert("RGB")
    img = ImageOps.fit(img, (size, size), method=Image.LANCZOS, centering=(0.5, 0.5))
    return img


def existing_hashes(skip: str = "") -> dict[str, str]:
    """md5 -> filename for every photo already downloaded."""
    out = {}
    for f in sorted(IMG_DIR.glob("*.jpg")):
        if f.stem == skip:
            continue
        out[hashlib.md5(f.read_bytes()).hexdigest()] = f.name
    return out


def fetch(pid: str, query: str, key: str, index: int = 0) -> dict | None:
    """Download one product photo, refusing a picture we already used.

    Different queries return overlapping results -- "usb type c cable" and
    "power bank portable charger" handed back the same photograph -- and two
    identical product cards make the storefront look like a placeholder. So the
    result is hashed against what is already on disk and the next candidate is
    tried instead of silently shipping a duplicate.
    """
    r = requests.get(
        API,
        headers={"Authorization": key},
        params={"query": query, "per_page": 15, "orientation": "square"},
        timeout=45,
    )
    if r.status_code != 200:
        print(f"  {pid}: HTTP {r.status_code} -- {r.text[:140]}")
        return None
    photos = r.json().get("photos", [])
    if not photos:
        print(f"  {pid}: no results for {query!r}")
        return None

    seen = existing_hashes(skip=pid)
    order = list(range(min(index, len(photos) - 1), len(photos))) + \
        list(range(0, min(index, len(photos) - 1)))
    for attempt, i in enumerate(order):
        p = photos[i]
        src = p["src"].get("large2x") or p["src"].get("large") or p["src"]["medium"]
        img_bytes = requests.get(src, timeout=60).content
        img = square(Image.open(io.BytesIO(img_bytes)))
        buf = io.BytesIO()
        img.save(buf, "JPEG", quality=88, optimize=True)
        digest = hashlib.md5(buf.getvalue()).hexdigest()
        if digest in seen:
            print(f"  {pid}: result {i} duplicates {seen[digest]}, trying next")
            continue
        out = IMG_DIR / f"{pid}.jpg"
        out.write_bytes(buf.getvalue())
        note = "" if attempt == 0 else f"  [result {i}]"
        print(f"  {pid}: {out.name}  ({out.stat().st_size // 1024} KB)  "
              f"by {p['photographer']}{note}")
        return {
            "product_id": pid,
            "file": f"img/{pid}.jpg",
            "photographer": p["photographer"],
            "photographer_url": p["photographer_url"],
            "source": p["url"],
            "query": query,
        }
    print(f"  {pid}: every result for {query!r} duplicates an existing photo")
    return None


def main() -> int:
    key = os.getenv("PEXELS_API_KEY", "").strip()
    if not key:
        print("PEXELS_API_KEY not set in .env")
        print("Get a free key at https://www.pexels.com/api/ then re-run.")
        return 1

    IMG_DIR.mkdir(parents=True, exist_ok=True)
    force = "--force" in sys.argv
    existing = json.loads((IMG_DIR / "credits.json").read_text(encoding="utf-8")) \
        if (IMG_DIR / "credits.json").exists() else []
    have = {c["product_id"]: c for c in existing}

    todo = [p for p in QUERIES if force or not (IMG_DIR / f"{p}.jpg").exists()]
    print(f"{len(QUERIES)} products, {len(todo)} to download -> {IMG_DIR}")
    if not force and len(todo) < len(QUERIES):
        print(f"  ({len(QUERIES) - len(todo)} already present; --force to refetch)")

    credits = [have[p] for p in QUERIES if p in have and p not in todo]
    for pid in todo:
        query, index = QUERIES[pid]
        try:
            c = fetch(pid, query, key, index)
            if c:
                credits.append(c)
        except Exception as e:
            print(f"  {pid}: FAILED {e}")

    (IMG_DIR / "credits.json").write_text(
        json.dumps(credits, indent=2), encoding="utf-8"
    )
    print(f"\n{len(credits)}/{len(QUERIES)} photos saved. Credits -> img/credits.json")
    if len(credits) < len(QUERIES):
        print("Some failed. Re-run, or pass a different index in QUERIES.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
