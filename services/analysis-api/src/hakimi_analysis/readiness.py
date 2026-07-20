import hashlib
import os
import subprocess
from collections.abc import Callable
from contextlib import suppress
from pathlib import Path
from threading import Lock
from time import monotonic
from typing import Protocol
from urllib.parse import urlparse
from uuid import uuid4

from hakimi_analysis.media import probe_duration_sync
from hakimi_analysis.orchestration import SkillRepository
from hakimi_analysis.settings import Settings
from hakimi_analysis.sources import SourceCatalog

FFMPEG_VERSION_TIMEOUT_SECONDS = 5
FFMPEG_CONFIGURATION_PREFIX = "configuration:"
FORBIDDEN_FFMPEG_CONFIGURATION_FLAGS = frozenset({"--enable-gpl", "--enable-nonfree"})


def validate_ffmpeg_runtime(
    executable: Path,
    expected_sha256: str,
    expected_configuration_sha256: str,
) -> bool:
    try:
        resolved = executable.resolve(strict=True)
        if not resolved.is_file() or not os.access(resolved, os.X_OK):
            return False
        digest = hashlib.sha256()
        with resolved.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
        if digest.hexdigest() != expected_sha256:
            return False
        completed = subprocess.run(
            [str(resolved), "-version"],
            capture_output=True,
            check=False,
            text=True,
            timeout=FFMPEG_VERSION_TIMEOUT_SECONDS,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    if completed.returncode != 0:
        return False
    lines = [line.strip() for line in completed.stdout.splitlines() if line.strip()]
    if not lines or not lines[0].startswith("ffmpeg version "):
        return False
    configuration_line = next(
        (line for line in lines if line.startswith(FFMPEG_CONFIGURATION_PREFIX)),
        None,
    )
    if configuration_line is None:
        return False
    configuration_flags = set(configuration_line.removeprefix(FFMPEG_CONFIGURATION_PREFIX).split())
    if configuration_flags & FORBIDDEN_FFMPEG_CONFIGURATION_FLAGS:
        return False
    return (
        hashlib.sha256(configuration_line.encode("utf-8")).hexdigest()
        == expected_configuration_sha256
    )


class ReadinessProbe(Protocol):
    def check(self) -> str | None: ...


class ProductionReadiness:
    def __init__(
        self,
        *,
        settings: Settings,
        catalog: SourceCatalog,
        temp_root: Path,
        skills_root: Path,
        duration_probe: Callable[[Path], float] | None = None,
        ffmpeg_runtime_probe: Callable[[Path, str, str], bool] | None = None,
        cache_seconds: float = 5,
    ) -> None:
        self._settings = settings
        self._catalog = catalog
        self._temp_root = temp_root
        self._skills_root = skills_root
        self._duration_probe = duration_probe or probe_duration_sync
        self._ffmpeg_runtime_probe = ffmpeg_runtime_probe or validate_ffmpeg_runtime
        self._cache_seconds = cache_seconds
        self._cache_lock = Lock()
        self._cached_at: float | None = None
        self._cached_result: str | None = None

    def check(self) -> str | None:
        with self._cache_lock:
            now = monotonic()
            if self._cached_at is not None and now - self._cached_at < self._cache_seconds:
                return self._cached_result
            result = self._check_uncached()
            self._cached_at = now
            self._cached_result = result
            return result

    def _check_uncached(self) -> str | None:
        if (
            self._settings.app_env != "production"
            or self._settings.analysis_provider != "cloud"
            or self._settings.ark_api_key is None
            or self._settings.volc_asr_api_key is None
            or not self._settings.ark_api_key.get_secret_value()
            or not self._settings.volc_asr_api_key.get_secret_value()
            or not self._settings.ark_model_id.strip()
            or not self._settings.ark_base_url.strip()
            or not self._settings.volc_asr_resource_id.strip()
            or not self._settings.volc_asr_url.strip()
        ):
            return "provider_configuration_invalid"
        ark_url = urlparse(self._settings.ark_base_url)
        asr_url = urlparse(self._settings.volc_asr_url)
        if ark_url.scheme != "https" or not ark_url.netloc:
            return "provider_configuration_invalid"
        if asr_url.scheme != "wss" or not asr_url.netloc:
            return "provider_configuration_invalid"
        if (
            self._settings.source_manifest_path is None
            or self._settings.source_media_root is None
            or self._settings.public_media_base_url is None
            or not self._settings.source_manifest_path.is_file()
            or not self._catalog.manifest_backed
        ):
            return "source_manifest_invalid"
        parsed_media_url = urlparse(self._settings.public_media_base_url)
        if parsed_media_url.scheme != "https" or not parsed_media_url.netloc:
            return "source_manifest_invalid"
        ffmpeg_executable = self._settings.imageio_ffmpeg_exe
        ffmpeg_sha256 = self._settings.ffmpeg_expected_sha256
        ffmpeg_configuration_sha256 = self._settings.ffmpeg_expected_configuration_sha256
        if (
            ffmpeg_executable is None
            or ffmpeg_sha256 is None
            or ffmpeg_configuration_sha256 is None
            or not self._ffmpeg_runtime_probe(
                ffmpeg_executable,
                ffmpeg_sha256,
                ffmpeg_configuration_sha256,
            )
        ):
            return "media_processor_unavailable"
        if not self._catalog.validate_media(self._duration_probe):
            return "media_cache_invalid"
        try:
            SkillRepository.load(self._skills_root)
        except (OSError, ValueError):
            return "skill_configuration_invalid"
        if (
            self._settings.judge_access_code is None
            or self._settings.access_cookie_secret is None
            or len(self._settings.judge_access_code.get_secret_value().encode("utf-8")) < 16
            or len(self._settings.access_cookie_secret.get_secret_value().encode("utf-8")) < 32
        ):
            return "access_configuration_invalid"
        web_static_root = self._settings.web_static_root
        if (
            web_static_root is None
            or not web_static_root.is_dir()
            or not (web_static_root / "index.html").is_file()
        ):
            return "web_static_unavailable"
        if not self._settings.trusted_proxy_cidr_list:
            return "proxy_configuration_invalid"
        if not self._temp_storage_available():
            return "temp_storage_unavailable"
        return None

    def _temp_storage_available(self) -> bool:
        probe_path = self._temp_root / f".ready-{uuid4().hex}"
        try:
            self._temp_root.mkdir(parents=True, exist_ok=True)
            probe_path.write_bytes(b"ready")
            if probe_path.read_bytes() != b"ready":
                return False
            probe_path.unlink()
            return True
        except OSError:
            return False
        finally:
            with suppress(OSError):
                probe_path.unlink(missing_ok=True)


class StaticReadiness:
    def __init__(self, failure_code: str | None) -> None:
        self._failure_code = failure_code

    def check(self) -> str | None:
        return self._failure_code
