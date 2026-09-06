"""Bundle exactly what Colab needs to train the layer-2 classifier.

    python notebooks/pack_for_colab.py

Writes notebooks/sentr_colab.zip -- the sentr package plus train.jsonl and
val.jsonl, and nothing else.

test.jsonl is deliberately excluded. The held-out set does not travel to a
machine where training happens (SPEC.md rule 3); the packer raises if it
somehow ends up in the archive.
"""

from __future__ import annotations

import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "notebooks" / "sentr_colab.zip"

INCLUDE = [
    "sentr/__init__.py",
    "sentr/classifier.py",
    "sentr/train_classifier.py",
    "sentr/rules.py",
    "sentr/rules.yaml",
    "data/processed/train.jsonl",
    "data/processed/val.jsonl",
]


def main() -> None:
    missing = [p for p in INCLUDE if not (ROOT / p).exists()]
    if missing:
        raise SystemExit(f"missing: {missing}")

    OUT.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(OUT, "w", zipfile.ZIP_DEFLATED) as z:
        for rel in INCLUDE:
            z.write(ROOT / rel, arcname=f"sentr_colab/{rel}")

    with zipfile.ZipFile(OUT) as z:
        names = z.namelist()
        if any("test.jsonl" in n for n in names):
            OUT.unlink()
            raise SystemExit("refusing to ship test.jsonl to Colab")
        size = sum(i.compress_size for i in z.infolist())

    print(f"wrote {OUT}  ({size/1e6:.1f} MB compressed, {len(names)} files)")
    for n in names:
        print(f"  {n}")
    print("\ntest.jsonl excluded and verified absent.")


if __name__ == "__main__":
    main()
