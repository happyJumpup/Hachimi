from __future__ import annotations

import json
import re
import sys
from typing import Any


class ReleasePolicyError(ValueError):
    pass


def _flow_ratio(value: object) -> int:
    if isinstance(value, bool):
        raise ReleasePolicyError("online version has an invalid flow ratio")
    if isinstance(value, int):
        ratio = value
    elif isinstance(value, str) and re.fullmatch(r"[0-9]{1,3}", value):
        ratio = int(value)
    else:
        raise ReleasePolicyError("online version has an invalid flow ratio")
    if ratio < 0 or ratio > 100:
        raise ReleasePolicyError("online version has an invalid flow ratio")
    return ratio


def resolve_release_policy(payload: Any) -> dict[str, object]:
    if not isinstance(payload, dict):
        raise ReleasePolicyError("release policy input must be a JSON object")
    access_types = payload.get("access_types")
    public_network_status = payload.get("public_network_status")
    if access_types == ["OA"] and public_network_status == "DISABLE":
        preserved_access_types = ["OA"]
    elif access_types == ["OA", "PUBLIC"] and public_network_status == "ENABLE":
        preserved_access_types = ["OA", "PUBLIC"]
    else:
        raise ReleasePolicyError(
            "release access and public networking state are inconsistent"
        )
    expected_current_version = payload.get("expected_current_version")
    online_versions = payload.get("online_versions")
    if (
        not isinstance(expected_current_version, str)
        or not expected_current_version
        or not isinstance(online_versions, list)
        or not online_versions
    ):
        raise ReleasePolicyError("stable route verification input is incomplete")
    normalized_versions: list[tuple[str, int]] = []
    for entry in online_versions:
        if not isinstance(entry, dict):
            raise ReleasePolicyError("stable route verification input is invalid")
        version_name = entry.get("version_name")
        if not isinstance(version_name, str) or not version_name:
            raise ReleasePolicyError("stable route verification input is invalid")
        normalized_versions.append((version_name, _flow_ratio(entry.get("flow_ratio"))))
    matching = [
        ratio for version_name, ratio in normalized_versions if version_name == expected_current_version
    ]
    if (
        matching != [100]
        or sum(ratio for _, ratio in normalized_versions) != 100
        or any(
            ratio != 0
            for version_name, ratio in normalized_versions
            if version_name != expected_current_version
        )
    ):
        raise ReleasePolicyError("expected rollback version is not the verified stable route")
    return {
        "access_types": preserved_access_types,
        "requested_candidate_initial_traffic_percent": 0,
        "verified_current_version": expected_current_version,
        "verified_current_flow_ratio": 100,
    }


def main() -> int:
    try:
        payload = json.load(sys.stdin)
        policy = resolve_release_policy(payload)
    except (json.JSONDecodeError, ReleasePolicyError) as error:
        print(f"release policy rejected: {error}", file=sys.stderr)
        return 1
    print(json.dumps(policy, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
