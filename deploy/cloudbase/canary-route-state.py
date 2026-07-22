from __future__ import annotations

import hashlib
import json
import re
import sys
from typing import Any


class RouteStateError(ValueError):
    pass


def _required_object(payload: dict[str, Any], key: str) -> dict[str, Any]:
    value = payload.get(key)
    if not isinstance(value, dict):
        raise RouteStateError("release order is missing required state")
    return value


def _required_string(payload: dict[str, Any], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value:
        raise RouteStateError("release order is missing required state")
    return value


def _required_ratio(payload: dict[str, Any], key: str) -> int:
    value = payload.get(key)
    if isinstance(value, bool):
        raise RouteStateError("release order has an invalid traffic ratio")
    try:
        ratio = int(value)
    except (TypeError, ValueError) as error:
        raise RouteStateError("release order has an invalid traffic ratio") from error
    if ratio < 0 or ratio > 100:
        raise RouteStateError("release order has an invalid traffic ratio")
    return ratio


def verify_route_state(payload: Any) -> dict[str, object]:
    if not isinstance(payload, dict):
        raise RouteStateError("route verification input must be a JSON object")
    mode = _required_string(payload, "mode")
    if mode not in {"enable", "restore"}:
        raise RouteStateError("route verification mode is invalid")
    stable_version = _required_string(payload, "stable_version")
    candidate_commit_sha = _required_string(payload, "candidate_commit_sha")
    if re.fullmatch(r"[0-9a-f]{40}", candidate_commit_sha) is None:
        raise RouteStateError("candidate commit identity is invalid")

    order = _required_object(payload, "release_order")
    stable = _required_object(order, "CurrentVersion")
    candidate = _required_object(order, "ReleaseVersion")
    candidate_version = _required_string(candidate, "VersionName")
    if _required_string(stable, "VersionName") != stable_version:
        raise RouteStateError("stable release identity is not verified")
    if candidate_version == stable_version:
        raise RouteStateError("candidate release identity is not distinct")
    if stable.get("Status") != "running":
        raise RouteStateError("stable release is not running")
    if mode == "enable" and candidate.get("Status") != "running":
        raise RouteStateError("candidate release is not running")
    if candidate.get("Remark") != f"git:{candidate_commit_sha[:12]}":
        raise RouteStateError("candidate release identity is not verified")
    if stable.get("IsDefaultPriority") is not True:
        raise RouteStateError("stable release is not the default route")
    if candidate.get("IsDefaultPriority") is not False:
        raise RouteStateError("candidate release unexpectedly became the default route")

    stable_ratio = _required_ratio(stable, "FlowRatio")
    candidate_ratio = _required_ratio(candidate, "FlowRatio")
    if stable_ratio != 100 or candidate_ratio != 0:
        raise RouteStateError("release traffic is not stable 100 and candidate 0")

    expected_traffic_type = "HEADERS" if mode == "enable" else "FLOW"
    if order.get("TrafficType") != expected_traffic_type:
        raise RouteStateError("release traffic type is not in the requested state")

    result: dict[str, object] = {
        "mode": mode,
        "traffic_type": expected_traffic_type,
        "stable_version": stable_version,
        "candidate_version": candidate_version,
        "stable_flow_ratio": stable_ratio,
        "candidate_flow_ratio": candidate_ratio,
        "routing_header_name": None,
        "routing_header_value_sha256": None,
    }
    if mode == "enable":
        header_name = _required_string(payload, "routing_header_name")
        header_value = _required_string(payload, "routing_header_value")
        route = _required_object(candidate, "UrlParam")
        if route.get("Key") != header_name or route.get("Value") != header_value:
            raise RouteStateError("candidate header route is not active")
        result["routing_header_name"] = header_name
        result["routing_header_value_sha256"] = hashlib.sha256(
            header_value.encode("utf-8")
        ).hexdigest()
    return result


def main() -> int:
    try:
        payload = json.load(sys.stdin)
        result = verify_route_state(payload)
    except (json.JSONDecodeError, RouteStateError) as error:
        print(f"route state rejected: {error}", file=sys.stderr)
        return 1
    print(json.dumps(result, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
