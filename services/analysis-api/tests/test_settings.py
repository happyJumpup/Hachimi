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
    assert settings.judge_analysis_concurrency == 2
    assert settings.public_analysis_concurrency == 0


def test_analysis_evidence_budget_has_a_bounded_positive_default() -> None:
    settings = Settings(_env_file=None)

    assert settings.analysis_evidence_timeout_seconds == 11.5
    assert settings.analysis_latency_target_max_seconds_per_video_minute == 15.0

    with pytest.raises(ValidationError):
        Settings(_env_file=None, analysis_evidence_timeout_seconds=0)
    with pytest.raises(ValidationError):
        Settings(_env_file=None, analysis_latency_target_max_seconds_per_video_minute=0)


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
