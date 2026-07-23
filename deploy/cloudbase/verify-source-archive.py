from __future__ import annotations

import sys
from pathlib import Path
from zipfile import BadZipFile, ZipFile

REFERENCE_QUESTIONNAIRE_PREFIX = "questionaire/"
FORBIDDEN_MEDIA_SUFFIXES = (
    ".mp4",
    ".mov",
    ".webm",
    ".avi",
    ".mkv",
    ".mp3",
    ".wav",
    ".m4a",
    ".aac",
    ".ogg",
)


def _contains_environment_component(name: str) -> bool:
    return any(
        component == ".env" or component.startswith(".env.")
        for component in name.casefold().split("/")
    )


def _contains_media_component(name: str) -> bool:
    return any(
        component.casefold().endswith(FORBIDDEN_MEDIA_SUFFIXES) for component in name.split("/")
    )


def verify_source_archive(archive_path: Path) -> None:
    """Reject private material from an immutable CloudBase source ZIP."""

    try:
        with ZipFile(archive_path) as archive:
            names = {
                entry.filename.replace("\\", "/").removeprefix("./") for entry in archive.infolist()
            }
    except (BadZipFile, OSError) as error:
        raise ValueError("CloudBase source archive is unreadable") from error

    if any(_contains_environment_component(name) for name in names):
        raise ValueError("CloudBase source archive contains an environment file")

    if any(_contains_media_component(name) for name in names):
        raise ValueError("CloudBase source archive contains a media file")

    if any(
        name == "questionaire" or name.startswith(REFERENCE_QUESTIONNAIRE_PREFIX) for name in names
    ):
        raise ValueError("CloudBase source archive contains the reference questionnaire")


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print("usage: verify-source-archive.py <source.zip>", file=sys.stderr)
        return 2
    try:
        verify_source_archive(Path(argv[1]))
    except ValueError as error:
        print(str(error), file=sys.stderr)
        return 1
    print("CloudBase source archive boundary validation passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
