from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[3]
POLICY_SCRIPT = PROJECT_ROOT / "deploy" / "cloudbase" / "canary-route-policy.py"
STATE_SCRIPT = PROJECT_ROOT / "deploy" / "cloudbase" / "canary-route-state.py"


def test_header_canary_keeps_the_stable_version_as_the_default_route() -> None:
    result = subprocess.run(
        [sys.executable, str(POLICY_SCRIPT)],
        input=json.dumps(
            {
                "mode": "enable",
                "stable_version": "trainpal-demo-009",
                "candidate_version": "trainpal-demo-010",
                "routing_header_name": "X-TrainPal-Canary",
                "routing_header_value": "private-route-token",
            }
        ),
        capture_output=True,
        check=False,
        encoding="utf-8",
    )

    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {
        "GrayType": "gray",
        "TrafficType": "HEADERS",
        "GrayFlowRatio": 0,
        "VersionFlowItems": [
            {
                "VersionName": "trainpal-demo-009",
                "IsDefaultPriority": True,
                "Priority": 1,
            },
            {
                "VersionName": "trainpal-demo-010",
                "UrlParam": {
                    "Key": "X-TrainPal-Canary",
                    "Value": "private-route-token",
                },
                "IsDefaultPriority": False,
                "Priority": 2,
            },
        ],
    }


def test_restore_route_returns_all_traffic_to_the_stable_version() -> None:
    result = subprocess.run(
        [sys.executable, str(POLICY_SCRIPT)],
        input=json.dumps(
            {
                "mode": "restore",
                "stable_version": "trainpal-demo-009",
                "candidate_version": "trainpal-demo-010",
            }
        ),
        capture_output=True,
        check=False,
        encoding="utf-8",
    )

    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {
        "GrayType": "gray",
        "TrafficType": "FLOW",
        "GrayFlowRatio": 0,
        "VersionFlowItems": [
            {
                "VersionName": "trainpal-demo-009",
                "FlowRatio": 100,
                "IsDefaultPriority": True,
                "Priority": 1,
            },
            {
                "VersionName": "trainpal-demo-010",
                "FlowRatio": 0,
                "IsDefaultPriority": False,
                "Priority": 2,
            },
        ],
    }


def test_route_setter_uses_the_validated_policy_and_redacts_the_token() -> None:
    route_setter = (
        PROJECT_ROOT / "deploy" / "cloudbase" / "set-canary-route.ps1"
    ).read_text(encoding="utf-8")

    assert "DescribeReleaseOrder" in route_setter
    assert "canary-route-policy.py" in route_setter
    assert "-Action 'ReleaseGray'" in route_setter
    assert "routing_header_value_sha256" in route_setter
    assert "routing_header_value =" not in route_setter
    assert "canary-route-state.py" in route_setter
    assert "Start-Sleep -Seconds 1" in route_setter
    assert "$Mode -eq 'enable' -and" in route_setter


def run_state_policy(payload: dict[str, object]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(STATE_SCRIPT)],
        input=json.dumps(payload),
        capture_output=True,
        check=False,
        encoding="utf-8",
    )


def release_order(*, traffic_type: str, route_value: str | None = None) -> dict[str, object]:
    candidate: dict[str, object] = {
        "VersionName": "trainpal-demo-010",
        "FlowRatio": 0,
        "Status": "running",
        "Remark": "git:" + "a" * 12,
        "IsDefaultPriority": False,
    }
    if route_value is not None:
        candidate["UrlParam"] = {"Key": "X-TrainPal-Canary", "Value": route_value}
    return {
        "TrafficType": traffic_type,
        "CurrentVersion": {
            "VersionName": "trainpal-demo-009",
            "FlowRatio": 100,
            "Status": "running",
            "IsDefaultPriority": True,
        },
        "ReleaseVersion": candidate,
    }


def test_route_state_policy_proves_the_header_route_is_active() -> None:
    result = run_state_policy(
        {
            "mode": "enable",
            "stable_version": "trainpal-demo-009",
            "candidate_commit_sha": "a" * 40,
            "routing_header_name": "X-TrainPal-Canary",
            "routing_header_value": "private-route-token",
            "release_order": release_order(
                traffic_type="HEADERS", route_value="private-route-token"
            ),
        }
    )

    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {
        "mode": "enable",
        "traffic_type": "HEADERS",
        "stable_version": "trainpal-demo-009",
        "candidate_version": "trainpal-demo-010",
        "stable_flow_ratio": 100,
        "candidate_flow_ratio": 0,
        "routing_header_name": "X-TrainPal-Canary",
        "routing_header_value_sha256": (
            "0aaeb90fe868b4f00fe67fc00c53d8d79de0932d8b698cbe94d664f5c57b33f4"
        ),
    }


def test_route_state_policy_proves_stable_traffic_was_restored() -> None:
    result = run_state_policy(
        {
            "mode": "restore",
            "stable_version": "trainpal-demo-009",
            "candidate_commit_sha": "a" * 40,
            "release_order": release_order(traffic_type="FLOW"),
        }
    )

    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["traffic_type"] == "FLOW"
    assert json.loads(result.stdout)["stable_flow_ratio"] == 100
    assert json.loads(result.stdout)["candidate_flow_ratio"] == 0


def test_restore_succeeds_even_when_the_candidate_has_stopped_running() -> None:
    order = release_order(traffic_type="FLOW")
    candidate = order["ReleaseVersion"]
    assert isinstance(candidate, dict)
    candidate["Status"] = "failed"
    result = run_state_policy(
        {
            "mode": "restore",
            "stable_version": "trainpal-demo-009",
            "candidate_commit_sha": "a" * 40,
            "release_order": order,
        }
    )

    assert result.returncode == 0, result.stderr


def test_route_state_policy_rejects_an_unverified_header_route() -> None:
    result = run_state_policy(
        {
            "mode": "enable",
            "stable_version": "trainpal-demo-009",
            "candidate_commit_sha": "a" * 40,
            "routing_header_name": "X-TrainPal-Canary",
            "routing_header_value": "private-route-token",
            "release_order": release_order(traffic_type="FLOW"),
        }
    )

    assert result.returncode == 1
    assert "private-route-token" not in result.stderr


def test_private_canary_wrapper_always_restores_stable_traffic() -> None:
    wrapper = (
        PROJECT_ROOT / "deploy" / "cloudbase" / "run-private-canary.ps1"
    ).read_text(encoding="utf-8")

    assert "try {" in wrapper
    assert "finally {" in wrapper
    assert "-Mode restore" in wrapper
    assert "private-canary.py" in wrapper
    assert "--routing-header-value-stdin" in wrapper
    assert "--expected-commit-sha" in wrapper
    assert "'--routing-header-value'," not in wrapper
