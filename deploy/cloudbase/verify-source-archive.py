from __future__ import annotations

import sys
from pathlib import Path
from zipfile import BadZipFile, ZipFile


REFERENCE_QUESTIONNAIRE_PREFIX = "questionaire/"


def verify_source_archive(archive_path: Path) -> None:
    """Reject reference-only material from an immutable CloudBase source ZIP."""

    try:
        with ZipFile(archive_path) as archive:
            names = {
                entry.filename.replace("\\", "/").removeprefix("./")
                for entry in archive.infolist()
            }
    except (BadZipFile, OSError) as error:
        raise ValueError("CloudBase source archive is unreadable") from error

    if any(
        name == "questionaire"
        or name.startswith(REFERENCE_QUESTIONNAIRE_PREFIX)
        for name in names
    ):
        raise ValueError(
            "CloudBase source archive contains the reference questionnaire"
        )


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
