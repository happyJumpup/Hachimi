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
        cache_seconds: float = 5,
    ) -> None:
        self._settings = settings
        self._catalog = catalog
        self._temp_root = temp_root
        self._skills_root = skills_root
        self._duration_probe = duration_probe or probe_duration_sync
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
        if not self._catalog.validate_media(self._duration_probe):
            return "media_cache_invalid"
        try:
            SkillRepository.load(self._skills_root)
        except (OSError, ValueError):
            return "skill_configuration_invalid"
        if (
            self._settings.judge_access_code is None
            or self._settings.access_cookie_secret is None
            or not self._settings.judge_access_code.get_secret_value()
            or len(self._settings.access_cookie_secret.get_secret_value().encode("utf-8")) < 32
        ):
            return "access_configuration_invalid"
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
