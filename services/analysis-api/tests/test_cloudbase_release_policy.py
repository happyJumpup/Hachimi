from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[3]
POLICY_SCRIPT = PROJECT_ROOT / "deploy" / "cloudbase" / "release-policy.py"
FOUNDATION_PLAN = PROJECT_ROOT / "deploy" / "cloudbase" / "foundation-plan.json"
FOUNDATION_VERIFIER = PROJECT_ROOT / "deploy" / "cloudbase" / "verify-foundation.py"

EXPECTED_RELEASE_MANAGED_SECRET_ALIASES = ["GYMTI_LLM_API_KEY"]
EXPECTED_RELEASE_MANAGED_NONSECRET_KEYS = [
    "APP_RELEASE_SHA",
    "GYMTI_LLM_BASE_URL",
    "GYMTI_LLM_CONCURRENCY",
    "GYMTI_LLM_ENABLED",
    "GYMTI_LLM_MODEL",
    "GYMTI_LLM_RETENTION_CONFIRMED",
    "PUBLIC_MEDIA_BASE_URL",
    "SOURCE_MANIFEST_PATH",
    "SOURCE_MEDIA_ROOT",
]


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
    assert "PUBLIC_ANALYSIS_CONCURRENCY = '1'" in source_release
    assert "JUDGE_ANALYSIS_CONCURRENCY = '0'" in source_release
    assert "GYMTI_LLM_CONCURRENCY = '3'" in source_release
    assert "SOURCE_MANIFEST_PATH = '/workspace/competition/media-manifest.json'" in source_release
    assert "SOURCE_MEDIA_ROOT = '/workspace/tmp/controlled-media'" in source_release
    assert "GYMTI_LLM_MODEL = 'doubao-seed-2-0-mini-260428'" in source_release
    assert "$environment['GYMTI_LLM_MODEL'] = $environment['ARK_MODEL_ID']" not in source_release
    assert "@{ Key = 'MinNum'; IntValue = 1 }" in source_release


def test_source_release_requires_an_explicit_provider_retention_confirmation() -> None:
    source_release = (
        PROJECT_ROOT / "deploy" / "cloudbase" / "release-source.ps1"
    ).read_text(encoding="utf-8")

    parameter = "[switch]$ConfirmGymtiProviderRetention"
    guard = "if (-not $ConfirmGymtiProviderRetention.IsPresent)"
    retention_override = "GYMTI_LLM_RETENTION_CONFIRMED = 'true'"

    assert parameter in source_release
    assert guard in source_release
    assert source_release.index(guard) < source_release.index(retention_override)


def test_foundation_separates_inherited_keys_from_release_managed_keys() -> None:
    foundation = json.loads(FOUNDATION_PLAN.read_text(encoding="utf-8"))

    assert foundation["release_managed_secret_alias_environment_keys"] == (
        EXPECTED_RELEASE_MANAGED_SECRET_ALIASES
    )
    assert foundation["release_managed_nonsecret_environment_keys"] == (
        EXPECTED_RELEASE_MANAGED_NONSECRET_KEYS
    )
    assert not (
        set(EXPECTED_RELEASE_MANAGED_SECRET_ALIASES)
        | set(EXPECTED_RELEASE_MANAGED_NONSECRET_KEYS)
    ) & (
        set(foundation["required_secret_environment_keys"])
        | set(foundation["required_nonsecret_environment_keys"])
    )

    result = subprocess.run(
        [sys.executable, str(FOUNDATION_VERIFIER), str(FOUNDATION_PLAN)],
        capture_output=True,
        check=False,
        encoding="utf-8",
    )

    assert result.returncode == 0, result.stderr


def test_foundation_rejects_release_managed_key_drift(tmp_path: Path) -> None:
    foundation = json.loads(FOUNDATION_PLAN.read_text(encoding="utf-8"))
    foundation["release_managed_nonsecret_environment_keys"].remove(
        "GYMTI_LLM_MODEL"
    )
    drifted_plan = tmp_path / "foundation-plan.json"
    drifted_plan.write_text(json.dumps(foundation), encoding="utf-8")

    result = subprocess.run(
        [sys.executable, str(FOUNDATION_VERIFIER), str(drifted_plan)],
        capture_output=True,
        check=False,
        encoding="utf-8",
    )

    assert result.returncode == 1
    assert "release-managed non-secret environment key contract changed" in result.stderr


def test_source_release_fails_closed_when_managed_key_names_drift() -> None:
    source_release = (
        PROJECT_ROOT / "deploy" / "cloudbase" / "release-source.ps1"
    ).read_text(encoding="utf-8")

    assert "$foundation.release_managed_secret_alias_environment_keys" in source_release
    assert "$foundation.release_managed_nonsecret_environment_keys" in source_release
    assert "Release-managed secret alias environment key contract drifted." in source_release
    assert "Release-managed non-secret environment key contract drifted." in source_release
