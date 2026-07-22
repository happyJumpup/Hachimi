from pathlib import Path
from runpy import run_path
from typing import Any
from zipfile import ZipFile

import pytest

PROJECT_ROOT = Path(__file__).parents[3]
VERIFY_SCRIPT = PROJECT_ROOT / "deploy" / "cloudbase" / "verify-source-archive.py"


def _verify_source_archive(path: Path) -> None:
    namespace: dict[str, Any] = run_path(str(VERIFY_SCRIPT))
    namespace["verify_source_archive"](path)


def test_cloudbase_source_archive_accepts_production_sources(tmp_path: Path) -> None:
    archive = tmp_path / "source.zip"
    with ZipFile(archive, "w") as bundle:
        bundle.writestr("Dockerfile", "FROM scratch\n")
        bundle.writestr("apps/web/package.json", "{}\n")

    _verify_source_archive(archive)


def test_cloudbase_source_archive_rejects_reference_questionnaire(
    tmp_path: Path,
) -> None:
    archive = tmp_path / "source.zip"
    with ZipFile(archive, "w") as bundle:
        bundle.writestr("Dockerfile", "FROM scratch\n")
        bundle.writestr("questionaire/server.js", "console.log('reference only')\n")

    with pytest.raises(ValueError, match="reference questionnaire"):
        _verify_source_archive(archive)


def test_reference_questionnaire_has_both_archive_exclusions() -> None:
    attributes = (PROJECT_ROOT / ".gitattributes").read_text(encoding="utf-8")
    dockerignore = (PROJECT_ROOT / ".dockerignore").read_text(encoding="utf-8")

    assert "/questionaire export-ignore" in attributes.splitlines()
    assert "questionaire" in dockerignore.splitlines()
