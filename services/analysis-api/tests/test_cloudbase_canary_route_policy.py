from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

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


def test_promote_route_sends_all_traffic_to_the_verified_candidate() -> None:
    result = subprocess.run(
        [sys.executable, str(POLICY_SCRIPT)],
        input=json.dumps(
            {
                "mode": "promote",
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
        "GrayFlowRatio": 100,
        "VersionFlowItems": [
            {
                "VersionName": "trainpal-demo-009",
                "FlowRatio": 0,
                "IsDefaultPriority": True,
                "Priority": 1,
            },
            {
                "VersionName": "trainpal-demo-010",
                "FlowRatio": 100,
                "IsDefaultPriority": False,
                "Priority": 2,
            },
        ],
    }


def test_promote_route_rejects_routing_headers_without_echoing_the_value() -> None:
    result = subprocess.run(
        [sys.executable, str(POLICY_SCRIPT)],
        input=json.dumps(
            {
                "mode": "promote",
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

    assert result.returncode == 1
    assert "private-route-token" not in result.stderr


def test_route_setter_uses_the_validated_policy_and_redacts_the_token() -> None:
    route_setter = (PROJECT_ROOT / "deploy" / "cloudbase" / "set-canary-route.ps1").read_text(
        encoding="utf-8"
    )

    assert "DescribeReleaseOrder" in route_setter
    assert "canary-route-policy.py" in route_setter
    assert "-Action 'ReleaseGray'" in route_setter
    assert "routing_header_value_sha256" in route_setter
    assert "routing_header_value =" not in route_setter
    assert "canary-route-state.py" in route_setter
    assert "Start-Sleep -Seconds 1" in route_setter
    assert "$Mode -eq 'enable' -and" in route_setter
    assert "DescribeVersionDetail" in route_setter
    assert "APP_RELEASE_SHA" in route_setter
    assert "DescribeCloudRunServerDetail" in route_setter
    assert "stable_route_verified_before_mutation" in route_setter
    assert "Convert-ExactTrafficRatio" in route_setter
    assert "$stateExitCode = $LASTEXITCODE" in route_setter
    assert "$ErrorActionPreference = 'Continue'" in route_setter
    assert "ValidateSet('enable', 'promote', 'restore')" in route_setter
    assert "$Mode -in @('enable', 'promote')" in route_setter
    assert "$Mode -ne 'enable' -and ($RoutingHeaderName -or $RoutingHeaderValue)" in route_setter
    assert "$normalizedOnlineVersions.Count -ne 2" in route_setter
    assert "Promotion can only start from exact stable 100 and candidate 0." in route_setter


def run_state_policy(payload: dict[str, object]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(STATE_SCRIPT)],
        input=json.dumps(payload),
        capture_output=True,
        check=False,
        encoding="utf-8",
    )


def release_order(
    *,
    traffic_type: str,
    route_value: str | None = None,
    stable_ratio: int = 0,
    candidate_ratio: int = 0,
) -> dict[str, object]:
    candidate: dict[str, object] = {
        "VersionName": "trainpal-demo-010",
        "FlowRatio": candidate_ratio,
        "Status": "normal",
        "Remark": "",
        "IsDefaultPriority": False,
    }
    traffic_values: list[dict[str, str]] | None = None
    if route_value is not None:
        traffic_values = [
            {"Key": "X-TrainPal-Canary", "Value": route_value},
        ]
    return {
        "TrafficType": traffic_type,
        "TrafficTypeValues": traffic_values,
        "CurrentVersion": {
            "VersionName": "trainpal-demo-009",
            "FlowRatio": stable_ratio,
            "Status": "normal",
            "IsDefaultPriority": True,
        },
        "ReleaseVersion": candidate,
    }


def online_versions(
    *, stable_ratio: int = 100, candidate_ratio: int = 0
) -> list[dict[str, object]]:
    return [
        {"VersionName": "trainpal-demo-009", "FlowRatio": stable_ratio},
        {"VersionName": "trainpal-demo-010", "FlowRatio": candidate_ratio},
    ]


def test_route_state_policy_proves_the_header_route_is_active() -> None:
    result = run_state_policy(
        {
            "mode": "enable",
            "stable_version": "trainpal-demo-009",
            "candidate_commit_sha": "a" * 40,
            "candidate_identity_verified": True,
            "stable_route_verified_before_mutation": True,
            "online_versions": [],
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
            "candidate_identity_verified": True,
            "stable_route_verified_before_mutation": False,
            "online_versions": online_versions(),
            "release_order": release_order(traffic_type="FLOW"),
        }
    )

    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["traffic_type"] == "FLOW"
    assert json.loads(result.stdout)["stable_flow_ratio"] == 100
    assert json.loads(result.stdout)["candidate_flow_ratio"] == 0


def test_route_state_policy_proves_exact_candidate_promotion() -> None:
    result = run_state_policy(
        {
            "mode": "promote",
            "stable_version": "trainpal-demo-009",
            "candidate_commit_sha": "a" * 40,
            "candidate_identity_verified": True,
            "stable_route_verified_before_mutation": True,
            "online_versions": online_versions(stable_ratio=0, candidate_ratio=100),
            "release_order": release_order(
                traffic_type="FLOW", stable_ratio=0, candidate_ratio=100
            ),
        }
    )

    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {
        "mode": "promote",
        "traffic_type": "FLOW",
        "stable_version": "trainpal-demo-009",
        "candidate_version": "trainpal-demo-010",
        "stable_flow_ratio": 0,
        "candidate_flow_ratio": 100,
        "routing_header_name": None,
        "routing_header_value_sha256": None,
    }


def test_promote_rejects_a_candidate_that_is_not_running() -> None:
    order = release_order(traffic_type="FLOW", stable_ratio=0, candidate_ratio=100)
    candidate = order["ReleaseVersion"]
    assert isinstance(candidate, dict)
    candidate["Status"] = "failed"

    result = run_state_policy(
        {
            "mode": "promote",
            "stable_version": "trainpal-demo-009",
            "candidate_commit_sha": "a" * 40,
            "candidate_identity_verified": True,
            "stable_route_verified_before_mutation": True,
            "online_versions": online_versions(stable_ratio=0, candidate_ratio=100),
            "release_order": order,
        }
    )

    assert result.returncode == 1


def test_promote_rejects_an_unverified_candidate_identity() -> None:
    result = run_state_policy(
        {
            "mode": "promote",
            "stable_version": "trainpal-demo-009",
            "candidate_commit_sha": "a" * 40,
            "candidate_identity_verified": False,
            "stable_route_verified_before_mutation": True,
            "online_versions": online_versions(stable_ratio=0, candidate_ratio=100),
            "release_order": release_order(
                traffic_type="FLOW", stable_ratio=0, candidate_ratio=100
            ),
        }
    )

    assert result.returncode == 1


def test_promote_rejects_an_unverified_pre_mutation_route() -> None:
    result = run_state_policy(
        {
            "mode": "promote",
            "stable_version": "trainpal-demo-009",
            "candidate_commit_sha": "a" * 40,
            "candidate_identity_verified": True,
            "stable_route_verified_before_mutation": False,
            "online_versions": online_versions(stable_ratio=0, candidate_ratio=100),
            "release_order": release_order(
                traffic_type="FLOW", stable_ratio=0, candidate_ratio=100
            ),
        }
    )

    assert result.returncode == 1


def test_promote_rejects_non_exact_online_routes() -> None:
    routes = online_versions(stable_ratio=0, candidate_ratio=100)
    routes.append({"VersionName": "trainpal-demo-008", "FlowRatio": 0})
    result = run_state_policy(
        {
            "mode": "promote",
            "stable_version": "trainpal-demo-009",
            "candidate_commit_sha": "a" * 40,
            "candidate_identity_verified": True,
            "stable_route_verified_before_mutation": True,
            "online_versions": routes,
            "release_order": release_order(
                traffic_type="FLOW", stable_ratio=0, candidate_ratio=100
            ),
        }
    )

    assert result.returncode == 1


def test_promote_rejects_stale_release_order_ratios() -> None:
    result = run_state_policy(
        {
            "mode": "promote",
            "stable_version": "trainpal-demo-009",
            "candidate_commit_sha": "a" * 40,
            "candidate_identity_verified": True,
            "stable_route_verified_before_mutation": True,
            "online_versions": online_versions(stable_ratio=0, candidate_ratio=100),
            "release_order": release_order(traffic_type="FLOW"),
        }
    )

    assert result.returncode == 1


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
            "candidate_identity_verified": True,
            "stable_route_verified_before_mutation": False,
            "online_versions": online_versions(),
            "release_order": order,
        }
    )

    assert result.returncode == 0, result.stderr


def test_restore_trusts_online_routes_over_stale_release_order_ratio() -> None:
    order = release_order(traffic_type="FLOW")
    candidate = order["ReleaseVersion"]
    assert isinstance(candidate, dict)
    candidate["FlowRatio"] = 100
    result = run_state_policy(
        {
            "mode": "restore",
            "stable_version": "trainpal-demo-009",
            "candidate_commit_sha": "a" * 40,
            "candidate_identity_verified": True,
            "stable_route_verified_before_mutation": False,
            "online_versions": online_versions(),
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
            "candidate_identity_verified": True,
            "online_versions": online_versions(),
            "routing_header_name": "X-TrainPal-Canary",
            "routing_header_value": "private-route-token",
            "release_order": release_order(traffic_type="FLOW"),
        }
    )

    assert result.returncode == 1
    assert "private-route-token" not in result.stderr


def test_route_state_policy_rejects_an_unverified_candidate_identity() -> None:
    result = run_state_policy(
        {
            "mode": "enable",
            "stable_version": "trainpal-demo-009",
            "candidate_commit_sha": "a" * 40,
            "candidate_identity_verified": False,
            "stable_route_verified_before_mutation": True,
            "online_versions": [],
            "routing_header_name": "X-TrainPal-Canary",
            "routing_header_value": "private-route-token",
            "release_order": release_order(
                traffic_type="HEADERS", route_value="private-route-token"
            ),
        }
    )

    assert result.returncode == 1
    assert "private-route-token" not in result.stderr


def test_route_state_policy_rejects_candidate_percentage_traffic() -> None:
    order = release_order(traffic_type="HEADERS", route_value="private-route-token")
    candidate = order["ReleaseVersion"]
    assert isinstance(candidate, dict)
    candidate["FlowRatio"] = 1
    result = run_state_policy(
        {
            "mode": "enable",
            "stable_version": "trainpal-demo-009",
            "candidate_commit_sha": "a" * 40,
            "candidate_identity_verified": True,
            "stable_route_verified_before_mutation": True,
            "online_versions": [],
            "routing_header_name": "X-TrainPal-Canary",
            "routing_header_value": "private-route-token",
            "release_order": order,
        }
    )

    assert result.returncode == 1
    assert "private-route-token" not in result.stderr


def test_route_state_policy_rejects_fractional_candidate_traffic() -> None:
    order = release_order(traffic_type="HEADERS", route_value="private-route-token")
    candidate = order["ReleaseVersion"]
    assert isinstance(candidate, dict)
    candidate["FlowRatio"] = 0.5
    result = run_state_policy(
        {
            "mode": "enable",
            "stable_version": "trainpal-demo-009",
            "candidate_commit_sha": "a" * 40,
            "candidate_identity_verified": True,
            "stable_route_verified_before_mutation": True,
            "online_versions": [],
            "routing_header_name": "X-TrainPal-Canary",
            "routing_header_value": "private-route-token",
            "release_order": order,
        }
    )

    assert result.returncode == 1
    assert "private-route-token" not in result.stderr


def test_route_state_policy_rejects_an_unverified_pre_mutation_route() -> None:
    result = run_state_policy(
        {
            "mode": "enable",
            "stable_version": "trainpal-demo-009",
            "candidate_commit_sha": "a" * 40,
            "candidate_identity_verified": True,
            "stable_route_verified_before_mutation": False,
            "online_versions": [],
            "routing_header_name": "X-TrainPal-Canary",
            "routing_header_value": "private-route-token",
            "release_order": release_order(
                traffic_type="HEADERS", route_value="private-route-token"
            ),
        }
    )

    assert result.returncode == 1
    assert "private-route-token" not in result.stderr


def test_route_state_policy_rejects_an_extra_header_rule() -> None:
    order = release_order(traffic_type="HEADERS", route_value="private-route-token")
    traffic_values = order["TrafficTypeValues"]
    assert isinstance(traffic_values, list)
    traffic_values.append({"Key": "X-Other", "Value": "unexpected"})
    result = run_state_policy(
        {
            "mode": "enable",
            "stable_version": "trainpal-demo-009",
            "candidate_commit_sha": "a" * 40,
            "candidate_identity_verified": True,
            "stable_route_verified_before_mutation": True,
            "online_versions": [],
            "routing_header_name": "X-TrainPal-Canary",
            "routing_header_value": "private-route-token",
            "release_order": order,
        }
    )

    assert result.returncode == 1
    assert "private-route-token" not in result.stderr


def test_private_canary_compatibility_wrapper_runs_a_public_full_flow_transaction() -> None:
    wrapper = (PROJECT_ROOT / "deploy" / "cloudbase" / "run-private-canary.ps1").read_text(
        encoding="utf-8"
    )

    assert "$PublicBaseUrl" in wrapper
    assert "try {" in wrapper
    assert "finally {" in wrapper
    assert "-Mode promote" in wrapper
    assert "-Mode restore" in wrapper
    assert '"/api/v1/health"' in wrapper
    assert '"/api/v1/ready"' in wrapper
    assert "release_sha" in wrapper
    assert "route_restored = $true" in wrapper

    assert "private-canary.py" not in wrapper
    assert "$Media" not in wrapper
    assert "JudgeCredential" not in wrapper
    assert "RoutingHeader" not in wrapper


POWERSHELL = shutil.which("powershell") or shutil.which("pwsh")


def run_private_canary_wrapper(
    tmp_path: Path,
    *,
    health_sha: str,
    ready_status: str = "ready",
    restore_fails: bool = False,
    stale_receipt: bool = False,
) -> tuple[subprocess.CompletedProcess[str], Path, Path, Path]:
    fake_repository = tmp_path / "repository"
    script_directory = fake_repository / "deploy" / "cloudbase"
    script_directory.mkdir(parents=True)
    wrapper_path = script_directory / "run-private-canary.ps1"
    shutil.copy2(
        PROJECT_ROOT / "deploy" / "cloudbase" / "run-private-canary.ps1",
        wrapper_path,
    )
    (script_directory / "set-canary-route.ps1").write_text(
        textwrap.dedent(
            """
            param(
                [Parameter(Mandatory)][string]$EnvironmentId,
                [Parameter(Mandatory)][string]$ExpectedStableVersion,
                [Parameter(Mandatory)][string]$ExpectedCandidateCommitSha,
                [Parameter(Mandatory)][ValidateSet('promote', 'restore')][string]$Mode,
                [string]$Region = 'ap-shanghai',
                [string]$ServiceName = 'trainpal-demo',
                [string]$AuthPath = ''
            )
            [IO.File]::AppendAllText($env:TRAINPAL_TEST_ROUTE_LOG, "$Mode`n")
            if ($Mode -eq 'restore' -and $env:TRAINPAL_TEST_RESTORE_FAILS -eq 'true') {
                throw 'Synthetic restore failure.'
            }
            @{ mode = $Mode } | ConvertTo-Json -Compress
            """
        ).strip()
        + "\n",
        encoding="utf-8",
    )

    output_directory = tmp_path / "receipts"
    receipt_path = output_directory / "private-canary-transaction.json"
    if stale_receipt:
        output_directory.mkdir()
        receipt_path.write_text('{"route_restored":true}', encoding="utf-8")

    route_log = tmp_path / "route.log"
    http_log = tmp_path / "http.log"
    harness_path = tmp_path / "invoke-wrapper.ps1"
    harness_path.write_text(
        textwrap.dedent(
            """
            param(
                [Parameter(Mandatory)][string]$WrapperPath,
                [Parameter(Mandatory)][string]$OutputDirectory,
                [Parameter(Mandatory)][string]$ExpectedSha,
                [Parameter(Mandatory)][string]$HealthSha,
                [Parameter(Mandatory)][string]$ReadyStatus
            )
            function Invoke-RestMethod {
                param(
                    [Parameter(Mandatory)][string]$Method,
                    [Parameter(Mandatory)][string]$Uri,
                    [hashtable]$Headers,
                    [int]$MaximumRedirection,
                    [int]$TimeoutSec
                )
                [IO.File]::AppendAllText(
                    $env:TRAINPAL_TEST_HTTP_LOG,
                    "$Method $Uri`n"
                )
                if ($Method -ne 'Get') {
                    throw 'Only exact GET requests are accepted by the test boundary.'
                }
                if ($Uri -eq 'https://public.example/api/v1/health') {
                    return [pscustomobject]@{ status = 'ok'; release_sha = $HealthSha }
                }
                if ($Uri -eq 'https://public.example/api/v1/ready') {
                    return [pscustomobject]@{ status = $ReadyStatus }
                }
                throw 'Unexpected public endpoint.'
            }
            & $WrapperPath `
                -EnvironmentId 'sensitive-environment-id' `
                -PublicBaseUrl 'https://public.example' `
                -ExpectedStableVersion 'trainpal-demo-009' `
                -ExpectedCandidateCommitSha $ExpectedSha `
                -OutputDirectory $OutputDirectory
            """
        ).strip()
        + "\n",
        encoding="utf-8",
    )

    assert POWERSHELL is not None
    expected_sha = "a" * 40
    environment = os.environ.copy()
    environment.update(
        {
            "TRAINPAL_TEST_ROUTE_LOG": str(route_log),
            "TRAINPAL_TEST_HTTP_LOG": str(http_log),
            "TRAINPAL_TEST_RESTORE_FAILS": str(restore_fails).lower(),
        }
    )
    result = subprocess.run(
        [
            POWERSHELL,
            "-NoProfile",
            "-NonInteractive",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(harness_path),
            "-WrapperPath",
            str(wrapper_path),
            "-OutputDirectory",
            str(output_directory),
            "-ExpectedSha",
            expected_sha,
            "-HealthSha",
            health_sha,
            "-ReadyStatus",
            ready_status,
        ],
        capture_output=True,
        check=False,
        encoding="utf-8",
        env=environment,
    )
    return result, receipt_path, route_log, http_log


@pytest.mark.skipif(POWERSHELL is None, reason="PowerShell is unavailable")
def test_full_flow_identity_failure_restores_and_removes_a_stale_receipt(
    tmp_path: Path,
) -> None:
    result, receipt_path, route_log, _ = run_private_canary_wrapper(
        tmp_path,
        health_sha="b" * 40,
        stale_receipt=True,
    )

    assert result.returncode != 0
    assert route_log.read_text(encoding="utf-8").splitlines() == ["promote", "restore"]
    assert not receipt_path.exists()


@pytest.mark.skipif(POWERSHELL is None, reason="PowerShell is unavailable")
def test_full_flow_identity_success_writes_only_the_sanitized_receipt(
    tmp_path: Path,
) -> None:
    expected_sha = "a" * 40
    result, receipt_path, route_log, http_log = run_private_canary_wrapper(
        tmp_path,
        health_sha=expected_sha,
    )

    assert result.returncode == 0, result.stderr
    assert route_log.read_text(encoding="utf-8").splitlines() == ["promote", "restore"]
    assert http_log.read_text(encoding="utf-8").splitlines() == [
        "Get https://public.example/api/v1/health",
        "Get https://public.example/api/v1/ready",
    ]
    assert json.loads(receipt_path.read_text(encoding="utf-8")) == {
        "service": "trainpal-demo",
        "candidate_commit_sha": expected_sha,
        "stable_version": "trainpal-demo-009",
        "health": "ok",
        "ready": "ready",
        "route_restored": True,
    }
    receipt_text = receipt_path.read_text(encoding="utf-8")
    assert "public.example" not in receipt_text
    assert "sensitive-environment-id" not in receipt_text


@pytest.mark.skipif(POWERSHELL is None, reason="PowerShell is unavailable")
def test_full_flow_restore_failure_never_writes_a_passing_receipt(tmp_path: Path) -> None:
    result, receipt_path, route_log, _ = run_private_canary_wrapper(
        tmp_path,
        health_sha="a" * 40,
        restore_fails=True,
    )

    assert result.returncode != 0
    assert route_log.read_text(encoding="utf-8").splitlines() == ["promote", "restore"]
    assert not receipt_path.exists()
