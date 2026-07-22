import hashlib
import json
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
from hakimi_analysis.provider_contracts import PromptContractError, PromptContractRegistry
from hakimi_analysis.provider_profile import ProviderProfile
from hakimi_analysis.release_gates import (
    validate_five_minute_canary_receipt,
    validate_five_minute_canary_receipt_json,
    validate_provider_conformance_report_json,
)
from hakimi_analysis.settings import Settings
from hakimi_analysis.sources import SourceCatalog

FFMPEG_VERSION_TIMEOUT_SECONDS = 5
FFMPEG_CONFIGURATION_PREFIX = "configuration:"
FORBIDDEN_FFMPEG_CONFIGURATION_FLAGS = frozenset({"--enable-gpl", "--enable-nonfree"})
FFMPEG_RELEASE_VERSION = "8.1.2"
FFMPEG_RELEASE_FINGERPRINT = "FCF986EA15E6E293A5644F10B4322F04D67658D8"
FFMPEG_RELEASE_SOURCE_URL = (
    f"https://ffmpeg.org/releases/ffmpeg-{FFMPEG_RELEASE_VERSION}.tar.xz"
)
FFMPEG_RECEIPT_KEYS = {
    "schema_version",
    "version",
    "source_url",
    "signing_key_fingerprint",
    "binary_sha256",
    "configuration_line",
    "configuration_sha256",
}
PRODUCTION_SPEECH_MODEL_ID = "doubao-seed-2-0-mini-260428"
PRODUCTION_VISUAL_MODEL_IDS = {
    "doubao-seed-2-0-mini-260428",
    "doubao-seed-2-0-lite-260428",
}
PRODUCTION_FALLBACK_MODEL_ID = "qwen3-vl-flash-2026-01-22"


def validate_ffmpeg_runtime(
    executable: Path,
    expected_sha256: str,
    expected_configuration_sha256: str,
    *,
    expected_version: str | None = None,
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
    version_tokens = lines[0].split()
    if expected_version is not None and (
        len(version_tokens) < 3 or version_tokens[2] != expected_version
    ):
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


def validate_ffmpeg_build_receipt(executable: Path, receipt_path: Path) -> bool:
    try:
        resolved_executable = executable.resolve(strict=True)
        resolved_receipt = receipt_path.resolve(strict=True)
        if resolved_executable != resolved_receipt.parent / "bin" / "ffmpeg":
            return False
        payload = json.loads(resolved_receipt.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    if not isinstance(payload, dict) or set(payload) != FFMPEG_RECEIPT_KEYS:
        return False
    if (
        payload.get("schema_version") != 1
        or payload.get("version") != FFMPEG_RELEASE_VERSION
        or payload.get("source_url") != FFMPEG_RELEASE_SOURCE_URL
        or payload.get("signing_key_fingerprint") != FFMPEG_RELEASE_FINGERPRINT
    ):
        return False
    binary_sha256 = payload.get("binary_sha256")
    configuration_line = payload.get("configuration_line")
    configuration_sha256 = payload.get("configuration_sha256")
    if not all(
        isinstance(value, str)
        for value in (binary_sha256, configuration_line, configuration_sha256)
    ):
        return False
    if not str(configuration_line).startswith(FFMPEG_CONFIGURATION_PREFIX):
        return False
    if hashlib.sha256(str(configuration_line).encode("utf-8")).hexdigest() != str(
        configuration_sha256
    ):
        return False
    return validate_ffmpeg_runtime(
        resolved_executable,
        str(binary_sha256),
        str(configuration_sha256),
        expected_version=FFMPEG_RELEASE_VERSION,
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
        contracts_root: Path,
        duration_probe: Callable[[Path], float] | None = None,
        ffmpeg_receipt_probe: Callable[[Path, Path], bool] | None = None,
        fallback_probe: Callable[[], bool] | None = None,
        cache_seconds: float = 5,
    ) -> None:
        self._settings = settings
        self._catalog = catalog
        self._temp_root = temp_root
        self._contracts_root = contracts_root
        self._duration_probe = duration_probe or probe_duration_sync
        self._ffmpeg_receipt_probe = ffmpeg_receipt_probe or validate_ffmpeg_build_receipt
        self._fallback_probe = fallback_probe
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
            self._settings.ark_model_id != PRODUCTION_SPEECH_MODEL_ID
            or self._settings.ark_visual_model_id not in PRODUCTION_VISUAL_MODEL_IDS
        ):
            return "provider_configuration_invalid"
        if self._settings.visual_fallback_enabled:
            qwen_url = urlparse(self._settings.qwen_base_url)
            if (
                qwen_url.scheme != "https"
                or not qwen_url.netloc
                or self._settings.qwen_visual_model_id != PRODUCTION_FALLBACK_MODEL_ID
                or self._fallback_probe is None
                or not self._fallback_probe()
            ):
                return "visual_fallback_configuration_invalid"
        controlled_source_values = (
            self._settings.source_manifest_path,
            self._settings.source_media_root,
            self._settings.public_media_base_url,
        )
        controlled_sources_configured = any(value is not None for value in controlled_source_values)
        if controlled_sources_configured:
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
        elif not self._settings.local_upload_enabled:
            return "source_manifest_invalid"
        ffmpeg_executable = self._settings.imageio_ffmpeg_exe
        ffmpeg_receipt = self._settings.ffmpeg_build_receipt_path
        receipt_valid = (
            ffmpeg_executable is not None
            and ffmpeg_receipt is not None
            and self._ffmpeg_receipt_probe(ffmpeg_executable, ffmpeg_receipt)
        )
        if not receipt_valid:
            return "media_processor_unavailable"
        if controlled_sources_configured and not self._catalog.validate_media(self._duration_probe):
            return "media_cache_invalid"
        try:
            visual_model_ids: tuple[str, ...] = (self._settings.ark_visual_model_id,)
            if self._settings.visual_fallback_enabled:
                visual_model_ids += (self._settings.qwen_visual_model_id,)
            contracts = PromptContractRegistry.load(
                self._contracts_root,
                speech_model_id=self._settings.ark_model_id,
                visual_model_ids=visual_model_ids,
            )
        except (OSError, PromptContractError):
            return "prompt_contract_invalid"
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
        if not self._temp_storage_available():
            return "temp_storage_unavailable"
        if not self._competition_profile_valid(
            visual_prompt_sha256=contracts.visual.prompt_sha256
        ):
            return "competition_configuration_invalid"
        return None

    def _competition_profile_valid(self, *, visual_prompt_sha256: str) -> bool:
        try:
            profile = ProviderProfile.from_settings(self._settings)
        except ValueError:
            return False
        return (
            self._settings.local_upload_enabled
            and profile.max_source_seconds == 300
            and profile.max_source_bytes == 256 * 1024 * 1024
            and profile.visual_attempt_timeout_seconds == 20
            and profile.speech_timeout_seconds == 45
            and profile.evidence_deadline_seconds == 170
            and profile.cleanup_reserve_seconds == 10
            and profile.visual_chunk_seconds == 60
            and profile.visual_overlap_seconds == 10
            and profile.max_visual_chunks == 6
            and profile.max_attempts_per_visual_provider == 2
            and profile.max_visual_calls == 12
            and profile.run_timeout_seconds == 180
            and profile.judge_concurrency == 3
            and profile.public_concurrency == 0
            and not self._settings.trusted_proxy_cidr_list
            and self._published_capability_valid(
                visual_prompt_sha256=visual_prompt_sha256
            )
        )

    def _published_capability_valid(self, *, visual_prompt_sha256: str) -> bool:
        published = self._settings.published_analysis_max_seconds
        if published == 60:
            return True
        if published != 300 or not self._settings.visual_fallback_enabled:
            return False
        conformance_report = self._settings.provider_conformance_report_json.strip()
        conformance_sha256 = validate_provider_conformance_report_json(
            conformance_report,
            expected_prompt_sha256=visual_prompt_sha256,
            expected_primary_model_id=self._settings.ark_visual_model_id,
            expected_fallback_model_id=self._settings.qwen_visual_model_id,
        )
        if conformance_sha256 is None:
            return False
        receipt_json = self._settings.provider_canary_receipt_json.strip()
        return (
            validate_five_minute_canary_receipt_json(
                receipt_json,
                expected_commit_sha=self._settings.deployment_commit_sha,
                expected_primary_model_id=self._settings.ark_visual_model_id,
                expected_fallback_model_id=self._settings.qwen_visual_model_id,
                expected_conformance_report_sha256=conformance_sha256,
            )
            if receipt_json
            else validate_five_minute_canary_receipt(
                self._settings.provider_canary_receipt_path,
                expected_commit_sha=self._settings.deployment_commit_sha,
                expected_primary_model_id=self._settings.ark_visual_model_id,
                expected_fallback_model_id=self._settings.qwen_visual_model_id,
                expected_conformance_report_sha256=conformance_sha256,
            )
        )

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
