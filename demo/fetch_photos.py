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
are written to demo/static/img/credits.json and shown in the page footer --
the Pexels licence does not require attribution, but crediting people whose
work you use is the right default.
"""

from __future__ import annotations

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

# product id -> (search query, how many results to consider)
QUERIES: dict[str, str] = {
    "CHG-65W": "usb charger adapter",
    "PWB-10K": "power bank portable charger",
    "CBL-USB": "usb cable",
    "EAR-TWS": "wireless earbuds",
    "HUB-7IN1": "usb hub adapter",
    "STD-LAP": "laptop stand desk",
}


def square(img: Image.Image, size: int = SIZE) -> Image.Image:
    """Centre-crop to a square and resize, on a white ground."""
    img = ImageOps.exif_transpose(img).convert("RGB")
    img = ImageOps.fit(img, (size, size), method=Image.LANCZOS, centering=(0.5, 0.5))
    return img


def fetch(pid: str, query: str, key: str, index: int = 0) -> dict | None:
    r = requests.get(
        API,
        headers={"Authorization": key},
        params={"query": query, "per_page": 8, "orientation": "square"},
        timeout=45,
    )
    if r.status_code != 200:
        print(f"  {pid}: HTTP {r.status_code} -- {r.text[:140]}")
        return None
    photos = r.json().get("photos", [])
    if not photos:
        print(f"  {pid}: no results for {query!r}")
        return None
    p = photos[min(index, len(photos) - 1)]
    src = p["src"].get("large2x") or p["src"].get("large") or p["src"]["medium"]
    img_bytes = requests.get(src, timeout=60).content
    img = square(Image.open(io.BytesIO(img_bytes)))
    out = IMG_DIR / f"{pid}.jpg"
    img.save(out, "JPEG", quality=88, optimize=True)
    print(f"  {pid}: {out.name}  ({out.stat().st_size // 1024} KB)  by {p['photographer']}")
    return {
        "product_id": pid,
        "file": f"img/{pid}.jpg",
        "photographer": p["photographer"],
        "photographer_url": p["photographer_url"],
        "source": p["url"],
        "query": query,
    }


def main() -> int:
    key = os.getenv("PEXELS_API_KEY", "").strip()
    if not key:
        print("PEXELS_API_KEY not set in .env")
        print("Get a free key at https://www.pexels.com/api/ then re-run.")
        return 1

    IMG_DIR.mkdir(parents=True, exist_ok=True)
    print(f"downloading {len(QUERIES)} product photos -> {IMG_DIR}")

    credits = []
    for pid, query in QUERIES.items():
        try:
            c = fetch(pid, query, key)
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
