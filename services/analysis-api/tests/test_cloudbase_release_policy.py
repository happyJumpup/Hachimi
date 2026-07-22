from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[3]
POLICY_SCRIPT = PROJECT_ROOT / "deploy" / "cloudbase" / "release-policy.py"


def run_policy(payload: dict[str, object]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(POLICY_SCRIPT)],
        input=json.dumps(payload),
        capture_output=True,
        check=False,
        encoding="utf-8",
    )


def test_release_policy_preserves_public_access_while_candidate_starts_at_zero() -> None:
    result = run_policy(
        {
            "access_types": ["OA", "PUBLIC"],
            "public_network_status": "ENABLE",
            "expected_current_version": "trainpal-demo-009",
            "online_versions": [
                {"version_name": "trainpal-demo-009", "flow_ratio": "100"}
            ],
        }
    )

    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {
        "access_types": ["OA", "PUBLIC"],
        "requested_candidate_initial_traffic_percent": 0,
        "verified_current_version": "trainpal-demo-009",
        "verified_current_flow_ratio": 100,
    }


def test_release_policy_preserves_an_oa_only_service() -> None:
    result = run_policy(
        {
            "access_types": ["OA"],
            "public_network_status": "DISABLE",
            "expected_current_version": "trainpal-demo-009",
            "online_versions": [
                {"version_name": "trainpal-demo-009", "flow_ratio": 100}
            ],
        }
    )

    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {
        "access_types": ["OA"],
        "requested_candidate_initial_traffic_percent": 0,
        "verified_current_version": "trainpal-demo-009",
        "verified_current_flow_ratio": 100,
    }


def test_release_policy_rejects_when_the_rollback_version_is_not_the_full_route() -> None:
    result = run_policy(
        {
            "access_types": ["OA", "PUBLIC"],
            "public_network_status": "ENABLE",
            "expected_current_version": "trainpal-demo-009",
            "online_versions": [
                {"version_name": "trainpal-demo-009", "flow_ratio": 90},
                {"version_name": "trainpal-demo-008", "flow_ratio": 10},
            ],
        }
    )

    assert result.returncode == 1
    assert "stable route" in result.stderr


def test_source_release_uses_the_validated_access_policy() -> None:
    source_release = (
        PROJECT_ROOT / "deploy" / "cloudbase" / "release-source.ps1"
    ).read_text(encoding="utf-8")

    assert "release-policy.py" in source_release
    assert "ArrayValue = @($releasePolicy.access_types)" in source_release
    assert "requested_candidate_initial_traffic_percent" in source_release
    assert "verified_current_flow_ratio" in source_release
    assert "$releasePolicy.candidate_initial_traffic_percent" not in source_release
    assert "Source release requires the service to remain OA-only." not in source_release
