import hashlib
import subprocess
from pathlib import Path

import pytest

from hakimi_analysis.readiness import validate_ffmpeg_runtime


def sha256(value: str | bytes) -> str:
    payload = value.encode("utf-8") if isinstance(value, str) else value
    return hashlib.sha256(payload).hexdigest()


def test_ffmpeg_runtime_is_pinned_and_uses_an_audited_lgpl_configuration(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    executable = tmp_path / "ffmpeg"
    executable.write_bytes(b"audited-ffmpeg")
    executable.chmod(0o755)
    configuration_line = "configuration: --disable-debug --enable-shared"

    def run(*_args: object, **_kwargs: object) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(
            args=[str(executable), "-version"],
            returncode=0,
            stdout=f"ffmpeg version 7.1\n{configuration_line}\nlibavutil 59\n",
            stderr="",
        )

    monkeypatch.setattr("hakimi_analysis.readiness.subprocess.run", run)

    assert validate_ffmpeg_runtime(
        executable,
        sha256(b"audited-ffmpeg"),
        sha256(configuration_line),
    )


@pytest.mark.parametrize(
    "configuration_line",
    [
        "configuration: --enable-gpl --enable-shared",
        "configuration: --enable-nonfree --enable-shared",
    ],
)
def test_ffmpeg_runtime_rejects_gpl_or_nonfree_configuration(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    configuration_line: str,
) -> None:
    executable = tmp_path / "ffmpeg"
    executable.write_bytes(b"unsafe-ffmpeg")
    executable.chmod(0o755)

    def run(*_args: object, **_kwargs: object) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(
            args=[str(executable), "-version"],
            returncode=0,
            stdout=f"ffmpeg version 7.1\n{configuration_line}\n",
            stderr="",
        )

    monkeypatch.setattr("hakimi_analysis.readiness.subprocess.run", run)

    assert not validate_ffmpeg_runtime(
        executable,
        sha256(b"unsafe-ffmpeg"),
        sha256(configuration_line),
    )


def test_ffmpeg_runtime_rejects_an_unpinned_binary(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    executable = tmp_path / "ffmpeg"
    executable.write_bytes(b"changed-ffmpeg")
    executable.chmod(0o755)
    called = False

    def run(*_args: object, **_kwargs: object) -> subprocess.CompletedProcess[str]:
        nonlocal called
        called = True
        raise AssertionError("a mismatched binary must not be executed")

    monkeypatch.setattr("hakimi_analysis.readiness.subprocess.run", run)

    assert not validate_ffmpeg_runtime(executable, "0" * 64, "1" * 64)
    assert not called
