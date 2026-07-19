from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr, model_validator
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
    ark_model_id: str = "doubao-seed-2-0-lite-260215"
    ark_base_url: str = "https://ark.cn-beijing.volces.com/api/v3"
    volc_asr_api_key: SecretStr | None = None
    volc_asr_resource_id: str = "volc.bigasr.auc_turbo"
    volc_asr_url: str = (
        "https://openspeech.bytedance.com/api/v3/auc/bigmodel/recognize/flash"
    )
    hakimi_demo_video_path: Path | None = None
    cors_origins: str = "http://localhost:5173"
    run_ttl_seconds: int = Field(default=600, ge=1)
    run_timeout_seconds: int = Field(default=180, ge=1)

    @model_validator(mode="after")
    def reject_runtime_test_provider(self) -> "Settings":
        if self.analysis_provider == "test" and self.app_env != "test":
            raise ValueError("test analysis provider is allowed only when APP_ENV=test")
        return self

    @property
    def cors_origin_list(self) -> list[str]:
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]
