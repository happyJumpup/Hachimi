import json
import logging

ALLOWED_LOG_FIELDS = frozenset(
    {
        "run_id",
        "source_id",
        "stage",
        "elapsed_ms",
        "model_version",
        "skill_version",
        "provider_request_id",
        "error_code",
    }
)


def log_safe_fields(logger: logging.Logger, **fields: object) -> None:
    if not logger.isEnabledFor(logging.INFO):
        return
    payload = {
        key: value
        for key, value in fields.items()
        if key in ALLOWED_LOG_FIELDS and value is not None
    }
    logger.info(json.dumps(payload, ensure_ascii=True, separators=(",", ":")))
