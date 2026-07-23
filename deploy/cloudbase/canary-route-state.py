from __future__ import annotations

import hashlib
import json
import re
import sys
from typing import Any


class RouteStateError(ValueError):
    pass


ACTIVE_VERSION_STATUSES = {"normal", "running"}


def _required_object(payload: dict[str, Any], key: str) -> dict[str, Any]:
    value = payload.get(key)
    if not isinstance(value, dict):
        raise RouteStateError("release order is missing required state")
    return value


def _required_list(payload: dict[str, Any], key: str) -> list[Any]:
    value = payload.get(key)
    if not isinstance(value, list):
        raise RouteStateError("release state is missing a required list")
    return value


def _optional_list(payload: dict[str, Any], key: str) -> list[Any]:
    value = payload.get(key)
    if value is None:
        return []
    if not isinstance(value, list):
        raise RouteStateError("release state has an invalid optional list")
    return value


def _required_string(payload: dict[str, Any], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value:
        raise RouteStateError("release order is missing required state")
    return value


def _required_ratio(payload: dict[str, Any], key: str) -> int:
    value = payload.get(key)
    if type(value) is not int:
        raise RouteStateError("release order has an invalid traffic ratio")
    if value < 0 or value > 100:
        raise RouteStateError("release order has an invalid traffic ratio")
    return value


def _verify_percentage_routes(
    online_versions: list[Any],
    *,
    stable_version: str,
    candidate_version: str,
    require_stable_route: bool,
) -> None:
    ratios: dict[str, int] = {}
    for item in online_versions:
        if not isinstance(item, dict):
            raise RouteStateError("online route state is invalid")
        version_name = _required_string(item, "VersionName")
        if version_name in ratios:
            raise RouteStateError("online route state contains duplicate versions")
        ratio = _required_ratio(item, "FlowRatio")
        ratios[version_name] = ratio
        if version_name == candidate_version and ratio != 0:
            raise RouteStateError("candidate unexpectedly received percentage traffic")
        if version_name not in {stable_version, candidate_version} and ratio != 0:
            raise RouteStateError("an unknown version received percentage traffic")
    if require_stable_route:
        if ratios.get(stable_version) != 100:
            raise RouteStateError("online traffic is not stable 100 and candidate 0")
        if ratios.get(candidate_version, 0) != 0:
            raise RouteStateError("online traffic is not stable 100 and candidate 0")


def _verify_exact_percentage_routes(
    online_versions: list[Any],
    *,
    stable_version: str,
    candidate_version: str,
    stable_ratio: int,
    candidate_ratio: int,
) -> None:
    ratios: dict[str, int] = {}
    for item in online_versions:
        if not isinstance(item, dict):
            raise RouteStateError("online route state is invalid")
        version_name = _required_string(item, "VersionName")
        if version_name in ratios:
            raise RouteStateError("online route state contains duplicate versions")
        ratios[version_name] = _required_ratio(item, "FlowRatio")
    if set(ratios) != {stable_version, candidate_version}:
        raise RouteStateError("online routes do not exactly match stable and candidate")
    if (
        ratios[stable_version] != stable_ratio
        or ratios[candidate_version] != candidate_ratio
    ):
        raise RouteStateError("online traffic does not match the requested promotion")


def verify_route_state(payload: Any) -> dict[str, object]:
    if not isinstance(payload, dict):
        raise RouteStateError("route verification input must be a JSON object")
    mode = _required_string(payload, "mode")
    if mode not in {"enable", "promote", "restore"}:
        raise RouteStateError("route verification mode is invalid")
    if mode != "enable" and (
        "routing_header_name" in payload or "routing_header_value" in payload
    ):
        raise RouteStateError("FLOW route modes do not accept routing headers")
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
    if stable.get("Status") not in ACTIVE_VERSION_STATUSES:
        raise RouteStateError("stable release is not running")
    if (
        mode in {"enable", "promote"}
        and candidate.get("Status") not in ACTIVE_VERSION_STATUSES
    ):
        raise RouteStateError("candidate release is not running")
    if (
        mode in {"enable", "promote"}
        and payload.get("candidate_identity_verified") is not True
    ):
        raise RouteStateError("candidate release identity is not verified")
    candidate_remark = candidate.get("Remark")
    if (
        isinstance(candidate_remark, str)
        and candidate_remark
        and not candidate_remark.startswith(f"git:{candidate_commit_sha[:12]}")
    ):
        raise RouteStateError("candidate release identity is inconsistent")
    expected_traffic_type = "HEADERS" if mode == "enable" else "FLOW"
    if order.get("TrafficType") != expected_traffic_type:
        raise RouteStateError("release traffic type is not in the requested state")

    online_versions = _required_list(payload, "online_versions")
    if mode == "enable":
        if payload.get("stable_route_verified_before_mutation") is not True:
            raise RouteStateError("stable traffic was not verified before mutation")
        # CloudBase currently omits OnlineVersionInfos while HEADERS routing is
        # active. If it returns entries, they must still be percentage-safe.
        _verify_percentage_routes(
            online_versions,
            stable_version=stable_version,
            candidate_version=candidate_version,
            require_stable_route=bool(online_versions),
        )
        if _required_ratio(stable, "FlowRatio") != 0:
            raise RouteStateError("stable version has unexpected header-mode traffic")
        if _required_ratio(candidate, "FlowRatio") != 0:
            raise RouteStateError("candidate unexpectedly received percentage traffic")
    elif mode == "promote":
        if payload.get("stable_route_verified_before_mutation") is not True:
            raise RouteStateError("stable traffic was not verified before mutation")
        _verify_exact_percentage_routes(
            online_versions,
            stable_version=stable_version,
            candidate_version=candidate_version,
            stable_ratio=0,
            candidate_ratio=100,
        )
        if _required_ratio(stable, "FlowRatio") != 0:
            raise RouteStateError("stable release order ratio is not zero")
        if _required_ratio(candidate, "FlowRatio") != 100:
            raise RouteStateError("candidate release order ratio is not 100")
        if stable.get("IsDefaultPriority") is not True:
            raise RouteStateError("stable release order priority is invalid")
        if candidate.get("IsDefaultPriority") is not False:
            raise RouteStateError("candidate release order priority is invalid")
        traffic_values = _optional_list(order, "TrafficTypeValues")
        if traffic_values:
            raise RouteStateError("candidate header route is still active")
    else:
        _verify_percentage_routes(
            online_versions,
            stable_version=stable_version,
            candidate_version=candidate_version,
            require_stable_route=True,
        )
    result: dict[str, object] = {
        "mode": mode,
        "traffic_type": expected_traffic_type,
        "stable_version": stable_version,
        "candidate_version": candidate_version,
        "stable_flow_ratio": 0 if mode == "promote" else 100,
        "candidate_flow_ratio": 100 if mode == "promote" else 0,
        "routing_header_name": None,
        "routing_header_value_sha256": None,
    }
    if mode == "enable":
        header_name = _required_string(payload, "routing_header_name")
        header_value = _required_string(payload, "routing_header_value")
        traffic_values = _required_list(order, "TrafficTypeValues")
        if len(traffic_values) != 1 or not isinstance(traffic_values[0], dict):
            raise RouteStateError("candidate header route is not uniquely active")
        route = traffic_values[0]
        if route.get("Key") != header_name or route.get("Value") != header_value:
            raise RouteStateError("candidate header route is not active")
        result["routing_header_name"] = header_name
        result["routing_header_value_sha256"] = hashlib.sha256(
            header_value.encode("utf-8")
        ).hexdigest()
    else:
        traffic_values = _optional_list(order, "TrafficTypeValues")
        if traffic_values:
            raise RouteStateError("candidate header route is still active")
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
