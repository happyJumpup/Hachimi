from pathlib import Path

import pytest
from pydantic import SecretStr, ValidationError

from hakimi_analysis.settings import Settings


def test_test_provider_is_rejected_outside_test_environment() -> None:
    with pytest.raises(ValidationError):
        Settings(_env_file=None, app_env="development", analysis_provider="test")


def test_test_provider_is_allowed_only_in_test_environment() -> None:
    settings = Settings(_env_file=None, app_env="test", analysis_provider="test")
    assert settings.analysis_provider == "test"


def test_analysis_capacity_has_safe_production_defaults() -> None:
    settings = Settings(_env_file=None)
    assert settings.judge_analysis_concurrency == 3
    assert settings.public_analysis_concurrency == 0


def test_default_development_cors_origins_match_vite_loopback_hosts() -> None:
    settings = Settings(_env_file=None)

    assert settings.cors_origin_list == [
        "http://localhost:5173",
        "http://127.0.0.1:5173",
    ]


def test_content_provider_budget_has_a_bounded_five_minute_default() -> None:
    settings = Settings(_env_file=None)

    assert settings.analysis_speech_timeout_seconds == 45.0
    assert settings.analysis_evidence_deadline_seconds == 170.0
    assert settings.analysis_cleanup_reserve_seconds == 10.0
    assert settings.analysis_chunk_timeout_seconds == 20.0
    assert settings.analysis_visual_chunk_seconds == 60.0
    assert settings.analysis_visual_overlap_seconds == 10.0
    assert settings.analysis_max_visual_chunks == 6
    assert settings.analysis_max_attempts_per_visual_provider == 2
    assert settings.analysis_max_visual_calls == 12
    assert settings.analysis_latency_target_max_seconds_per_video_minute == 15.0

    with pytest.raises(ValidationError):
        Settings(_env_file=None, analysis_speech_timeout_seconds=0)
    with pytest.raises(ValidationError):
        Settings(_env_file=None, analysis_latency_target_max_seconds_per_video_minute=0)
    with pytest.raises(ValidationError):
        Settings(
            _env_file=None,
            analysis_visual_chunk_seconds=60,
            analysis_visual_overlap_seconds=60,
        )


def test_local_analysis_defaults_to_300_seconds_and_has_a_300_second_hard_cap() -> None:
    settings = Settings(_env_file=None)

    assert settings.local_upload_enabled is True
    assert settings.local_analysis_max_seconds == 300
    assert settings.published_analysis_max_seconds == 60
    assert settings.local_upload_max_bytes == 19 * 1024 * 1024
    assert settings.analysis_max_source_bytes == 256 * 1024 * 1024

    configured = Settings(_env_file=None, local_analysis_max_seconds=300)
    assert configured.local_analysis_max_seconds == 300
    with pytest.raises(ValidationError):
        Settings(_env_file=None, local_analysis_max_seconds=300.001)
    with pytest.raises(ValidationError, match="cannot exceed"):
        Settings(
            _env_file=None,
            local_analysis_max_seconds=60,
            published_analysis_max_seconds=300,
        )


def test_web_static_root_is_an_optional_backend_path(tmp_path: Path) -> None:
    without_static = Settings(_env_file=None)
    with_static = Settings(_env_file=None, web_static_root=tmp_path / "dist")

    assert without_static.web_static_root is None
    assert with_static.web_static_root == tmp_path / "dist"


def test_production_ffmpeg_is_an_explicit_optional_path(tmp_path: Path) -> None:
    without_ffmpeg = Settings(_env_file=None)
    with_ffmpeg = Settings(_env_file=None, imageio_ffmpeg_exe=tmp_path / "ffmpeg")

    assert without_ffmpeg.imageio_ffmpeg_exe is None
    assert with_ffmpeg.imageio_ffmpeg_exe == tmp_path / "ffmpeg"


def test_ffmpeg_audit_hashes_are_normalized_and_validated() -> None:
    settings = Settings(
        _env_file=None,
        ffmpeg_expected_sha256=f" {'A' * 64} ",
        ffmpeg_expected_configuration_sha256="B" * 64,
    )

    assert settings.ffmpeg_expected_sha256 == "a" * 64
    assert settings.ffmpeg_expected_configuration_sha256 == "b" * 64

    with pytest.raises(ValidationError):
        Settings(_env_file=None, ffmpeg_expected_sha256="not-a-sha256")


def test_trusted_proxy_cidrs_are_split_without_wildcard_defaults() -> None:
    default = Settings(_env_file=None)
    configured = Settings(
        _env_file=None,
        trusted_proxy_cidrs="172.30.248.2/32, 2001:db8::1/128",
    )

    assert default.trusted_proxy_cidr_list == []
    assert configured.trusted_proxy_cidr_list == ["172.30.248.2/32", "2001:db8::1/128"]

    with pytest.raises(ValidationError):
        Settings(_env_file=None, trusted_proxy_cidrs="0.0.0.0/0")


def test_secret_values_are_redacted_from_settings_repr() -> None:
    settings = Settings(
        _env_file=None,
        app_env="development",
        analysis_provider="cloud",
        ark_api_key=SecretStr("ark-secret-value"),
        volc_asr_api_key=SecretStr("asr-secret-value"),
        judge_access_code=SecretStr("judge-secret-value"),
        access_cookie_secret=SecretStr("cookie-secret-value"),
    )
    representation = repr(settings)
    assert "ark-secret-value" not in representation
    assert "asr-secret-value" not in representation
    assert "judge-secret-value" not in representation
    assert "cookie-secret-value" not in representation


def test_visual_fallback_is_fail_closed_without_qwen_and_private_cos() -> None:
    assert Settings(_env_file=None).visual_fallback_enabled is False

    with pytest.raises(ValidationError, match="visual fallback"):
        Settings(_env_file=None, visual_fallback_enabled=True)

    settings = Settings(
        _env_file=None,
        visual_fallback_enabled=True,
        qwen_api_key="qwen-key",
        cos_secret_id="cos-id",
        cos_secret_key="cos-key",
        cos_region="ap-guangzhou",
        cos_bucket="private-bucket-123",
    )

    assert settings.qwen_visual_model_id == "qwen3-vl-flash-2026-01-22"
    assert settings.cos_signed_url_ttl_seconds == 600
    assert settings.cos_lifecycle_days == 1
