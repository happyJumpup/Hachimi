import json
from pathlib import Path

import pytest

from hakimi_analysis.benchmark.cli import (
    _validate_preflight_receipt,
    _write_preflight_receipt,
)


def test_run_receipt_must_cover_every_selected_route_component(tmp_path: Path) -> None:
    manifest = tmp_path / "manifest.json"
    manifest.write_text("{}", encoding="utf-8")
    receipt = tmp_path / "receipt.json"
    _write_preflight_receipt(
        receipt,
        manifest_path=manifest,
        passed_components=["asr", "seed_text"],
        failures=[],
    )

    _validate_preflight_receipt(
        receipt,
        manifest_path=manifest,
        selected_routes=["A-ASR"],
    )
    with pytest.raises(RuntimeError, match="does not cover"):
        _validate_preflight_receipt(
            receipt,
            manifest_path=manifest,
            selected_routes=["Q-AV"],
        )


def test_preflight_receipt_is_bound_to_exact_manifest_bytes(tmp_path: Path) -> None:
    manifest = tmp_path / "manifest.json"
    manifest.write_text("{}", encoding="utf-8")
    receipt = tmp_path / "receipt.json"
    _write_preflight_receipt(
        receipt,
        manifest_path=manifest,
        passed_components=["seed_av"],
        failures=[],
    )
    manifest.write_text(json.dumps({"changed": True}), encoding="utf-8")

    with pytest.raises(RuntimeError, match="does not match"):
        _validate_preflight_receipt(
            receipt,
            manifest_path=manifest,
            selected_routes=["S-AV"],
        )
