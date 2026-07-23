from pathlib import Path
from runpy import run_path
from typing import Any
from zipfile import ZipFile

import pytest

PROJECT_ROOT = Path(__file__).parents[3]
VERIFY_SCRIPT = PROJECT_ROOT / "deploy" / "cloudbase" / "verify-source-archive.py"
FORBIDDEN_MEDIA_EXTENSIONS = (
    "mp4",
    "mov",
    "webm",
    "avi",
    "mkv",
    "mp3",
    "wav",
    "m4a",
    "aac",
    "ogg",
)


def _verify_source_archive(path: Path) -> None:
    namespace: dict[str, Any] = run_path(str(VERIFY_SCRIPT))
    namespace["verify_source_archive"](path)


def test_cloudbase_source_archive_accepts_production_sources(tmp_path: Path) -> None:
    archive = tmp_path / "source.zip"
    with ZipFile(archive, "w") as bundle:
        bundle.writestr("Dockerfile", "FROM scratch\n")
        bundle.writestr("apps/web/package.json", "{}\n")
        bundle.writestr(
            "competition/media-manifest.json",
            '{"version":1,"sources":[]}\n',
        )

    _verify_source_archive(archive)


@pytest.mark.parametrize(
    "archive_name",
    [".env", "apps/web/.env.local", "nested/config/.env.production"],
)
def test_cloudbase_source_archive_rejects_environment_file_at_any_depth(
    tmp_path: Path,
    archive_name: str,
) -> None:
    archive = tmp_path / "source.zip"
    with ZipFile(archive, "w") as bundle:
        bundle.writestr("Dockerfile", "FROM scratch\n")
        bundle.writestr(archive_name, "API_KEY=synthetic-test-value\n")

    with pytest.raises(ValueError, match="environment file"):
        _verify_source_archive(archive)


@pytest.mark.parametrize(
    "extension",
    FORBIDDEN_MEDIA_EXTENSIONS,
)
def test_cloudbase_source_archive_rejects_media_file_at_any_depth(
    tmp_path: Path,
    extension: str,
) -> None:
    archive = tmp_path / "source.zip"
    with ZipFile(archive, "w") as bundle:
        bundle.writestr("Dockerfile", "FROM scratch\n")
        bundle.writestr(
            f"competition/media/synthetic-sample.{extension}",
            b"synthetic-media-placeholder",
        )

    with pytest.raises(ValueError, match="media file"):
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


def test_source_boundaries_recursively_exclude_environment_and_media() -> None:
    attributes = (PROJECT_ROOT / ".gitattributes").read_text(encoding="utf-8").splitlines()
    dockerignore = (PROJECT_ROOT / ".dockerignore").read_text(encoding="utf-8").splitlines()

    assert "**/.env export-ignore" in attributes
    assert "**/.env.* export-ignore" in attributes
    assert "**/.env" in dockerignore
    assert "**/.env.*" in dockerignore
    for extension in FORBIDDEN_MEDIA_EXTENSIONS:
        assert f"**/*.{extension} export-ignore" in attributes
        assert f"**/*.{extension}" in dockerignore
