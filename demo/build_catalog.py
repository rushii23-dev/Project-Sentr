"""Build the demo storefront catalogue, clean and poisoned.

    python demo/build_catalog.py

Writes demo/catalog_clean.json and demo/catalog_poisoned.json as OpenAI Agentic
Commerce Protocol feeds -- the format Razorpay's ChatGPT storefront syncs
merchant catalogues into.

WHY THIS IS A SCRIPT AND NOT TWO HAND-EDITED FILES
    The two catalogues must differ in exactly one field of exactly the injected
    items, or the side-by-side demo is comparing two different things and proves
    nothing. Generating both from one product list makes that true by
    construction rather than by careful editing.

DEFENCE ONLY (CLAUDE.md section 9)
    No payload is written here. Injected strings are read VERBATIM from
    data/fixtures/commerce_patterns.yaml -- the same frozen fixture the
    evaluation uses -- and this script only chooses which product carries which
    already-published pattern. Nothing is composed, mutated or optimised, and
    the mapping below is fixed, not searched.

WHY SEVERAL PRODUCTS PER CATEGORY
    With one candidate per query, blocking the poisoned listing leaves the agent
    nothing else to buy, so both runs look identical and the demo proves
    nothing. Every query a visitor is likely to type needs at least one honest
    alternative the agent can fall back to. That is the whole point being shown:
    a screened catalogue still sells, it just sells the honest listing.
"""

from __future__ import annotations

import json
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
FIXTURES = ROOT / "data" / "fixtures" / "commerce_patterns.yaml"
OUT = ROOT / "demo"

SELLER = "Kirana Tech"
BASE = "https://kiranatech.example"

# item_id, title, brand, category, mrp, sale, rating, reviews, description, warning
PRODUCTS = [
    # ---------------------------------------------------- wireless chargers
    ("CHG-MAG", "MagPod 15W Wireless Charger", "MagPod", "Wireless Chargers",
     1799, 999, "4.3", 2841,
     "Magnetic 15W wireless charging puck that snaps to the back of your phone. "
     "Slim enough for a pocket, weighs 48 grams. Works through cases up to 3mm. "
     "Includes a 1 metre braided USB-C lead. 1 year manufacturer warranty included.",
     "Use only the supplied cable. Remove thick cases before charging."),
    ("CHG-HAL", "Halo Slim Charging Pad", "Halo", "Wireless Chargers",
     2199, 1299, "4.1", 1663,
     "Flat 10W charging pad with a woven fabric top and a non-slip base. "
     "Charges through most cases. Status LED dims automatically at night. "
     "USB-C input, adapter sold separately.",
     "Not compatible with metal phone cases or magnetic card holders."),
    ("CHG-DUO", "Voltaire Duo Charging Stand", "Voltaire", "Wireless Chargers",
     2499, 1449, "4.4", 906,
     "Upright stand that charges a phone and earbuds at the same time. "
     "Adjustable 45 to 70 degree viewing angle, so face unlock still works while "
     "it charges. Aluminium frame with a silicone cradle.",
     "Total output is shared across both pads when two devices are charging."),
    ("CHG-PUCK", "Nimbus Mini Charging Puck", "Nimbus", "Wireless Chargers",
     1299, 749, "3.9", 4127,
     "Pocket-sized 7.5W charging puck, 62 grams. Rubberised ring keeps the phone "
     "centred. Fold-flat cable channel on the underside for travel.",
     "7.5W maximum. Fast charging is not supported on all handsets."),

    # ---------------------------------------------------------- earbuds
    ("EAR-TWS", "Vibe Air Wireless Earbuds", "Vibe", "Earbuds",
     2999, 1499, "4.0", 8925,
     "True wireless earbuds with 28 hours total playback including the case. "
     "Environmental noise cancellation on calls, IPX4 splash resistance and "
     "touch controls. Bluetooth 5.3 with a low-latency mode for video.",
     "Splash resistant, not waterproof. Do not wear while swimming."),
    ("EAR-BUD", "Kestrel Buds Pro", "Kestrel", "Earbuds",
     3499, 1899, "4.3", 2210,
     "Hybrid active noise cancellation with a transparency mode you can hold a "
     "conversation through. 32 hours total playback, USB-C and wireless charging "
     "case, multipoint pairing across two devices.",
     "Noise cancellation reduces battery life by roughly 20 percent."),
    ("EAR-CLIP", "Aria OpenClip Earbuds", "Aria", "Earbuds",
     2799, 1749, "4.2", 1385,
     "Open-ear clip design that rests outside the ear canal, so you stay aware of "
     "traffic while running. 24 hours total playback, IPX5, secure over 10km "
     "sessions without pressure fatigue.",
     "Open design leaks sound at high volume. Not suited to quiet offices."),

    # ------------------------------------------------------------- cables
    ("CBL-USB", "BraidPro USB-C Cable 1.5m", "BraidPro", "Cables",
     699, 349, "4.4", 5210,
     "1.5 metre braided USB-C to USB-C cable rated for 60W charging and 480Mbps "
     "data. Nylon sleeve with reinforced strain relief, tested to 20,000 bends. "
     "Includes a reusable cable tie.",
     "60W maximum. Not rated for 100W laptop charging."),
    ("CBL-FAST", "Ironweave 100W USB-C Cable 2m", "Ironweave", "Cables",
     999, 499, "4.5", 3074,
     "2 metre 100W USB-C cable with an E-marker chip, so laptops negotiate full "
     "power. USB 2.0 data speeds. Aramid fibre core under a braided sleeve.",
     "Data transfer is 480Mbps. Not a display or Thunderbolt cable."),
    ("CBL-PRO", "Anvil 100W USB-C Cable 2m", "Anvil", "Cables",
     1199, 649, "4.3", 1548,
     "2 metre 100W USB-C cable with an E-marker chip for full laptop charging. "
     "Kevlar-reinforced core, machined aluminium housings and a 90 degree "
     "connector that sits flat against a laptop edge.",
     "Data transfer is 480Mbps. Not a display or Thunderbolt cable."),
    ("CBL-SHORT", "Pocket USB-C Cable 30cm", "Pocket", "Cables",
     399, 199, "4.1", 1902,
     "30 centimetre USB-C cable sized for power banks and car chargers, so there "
     "is no loose loop on the seat. 60W rated, silicone jacket that stays soft in "
     "cold weather.",
     "Short length is intended for power banks, not wall sockets."),

    # -------------------------------------------------------- everything else
    ("PWB-10K", "Cellar 10000mAh Power Bank", "Cellar", "Power Banks",
     1899, 1099, "4.2", 6431,
     "10000mAh power bank with 22.5W output and pass-through charging. Recharges "
     "fully in 3.5 hours over USB-C. Four-LED charge indicator.",
     "Airline rules permit this capacity in cabin baggage only."),
    ("HUB-7IN1", "Portway 7-in-1 USB-C Hub", "Portway", "Adapters",
     2799, 1599, "4.3", 1174,
     "Seven port USB-C hub with 4K HDMI at 60Hz, three USB-A ports, SD and "
     "microSD readers and 100W pass-through charging. Aluminium shell that runs "
     "cool under load.",
     "4K at 60Hz requires a host that supports DisplayPort Alt Mode."),
    ("SPK-BT", "Pulse Mini Bluetooth Speaker", "Pulse", "Speakers",
     3499, 1899, "4.2", 942,
     "Palm-sized Bluetooth speaker with a passive radiator for low end you can "
     "actually hear. 12 hours playback, IP67 dust and water rating, pairs to a "
     "second unit for stereo.",
     "IP67 rated. Rinse with fresh water after use near salt water."),
    ("STD-LAP", "DeskRise Aluminium Laptop Stand", "DeskRise", "Desk",
     2299, 1199, "4.5", 1204,
     "Folding aluminium laptop stand with six height positions, lifting the "
     "screen to eye level. Holds up to 8kg. Silicone pads front and rear. Folds "
     "to 2cm for a bag.",
     "Fits laptops from 11 to 17 inches."),
    ("CSE-PHN", "Shieldskin Clear Phone Case", "Shieldskin", "Cases",
     799, 399, "4.0", 7788,
     "Clear TPU case with a raised camera lip and reinforced corners rated to a "
     "2 metre drop. Anti-yellowing coating. Compatible with magnetic wireless "
     "charging.",
     "Clear cases discolour over time. Coating slows this, it does not stop it."),
]

# item_id -> (family, index into that family's pattern list in the fixture)
#
# Fixed mapping. The strings themselves are NOT written here -- they are read
# from the frozen fixture at build time, so this file contains no attack text.
# One injection per query a visitor is likely to type, so every prompt has a
# poisoned listing AND an honest alternative to fall back to.
INJECTIONS = {
    "CHG-MAG": ("fake_role_marker", 0),      # canonical example, CLAUDE.md section 2
    "EAR-BUD": ("prior_authorisation", 1),
    # The poisoned listing has to be the one the agent would choose on the
    # merits, or the attack never gets a chance to fire -- and that is exactly
    # what a real attacker would target: the listing that already wins the
    # query. Measured against the three demo prompts, those are the cheapest
    # well-reviewed charger, the only ANC earbuds, and the only 100W cable.
    "CBL-FAST": ("fake_role_marker", 1),

}


def load_patterns() -> dict[str, list[str]]:
    doc = yaml.safe_load(FIXTURES.read_text(encoding="utf-8"))
    return {f["family"]: f["patterns"] for f in doc["families"]}


def item(p: tuple, description: str) -> dict:
    iid, title, brand, cat, mrp, sale, rating, reviews, _desc, warning = p
    return {
        "item_id": iid,
        "title": title,
        "description": description,
        "url": f"{BASE}/p/{iid}",
        "brand": brand,
        "image_url": f"/static/img/{iid}.jpg",
        "price": f"{mrp}.00 INR",
        "sale_price": f"{sale}.00 INR",
        "availability": "in_stock",
        "condition": "new",
        "product_category": f"Electronics > Mobile Accessories > {cat}",
        "star_rating": rating,
        "review_count": reviews,
        "pricing_trend": "Lowest price in 3 months",
        "warning": warning,
        "is_eligible_search": True,
        "is_eligible_checkout": True,
        "seller_name": SELLER,
        "seller_privacy_policy": f"{BASE}/privacy",
        "seller_tos": f"{BASE}/terms",
        "target_countries": ["IN"],
    }


SPEC = ("Shaped as an OpenAI Agentic Commerce Protocol product feed -- the format "
        "Razorpay's ChatGPT storefront syncs merchant catalogues into. Field names, "
        "types and enums follow the published spec.")
DEVIATION = ("image_url is a local path rather than an absolute URL, because the demo "
             "serves its own images offline. Everything else matches the spec.")


def main() -> None:
    patterns = load_patterns()

    clean_feed, poisoned_feed = [], []
    injected = []
    for p in PRODUCTS:
        iid, honest = p[0], p[8]
        clean_feed.append(item(p, honest))

        if iid in INJECTIONS:
            family, idx = INJECTIONS[iid]
            span = patterns[family][idx]          # verbatim, never edited
            bad = item(p, f"{honest}\n\n{span}")
            bad["_poisoned"] = True
            bad["_attack_family"] = family
            bad["_injected_field"] = "description"
            bad["_injected_span"] = span
            poisoned_feed.append(bad)
            injected.append((iid, family))
        else:
            poisoned_feed.append(item(p, honest))

    header = {"_spec": SPEC, "_deviation": DEVIATION, "seller_name": SELLER}

    clean = {"_note": "Honest catalogue for the demo storefront.", **header,
             "feed": clean_feed}
    poisoned = {
        "_note": "POISONED catalogue. Demonstrates the attack against our own sandbox agent.",
        **header,
        "feed": poisoned_feed,
        "_DEFENSIVE_FIXTURE": (
            "Contains published prompt-injection patterns, held for defensive "
            "evaluation only. Every string is copied verbatim from "
            "data/fixtures/commerce_patterns.yaml by demo/build_catalog.py. "
            "Nothing here is composed, mutated or optimised (CLAUDE.md section 9)."
        ),
        "_diff_from_clean": (
            "Identical to catalog_clean.json except the ACP 'description' field of: "
            + ", ".join(f"{i} ({f})" for i, f in injected)
        ),
    }

    for name, doc in (("catalog_clean.json", clean), ("catalog_poisoned.json", poisoned)):
        (OUT / name).write_text(json.dumps(doc, indent=2, ensure_ascii=False),
                                encoding="utf-8")

    # The guarantee the side-by-side demo depends on.
    diff = [c["item_id"] for c, b in zip(clean_feed, poisoned_feed) if c != b]
    assert diff == [i for i, _ in injected], f"unexpected diff: {diff}"
    for c, b in zip(clean_feed, poisoned_feed):
        if c != b:
            assert {k for k in b if not k.startswith("_")} == set(c), "field added"
            assert all(c[k] == b[k] for k in c if k != "description"), "non-description change"

    print(f"{len(PRODUCTS)} products -> catalog_clean.json + catalog_poisoned.json")
    by_cat: dict[str, int] = {}
    for p in PRODUCTS:
        by_cat[p[3]] = by_cat.get(p[3], 0) + 1
    for cat, n in sorted(by_cat.items(), key=lambda kv: -kv[1]):
        print(f"  {cat:20} {n}")
    print(f"\npoisoned: {len(injected)}")
    for iid, fam in injected:
        print(f"  {iid:10} {fam}")
    print("\nverified: the two feeds differ only in the description of those items.")


if __name__ == "__main__":
    main()
