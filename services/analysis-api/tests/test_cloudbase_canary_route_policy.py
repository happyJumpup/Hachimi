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
    assert "DescribeCloudRunPodList" not in route_setter
    assert "StartVersionInstance" not in route_setter
    assert "candidate_pod_ready" not in route_setter
    assert "stable_route_verified_before_mutation" in route_setter
    assert "Convert-ExactTrafficRatio" in route_setter
    assert "$stateExitCode = $LASTEXITCODE" in route_setter
    assert "$ErrorActionPreference = 'Continue'" in route_setter
    assert "ValidateSet('enable', 'promote', 'restore')" in route_setter
    assert "$Mode -in @('enable', 'promote')" in route_setter
    assert "$Mode -ne 'enable' -and ($RoutingHeaderName -or $RoutingHeaderValue)" in route_setter
    assert "$promotionStartsFromExactTwoRoutes" in route_setter
    assert "$promotionStartsFromOmittedZeroCandidate" in route_setter
    assert "$normalizedOnlineVersions.Count -eq 1" in route_setter
    assert "$candidateRoutes.Count -eq 0" in route_setter
    assert "for ($attempt = 1; $attempt -le 120; $attempt++)" in route_setter
    assert "if ($attempt -lt 120)" in route_setter
    assert (
        "Promotion requires exact stable 100 with a zero-percent or omitted candidate."
        in route_setter
    )


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


def test_route_state_policy_accepts_omitted_zero_percent_stable_after_promotion() -> None:
    result = run_state_policy(
        {
            "mode": "promote",
            "stable_version": "trainpal-demo-009",
            "candidate_commit_sha": "a" * 40,
            "candidate_identity_verified": True,
            "stable_route_verified_before_mutation": True,
            "online_versions": [
                {"VersionName": "trainpal-demo-010", "FlowRatio": 100}
            ],
            "release_order": release_order(
                traffic_type="FLOW", stable_ratio=0, candidate_ratio=100
            ),
        }
    )

    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["candidate_flow_ratio"] == 100


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
    assert "[ValidateSet('exercise', 'finalize')][string]$Mode = 'exercise'" in wrapper
    assert "try {" in wrapper
    assert "finally {" in wrapper
    assert "-Action 'ReleaseGray'" in wrapper
    assert "CloseGrayRelease = $true" in wrapper
    assert "-Action 'DescribeServerManageTask'" in wrapper
    assert "-Action 'SubmitServerRollback'" in wrapper
    assert "Wait-ManagementTaskTerminal" in wrapper
    assert "Wait-ExactOnlineTraffic" in wrapper
    assert "'/api/v1/health'" in wrapper
    assert "'/api/v1/ready'" in wrapper
    assert "release_sha" in wrapper
    assert "route_restored = $RouteRestored" in wrapper
    assert "route_finalized = $RouteFinalized" in wrapper
    assert "[ValidateRange(1, 300)][int]$TimeoutSeconds = 240" in wrapper
    assert "[ValidateRange(1, 600)][int]$RecoveryTimeoutSeconds = 300" in wrapper

    assert "private-canary.py" not in wrapper
    assert "set-canary-route.ps1" not in wrapper
    assert "$Media" not in wrapper
    assert "JudgeCredential" not in wrapper
    assert "RoutingHeader" not in wrapper


POWERSHELL = shutil.which("powershell") or shutil.which("pwsh")


def run_private_canary_wrapper(
    tmp_path: Path,
    *,
    health_sha: str,
    mode: str = "exercise",
    ready_status: str = "ready",
    restore_fails: bool = False,
    promote_task_fails: bool = False,
    rollback_task_fails: bool = False,
    receipt_write_fails: bool = False,
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

    output_directory = tmp_path / "receipts"
    receipt_path = output_directory / f"private-canary-{mode}-transaction.json"
    if stale_receipt:
        output_directory.mkdir()
        receipt_path.write_text('{"route_restored":true}', encoding="utf-8")

    auth_path = tmp_path / "auth.json"
    auth_path.write_text(
        json.dumps(
            {
                "credential": {
                    "tmpSecretId": "test-id",
                    "tmpSecretKey": "test-key",
                    "tmpToken": "test-token",
                }
            }
        ),
        encoding="utf-8",
    )
    action_log = tmp_path / "actions.log"
    http_log = tmp_path / "http.log"
    harness_path = tmp_path / "invoke-wrapper.ps1"
    harness_path.write_text(
        textwrap.dedent(
            """
            param(
                [Parameter(Mandatory)][string]$WrapperPath,
                [Parameter(Mandatory)][string]$OutputDirectory,
                [Parameter(Mandatory)][string]$AuthPath,
                [Parameter(Mandatory)][string]$ExpectedSha,
                [Parameter(Mandatory)][string]$HealthSha,
                [Parameter(Mandatory)][string]$ReadyStatus
            )
            $ErrorActionPreference = 'Stop'
            $global:TrainPalTestPhase = 'preflight'
            $global:TrainPalTestPromoteTaskPolls = 0
            $global:TrainPalTestRollbackTaskPolls = 0

            function New-ApiResponse {
                param([Parameter(Mandatory)][object]$Value)
                [pscustomobject]@{ Response = $Value }
            }

            function Invoke-RestMethod {
                param(
                    [Parameter(Mandatory)][string]$Method,
                    [Parameter(Mandatory)][string]$Uri,
                    [hashtable]$Headers,
                    [AllowNull()][object]$Body,
                    [AllowNull()][string]$ContentType,
                    [int]$MaximumRedirection,
                    [int]$TimeoutSec
                )
                if ($Uri -eq 'https://tcbr.tencentcloudapi.com') {
                    $action = [string]$Headers['X-TC-Action']
                    [IO.File]::AppendAllText(
                        $env:TRAINPAL_TEST_ACTION_LOG,
                        "$action`n"
                    )
                    $payload = $Body | ConvertFrom-Json
                    switch ($action) {
                        'DescribeReleaseOrder' {
                            return New-ApiResponse ([pscustomobject]@{
                                ReleaseOrderInfo = [pscustomobject]@{
                                    TrafficType = 'FLOW'
                                    TrafficTypeValues = @()
                                    CurrentVersion = [pscustomobject]@{
                                        VersionName = 'trainpal-demo-009'
                                        Status = 'normal'
                                        FlowRatio = 100
                                        IsDefaultPriority = $true
                                    }
                                    ReleaseVersion = [pscustomobject]@{
                                        VersionName = 'trainpal-demo-010'
                                        Status = 'normal'
                                        FlowRatio = 0
                                        IsDefaultPriority = $false
                                    }
                                }
                            })
                        }
                        'DescribeVersionDetail' {
                            return New-ApiResponse ([pscustomobject]@{
                                Name = 'trainpal-demo-010'
                                EnvParams = [pscustomobject]@{
                                    APP_RELEASE_SHA = $ExpectedSha
                                }
                            })
                        }
                        'DescribeCloudRunServerDetail' {
                            $versions = if (
                                $global:TrainPalTestPhase -in @(
                                    'promoted',
                                    'rolling-back'
                                )
                            ) {
                                @([pscustomobject]@{
                                    VersionName = 'trainpal-demo-010'
                                    FlowRatio = 100
                                })
                            } else {
                                @([pscustomobject]@{
                                    VersionName = 'trainpal-demo-009'
                                    FlowRatio = 100
                                })
                            }
                            return New-ApiResponse ([pscustomobject]@{
                                OnlineVersionInfos = $versions
                            })
                        }
                        'DescribeServerManageTask' {
                            if ($global:TrainPalTestPhase -eq 'preflight') {
                                return New-ApiResponse ([pscustomobject]@{
                                    IsExist = $true
                                    Task = [pscustomobject]@{
                                        Id = 101
                                        ServerName = 'trainpal-demo'
                                        Status = 'running'
                                        ReleaseType = 'GRAY'
                                        PreVersionName = 'trainpal-demo-009'
                                        VersionName = 'trainpal-demo-010'
                                        FailReason = ''
                                    }
                                })
                            }
                            if ($global:TrainPalTestPhase -eq 'promoting') {
                                $global:TrainPalTestPromoteTaskPolls++
                                $status = if (
                                    $env:TRAINPAL_TEST_PROMOTE_TASK_FAILS -eq 'true'
                                ) {
                                    $global:TrainPalTestPhase = 'promoted'
                                    'failed'
                                } elseif (
                                    $global:TrainPalTestPromoteTaskPolls -ge 2
                                ) {
                                    $global:TrainPalTestPhase = 'promoted'
                                    'finished'
                                } else {
                                    'running'
                                }
                                return New-ApiResponse ([pscustomobject]@{
                                    IsExist = $true
                                    Task = [pscustomobject]@{
                                        Id = 101
                                        ServerName = 'trainpal-demo'
                                        Status = $status
                                        ReleaseType = 'GRAY'
                                        PreVersionName = 'trainpal-demo-009'
                                        VersionName = 'trainpal-demo-010'
                                        FailReason = if ($status -eq 'failed') {
                                            'synthetic failure'
                                        } else { '' }
                                    }
                                })
                            }
                            if ($global:TrainPalTestPhase -eq 'rolling-back') {
                                $global:TrainPalTestRollbackTaskPolls++
                                if ($global:TrainPalTestRollbackTaskPolls -eq 1) {
                                    return New-ApiResponse ([pscustomobject]@{
                                        IsExist = $true
                                        Task = [pscustomobject]@{
                                            Id = 101
                                            ServerName = 'trainpal-demo'
                                            Status = 'finished'
                                            ReleaseType = 'GRAY'
                                            PreVersionName = 'trainpal-demo-009'
                                            VersionName = 'trainpal-demo-010'
                                            FailReason = ''
                                        }
                                    })
                                }
                                $status = if (
                                    $env:TRAINPAL_TEST_ROLLBACK_TASK_FAILS -eq 'true'
                                ) {
                                    'failed'
                                } elseif (
                                    $global:TrainPalTestRollbackTaskPolls -ge 3
                                ) {
                                    $global:TrainPalTestPhase = 'restored'
                                    'finished'
                                } else {
                                    'running'
                                }
                                return New-ApiResponse ([pscustomobject]@{
                                    IsExist = $true
                                    Task = [pscustomobject]@{
                                        Id = 202
                                        ServerName = 'trainpal-demo'
                                        Status = $status
                                        ReleaseType = 'FULL'
                                        PreVersionName = 'trainpal-demo-010'
                                        VersionName = 'trainpal-demo-009'
                                        FailReason = if ($status -eq 'failed') {
                                            'synthetic failure'
                                        } else { '' }
                                    }
                                })
                            }
                            throw 'Unexpected task lookup.'
                        }
                        'ReleaseGray' {
                            if (
                                $payload.CloseGrayRelease -ne $true -or
                                [int]$payload.GrayFlowRatio -ne 100 -or
                                @($payload.VersionFlowItems).Count -ne 1 -or
                                [string]$payload.VersionFlowItems[0].VersionName -ne
                                    'trainpal-demo-010' -or
                                [int]$payload.VersionFlowItems[0].FlowRatio -ne 100
                            ) {
                                throw 'Promotion payload did not match CLI semantics.'
                            }
                            $global:TrainPalTestPhase = 'promoting'
                            return New-ApiResponse ([pscustomobject]@{
                                RequestId = 'promote-request'
                            })
                        }
                        'SubmitServerRollback' {
                            if ($env:TRAINPAL_TEST_RESTORE_FAILS -eq 'true') {
                                throw 'Synthetic rollback submission failure.'
                            }
                            if (
                                [string]$payload.CurrentVersionName -ne
                                    'trainpal-demo-010' -or
                                [string]$payload.RollbackVersionName -ne
                                    'trainpal-demo-009'
                            ) {
                                throw 'Rollback identities were not verified.'
                            }
                            $global:TrainPalTestPhase = 'rolling-back'
                            return New-ApiResponse ([pscustomobject]@{
                                TaskId = 0
                                RequestId = 'rollback-request'
                            })
                        }
                        default { throw "Unexpected API action: $action" }
                    }
                }
                [IO.File]::AppendAllText(
                    $env:TRAINPAL_TEST_HTTP_LOG,
                    "$Method $Uri`n"
                )
                if (
                    $Method -ne 'Get' -or
                    $global:TrainPalTestPhase -ne 'promoted'
                ) {
                    throw 'Unexpected public request state.'
                }
                if ($Uri -eq 'https://public.example/api/v1/health') {
                    return [pscustomobject]@{ status = 'ok'; release_sha = $HealthSha }
                }
                if ($Uri -eq 'https://public.example/api/v1/ready') {
                    if ($env:TRAINPAL_TEST_RECEIPT_WRITE_FAILS -eq 'true') {
                        New-Item -ItemType Directory -Path (
                            Join-Path $OutputDirectory (
                                "private-canary-$($env:TRAINPAL_TEST_MODE)-transaction.json"
                            )
                        ) -Force | Out-Null
                    }
                    return [pscustomobject]@{ status = $ReadyStatus }
                }
                throw 'Unexpected public endpoint.'
            }
            & $WrapperPath `
                -EnvironmentId 'sensitive-environment-id' `
                -PublicBaseUrl 'https://public.example' `
                -ExpectedStableVersion 'trainpal-demo-009' `
                -ExpectedCandidateCommitSha $ExpectedSha `
                -OutputDirectory $OutputDirectory `
                -AuthPath $AuthPath `
                -Mode $env:TRAINPAL_TEST_MODE `
                -TimeoutSeconds 5 `
                -RecoveryTimeoutSeconds 5
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
            "TRAINPAL_TEST_ACTION_LOG": str(action_log),
            "TRAINPAL_TEST_HTTP_LOG": str(http_log),
            "TRAINPAL_TEST_RESTORE_FAILS": str(restore_fails).lower(),
            "TRAINPAL_TEST_PROMOTE_TASK_FAILS": str(promote_task_fails).lower(),
            "TRAINPAL_TEST_ROLLBACK_TASK_FAILS": str(rollback_task_fails).lower(),
            "TRAINPAL_TEST_MODE": mode,
            "TRAINPAL_TEST_RECEIPT_WRITE_FAILS": str(receipt_write_fails).lower(),
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
            "-AuthPath",
            str(auth_path),
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
    return result, receipt_path, action_log, http_log


@pytest.mark.skipif(POWERSHELL is None, reason="PowerShell is unavailable")
def test_full_flow_identity_failure_restores_and_removes_a_stale_receipt(
    tmp_path: Path,
) -> None:
    result, receipt_path, action_log, _ = run_private_canary_wrapper(
        tmp_path,
        health_sha="b" * 40,
        stale_receipt=True,
    )

    assert result.returncode != 0
    actions = action_log.read_text(encoding="utf-8").splitlines()
    assert actions.index("ReleaseGray") < actions.index("SubmitServerRollback")
    assert not receipt_path.exists()


@pytest.mark.skipif(POWERSHELL is None, reason="PowerShell is unavailable")
def test_full_flow_identity_success_writes_only_the_sanitized_receipt(
    tmp_path: Path,
) -> None:
    expected_sha = "a" * 40
    result, receipt_path, action_log, http_log = run_private_canary_wrapper(
        tmp_path,
        health_sha=expected_sha,
    )

    assert result.returncode == 0, result.stderr
    actions = action_log.read_text(encoding="utf-8").splitlines()
    assert actions.index("ReleaseGray") < actions.index("SubmitServerRollback")
    assert actions.count("DescribeServerManageTask") >= 5
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
        "route_finalized": False,
    }
    receipt_text = receipt_path.read_text(encoding="utf-8")
    assert "public.example" not in receipt_text
    assert "sensitive-environment-id" not in receipt_text


@pytest.mark.skipif(POWERSHELL is None, reason="PowerShell is unavailable")
def test_finalize_keeps_verified_candidate_at_full_traffic(tmp_path: Path) -> None:
    exercise_receipt = (
        tmp_path / "receipts" / "private-canary-exercise-transaction.json"
    )
    exercise_receipt.parent.mkdir()
    exercise_receipt.write_text('{"prior_exercise":true}', encoding="utf-8")
    expected_sha = "a" * 40
    result, receipt_path, action_log, _ = run_private_canary_wrapper(
        tmp_path,
        health_sha=expected_sha,
        mode="finalize",
    )

    assert result.returncode == 0, result.stderr
    actions = action_log.read_text(encoding="utf-8").splitlines()
    assert "ReleaseGray" in actions
    assert "SubmitServerRollback" not in actions
    assert json.loads(receipt_path.read_text(encoding="utf-8")) == {
        "service": "trainpal-demo",
        "candidate_commit_sha": expected_sha,
        "stable_version": "trainpal-demo-009",
        "health": "ok",
        "ready": "ready",
        "route_restored": False,
        "route_finalized": True,
    }
    assert exercise_receipt.read_text(encoding="utf-8") == '{"prior_exercise":true}'


@pytest.mark.skipif(POWERSHELL is None, reason="PowerShell is unavailable")
def test_finalize_failure_restores_verified_stable_traffic(tmp_path: Path) -> None:
    result, receipt_path, action_log, _ = run_private_canary_wrapper(
        tmp_path,
        health_sha="b" * 40,
        mode="finalize",
    )

    assert result.returncode != 0
    actions = action_log.read_text(encoding="utf-8").splitlines()
    assert actions.index("ReleaseGray") < actions.index("SubmitServerRollback")
    assert not receipt_path.exists()


@pytest.mark.skipif(POWERSHELL is None, reason="PowerShell is unavailable")
def test_finalize_receipt_write_failure_restores_verified_stable_traffic(
    tmp_path: Path,
) -> None:
    result, receipt_path, action_log, _ = run_private_canary_wrapper(
        tmp_path,
        health_sha="a" * 40,
        mode="finalize",
        receipt_write_fails=True,
    )

    assert result.returncode != 0
    actions = action_log.read_text(encoding="utf-8").splitlines()
    assert actions.index("ReleaseGray") < actions.index("SubmitServerRollback")
    assert not receipt_path.is_file()


@pytest.mark.skipif(POWERSHELL is None, reason="PowerShell is unavailable")
def test_full_flow_restore_failure_never_writes_a_passing_receipt(tmp_path: Path) -> None:
    result, receipt_path, action_log, _ = run_private_canary_wrapper(
        tmp_path,
        health_sha="a" * 40,
        restore_fails=True,
    )

    assert result.returncode != 0
    assert "SubmitServerRollback" in action_log.read_text(
        encoding="utf-8"
    ).splitlines()
    assert not receipt_path.exists()


@pytest.mark.skipif(POWERSHELL is None, reason="PowerShell is unavailable")
def test_failed_promotion_task_is_terminal_before_rollback(tmp_path: Path) -> None:
    result, receipt_path, action_log, _ = run_private_canary_wrapper(
        tmp_path,
        health_sha="a" * 40,
        promote_task_fails=True,
    )

    actions = action_log.read_text(encoding="utf-8").splitlines()
    assert result.returncode != 0
    assert actions.index("DescribeServerManageTask", actions.index("ReleaseGray")) < (
        actions.index("SubmitServerRollback")
    )
    assert not receipt_path.exists()


@pytest.mark.skipif(POWERSHELL is None, reason="PowerShell is unavailable")
def test_failed_rollback_task_never_writes_a_passing_receipt(tmp_path: Path) -> None:
    result, receipt_path, action_log, _ = run_private_canary_wrapper(
        tmp_path,
        health_sha="a" * 40,
        rollback_task_fails=True,
    )

    actions = action_log.read_text(encoding="utf-8").splitlines()
    assert result.returncode != 0
    assert "SubmitServerRollback" in actions
    assert not receipt_path.exists()
