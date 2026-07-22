import re
from ipaddress import ip_network
from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parents[4]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=PROJECT_ROOT / ".env.local",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    app_env: Literal["development", "test", "production"] = "development"
    analysis_provider: Literal["cloud", "test"] = "cloud"
    ark_api_key: SecretStr | None = None
    ark_model_id: str = "doubao-seed-2-0-mini-260428"
    ark_visual_model_id: str = "doubao-seed-2-0-mini-260428"
    ark_base_url: str = "https://ark.cn-beijing.volces.com/api/v3"
    visual_fallback_enabled: bool = False
    qwen_api_key: SecretStr | None = None
    qwen_visual_model_id: str = "qwen3-vl-flash-2026-01-22"
    qwen_base_url: str = "https://dashscope.aliyuncs.com/compatible-mode/v1"
    cos_secret_id: SecretStr | None = None
    cos_secret_key: SecretStr | None = None
    cos_region: str = ""
    cos_bucket: str = ""
    cos_object_prefix: str = "trainpal/visual-fallback"
    cos_signed_url_ttl_seconds: int = Field(default=600, ge=60, le=3600)
    cos_lifecycle_days: int = Field(default=1, ge=1, le=1)
    volc_asr_api_key: SecretStr | None = None
    volc_asr_resource_id: str = "volc.seedasr.sauc.duration"
    volc_asr_url: str = "wss://openspeech.bytedance.com/api/v3/sauc/bigmodel_nostream"
    hakimi_demo_video_path: Path | None = None
    source_manifest_path: Path | None = None
    source_media_root: Path | None = None
    public_media_base_url: str | None = None
    smoke_annotations_path: Path | None = None
    web_static_root: Path | None = None
    imageio_ffmpeg_exe: Path | None = None
    ffmpeg_build_receipt_path: Path | None = None
    ffmpeg_expected_sha256: str | None = None
    ffmpeg_expected_configuration_sha256: str | None = None
    judge_access_code: SecretStr | None = None
    access_cookie_secret: SecretStr | None = None
    judge_analysis_concurrency: int = Field(default=3, ge=0)
    public_analysis_concurrency: int = Field(default=0, ge=0)
    trusted_proxy_cidrs: str = ""
    cors_origins: str = "http://localhost:5173,http://127.0.0.1:5173"
    run_ttl_seconds: int = Field(default=600, ge=1)
    run_timeout_seconds: int = Field(default=180, ge=1)
    local_upload_enabled: bool = True
    local_analysis_max_seconds: float = Field(default=300, gt=0, le=300)
    local_upload_max_bytes: int = Field(default=256 * 1024 * 1024, ge=1)
    analysis_speech_timeout_seconds: float = Field(default=45.0, gt=0)
    analysis_evidence_deadline_seconds: float = Field(default=170.0, gt=0)
    analysis_cleanup_reserve_seconds: float = Field(default=10.0, ge=0)
    analysis_chunk_timeout_seconds: float = Field(default=20.0, gt=0)
    analysis_visual_chunk_seconds: float = Field(default=60.0, gt=0, le=300)
    analysis_visual_overlap_seconds: float = Field(default=10.0, ge=0)
    analysis_max_visual_chunks: int = Field(default=6, ge=1)
    analysis_max_attempts_per_visual_provider: int = Field(default=2, ge=1)
    analysis_max_visual_calls: int = Field(default=12, ge=1)
    analysis_latency_target_max_seconds_per_video_minute: float = Field(default=15.0, gt=0)

    @model_validator(mode="after")
    def reject_runtime_test_provider(self) -> "Settings":
        if self.analysis_provider == "test" and self.app_env != "test":
            raise ValueError("test analysis provider is allowed only when APP_ENV=test")
        if self.analysis_visual_overlap_seconds >= self.analysis_visual_chunk_seconds:
            raise ValueError("visual chunk overlap must be shorter than the chunk")
        if self.visual_fallback_enabled and not self._fallback_fields_present():
            raise ValueError("visual fallback requires Qwen and private COS configuration")
        return self

    def _fallback_fields_present(self) -> bool:
        secrets_present = all(
            secret is not None and bool(secret.get_secret_value())
            for secret in (self.qwen_api_key, self.cos_secret_id, self.cos_secret_key)
        )
        return secrets_present and all(
            value.strip()
            for value in (
                self.qwen_visual_model_id,
                self.qwen_base_url,
                self.cos_region,
                self.cos_bucket,
                self.cos_object_prefix,
            )
        )

    @field_validator("trusted_proxy_cidrs")
    @classmethod
    def validate_trusted_proxy_cidrs(cls, value: str) -> str:
        for entry in (item.strip() for item in value.split(",")):
            if entry:
                network = ip_network(entry, strict=False)
                if network.prefixlen == 0:
                    raise ValueError("trusted proxy CIDR must not trust every address")
        return value

    @field_validator(
        "ffmpeg_expected_sha256",
        "ffmpeg_expected_configuration_sha256",
        mode="before",
    )
    @classmethod
    def validate_optional_sha256(cls, value: object) -> str | None:
        if value is None:
            return None
        normalized = str(value).strip().lower()
        if not normalized:
            return None
        if re.fullmatch(r"[0-9a-f]{64}", normalized) is None:
            raise ValueError("expected a 64-character SHA-256 digest")
        return normalized

    @property
    def cors_origin_list(self) -> list[str]:
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]

    @property
    def trusted_proxy_cidr_list(self) -> list[str]:
        return [entry.strip() for entry in self.trusted_proxy_cidrs.split(",") if entry.strip()]
