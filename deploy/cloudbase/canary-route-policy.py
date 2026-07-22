from __future__ import annotations

import json
import re
import sys
from typing import Any


VERSION_PATTERN = re.compile(r"^[A-Za-z0-9_-]{3,80}$")
HEADER_NAME_PATTERN = re.compile(r"^[A-Za-z0-9-]{1,64}$")
HEADER_VALUE_PATTERN = re.compile(r"^[A-Za-z0-9_-]{16,128}$")


class CanaryRoutePolicyError(ValueError):
    pass


def _required_string(payload: dict[str, Any], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value:
        raise CanaryRoutePolicyError(f"{key} must be a non-empty string")
    return value


def resolve_canary_route(payload: Any) -> dict[str, object]:
    if not isinstance(payload, dict):
        raise CanaryRoutePolicyError("canary route input must be a JSON object")
    mode = payload.get("mode")
    if mode not in {"enable", "restore"}:
        raise CanaryRoutePolicyError("route mode must be enable or restore")
    stable_version = _required_string(payload, "stable_version")
    candidate_version = _required_string(payload, "candidate_version")
    if not VERSION_PATTERN.fullmatch(stable_version) or not VERSION_PATTERN.fullmatch(
        candidate_version
    ):
        raise CanaryRoutePolicyError("version name has an invalid format")
    if stable_version == candidate_version:
        raise CanaryRoutePolicyError("stable and candidate versions must differ")
    if mode == "restore":
        return {
            "GrayType": "gray",
            "TrafficType": "FLOW",
            "GrayFlowRatio": 0,
            "VersionFlowItems": [
                {
                    "VersionName": stable_version,
                    "FlowRatio": 100,
                    "IsDefaultPriority": True,
                    "Priority": 1,
                },
                {
                    "VersionName": candidate_version,
                    "FlowRatio": 0,
                    "IsDefaultPriority": False,
                    "Priority": 2,
                },
            ],
        }
    header_name = _required_string(payload, "routing_header_name")
    header_value = _required_string(payload, "routing_header_value")
    if not HEADER_NAME_PATTERN.fullmatch(header_name):
        raise CanaryRoutePolicyError("routing header name has an invalid format")
    if not HEADER_VALUE_PATTERN.fullmatch(header_value):
        raise CanaryRoutePolicyError("routing header value has an invalid format")
    return {
        "GrayType": "gray",
        "TrafficType": "HEADERS",
        "GrayFlowRatio": 0,
        "VersionFlowItems": [
            {
                "VersionName": stable_version,
                "IsDefaultPriority": True,
                "Priority": 1,
            },
            {
                "VersionName": candidate_version,
                "UrlParam": {"Key": header_name, "Value": header_value},
                "IsDefaultPriority": False,
                "Priority": 2,
            },
        ],
    }


def main() -> int:
    try:
        payload = json.load(sys.stdin)
        route = resolve_canary_route(payload)
    except (json.JSONDecodeError, CanaryRoutePolicyError) as error:
        print(f"canary route policy rejected: {error}", file=sys.stderr)
        return 1
    print(json.dumps(route, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
