import hashlib
import json
import subprocess
from pathlib import Path

import pytest

from hakimi_analysis.readiness import (
    FFMPEG_RELEASE_FINGERPRINT,
    FFMPEG_RELEASE_VERSION,
    validate_ffmpeg_build_receipt,
    validate_ffmpeg_runtime,
)


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


def test_ffmpeg_runtime_requires_the_exact_expected_version_token(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    executable = tmp_path / "ffmpeg"
    executable.write_bytes(b"audited-ffmpeg")
    executable.chmod(0o755)
    configuration_line = "configuration: --enable-shared --disable-static"
    monkeypatch.setattr(
        "hakimi_analysis.readiness.subprocess.run",
        lambda *_args, **_kwargs: subprocess.CompletedProcess(
            args=[str(executable), "-version"],
            returncode=0,
            stdout=f"ffmpeg version 8.1.20\n{configuration_line}\n",
            stderr="",
        ),
    )

    assert not validate_ffmpeg_runtime(
        executable,
        sha256(b"audited-ffmpeg"),
        sha256(configuration_line),
        expected_version="8.1.2",
    )


def test_ffmpeg_build_receipt_binds_official_source_signature_config_and_binary(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "opt" / "trainpal" / "ffmpeg"
    executable = root / "bin" / "ffmpeg"
    executable.parent.mkdir(parents=True)
    executable.write_bytes(b"official-source-build")
    executable.chmod(0o755)
    configuration_line = (
        "configuration: --prefix=/opt/trainpal/ffmpeg --enable-shared "
        "--disable-static --disable-network --disable-autodetect"
    )
    receipt = root / "receipt.json"
    receipt.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "version": FFMPEG_RELEASE_VERSION,
                "source_url": (
                    f"https://ffmpeg.org/releases/ffmpeg-{FFMPEG_RELEASE_VERSION}.tar.xz"
                ),
                "signing_key_fingerprint": FFMPEG_RELEASE_FINGERPRINT,
                "binary_sha256": sha256(b"official-source-build"),
                "configuration_line": configuration_line,
                "configuration_sha256": sha256(configuration_line),
            }
        ),
        encoding="utf-8",
    )

    monkeypatch.setattr(
        "hakimi_analysis.readiness.subprocess.run",
        lambda *_args, **_kwargs: subprocess.CompletedProcess(
            args=[str(executable), "-version"],
            returncode=0,
            stdout=(
                f"ffmpeg version {FFMPEG_RELEASE_VERSION}\n{configuration_line}\n"
            ),
            stderr="",
        ),
    )

    assert validate_ffmpeg_build_receipt(executable, receipt)

    payload = json.loads(receipt.read_text(encoding="utf-8"))
    payload["signing_key_fingerprint"] = "0" * 40
    receipt.write_text(json.dumps(payload), encoding="utf-8")
    assert not validate_ffmpeg_build_receipt(executable, receipt)
