#!/usr/bin/env python3

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
MANIFEST_PATH = Path(__file__).with_name("registered-visual-assets.json")
VERSIONED_ROOTS = (
    (PROJECT_ROOT / "apps" / "web" / "public" / "trainpal" / "pets", 168),
    (PROJECT_ROOT / "apps" / "web" / "public" / "gymti" / "types", 7),
)
LEGACY_ASSETS = {
    "apps/web/src/assets/pet/completed.webp": (
        "74ffadcabdb1124680efcb0dbf2d4b2c2f5c5a88b79a6d811cf108a8552a92a9"
    ),
    "apps/web/src/assets/pet/idle.webp": (
        "5518f49229cc0331bfdf9c5351e6a7806bf97cac6c882b2d5dc40e620f85561c"
    ),
    "apps/web/src/assets/pet/paused.webp": (
        "d775c21bc2df5bd2156638247066f7f836b9b800c9d8f3044f595a81f906f91c"
    ),
    "apps/web/src/assets/pet/resting.webp": (
        "730f5c6b4b2b91d11ea23085eac3739a4d22841014d7c21752b207b63ff4db30"
    ),
    "apps/web/src/assets/pet/training.webp": (
        "bdab0d00707684a80f44f31fc09f0325ac0b608060ca746b63e15e6d940b44ab"
    ),
}


def _asset(path: Path) -> dict[str, object]:
    return {
        "sourcePath": path.relative_to(PROJECT_ROOT).as_posix(),
        "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
    }


def build_manifest() -> dict[str, object]:
    assets: list[dict[str, object]] = [
        {"sourcePath": source_path, "sha256": digest, "legacy": True}
        for source_path, digest in LEGACY_ASSETS.items()
    ]
    for root, expected_count in VERSIONED_ROOTS:
        source_files = sorted(root.rglob("*.webp"))
        if len(source_files) != expected_count:
            raise RuntimeError(
                f"expected {expected_count} registered WebP files under {root}, "
                f"found {len(source_files)}"
            )
        assets.extend(_asset(path) for path in source_files)
    assets.sort(key=lambda item: str(item["sourcePath"]))
    hashes = {str(item["sha256"]) for item in assets}
    if len(hashes) != len(assets):
        raise RuntimeError("registered visual assets must have unique SHA-256 values")
    return {"version": 1, "assets": assets}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--check",
        action="store_true",
        help="fail when the checked-in manifest differs from the registered source assets",
    )
    args = parser.parse_args()
    rendered = json.dumps(build_manifest(), ensure_ascii=False, indent=2) + "\n"
    if args.check:
        if not MANIFEST_PATH.is_file() or MANIFEST_PATH.read_text(encoding="utf-8") != rendered:
            raise RuntimeError("registered visual asset manifest is out of date")
        return 0
    MANIFEST_PATH.write_text(rendered, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
