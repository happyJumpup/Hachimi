from collections.abc import Mapping, Sequence

from hakimi_analysis.benchmark.models import RouteAggregate

_SAFE_DIAGNOSTIC_KEYS = {
    "request_id",
    "stage",
    "provider",
    "error_code",
    "proxy_mode",
    "dns_mode",
}


def sanitized_report(
    routes: Sequence[RouteAggregate],
    *,
    diagnostics: Mapping[str, object],
) -> dict[str, object]:
    safe_diagnostics = {
        key: value for key, value in diagnostics.items() if key in _SAFE_DIAGNOSTIC_KEYS
    }
    return {
        "version": 1,
        "scope": "local_quality_only",
        "routes": [route.model_dump(mode="json") for route in routes],
        "diagnostics": safe_diagnostics,
    }
