from __future__ import annotations

import argparse
import ctypes
import hashlib
import hmac
import json
import re
import sys
import time
from ctypes import wintypes
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from uuid import uuid4

import httpx


MAX_UPLOAD_BYTES = 256 * 1024 * 1024
TERMINAL_STATUSES = {"completed", "failed", "cancelled"}


class CanaryError(RuntimeError):
    pass


class _Credential(ctypes.Structure):
    _fields_ = [
        ("Flags", wintypes.DWORD),
        ("Type", wintypes.DWORD),
        ("TargetName", wintypes.LPWSTR),
        ("Comment", wintypes.LPWSTR),
        ("LastWritten", wintypes.FILETIME),
        ("CredentialBlobSize", wintypes.DWORD),
        ("CredentialBlob", ctypes.POINTER(ctypes.c_ubyte)),
        ("Persist", wintypes.DWORD),
        ("AttributeCount", wintypes.DWORD),
        ("Attributes", ctypes.c_void_p),
        ("TargetAlias", wintypes.LPWSTR),
        ("UserName", wintypes.LPWSTR),
    ]


def read_windows_credential(target: str) -> str:
    if sys.platform != "win32":
        raise CanaryError("Windows Credential Manager is required for the judge code")
    credential_pointer = ctypes.POINTER(_Credential)()
    advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)
    cred_read = advapi32.CredReadW
    cred_read.argtypes = [
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
        ctypes.POINTER(ctypes.POINTER(_Credential)),
    ]
    cred_read.restype = wintypes.BOOL
    cred_free = advapi32.CredFree
    cred_free.argtypes = [ctypes.c_void_p]
    cred_free.restype = None
    if not cred_read(target, 1, 0, ctypes.byref(credential_pointer)):
        raise CanaryError("the configured judge credential is unavailable")
    try:
        credential = credential_pointer.contents
        if credential.CredentialBlobSize <= 0:
            raise CanaryError("the configured judge credential is empty")
        raw = ctypes.string_at(
            credential.CredentialBlob,
            credential.CredentialBlobSize,
        )
        try:
            value = raw.decode("utf-16-le").rstrip("\x00")
        except UnicodeDecodeError:
            value = raw.decode("utf-8").rstrip("\x00")
        if not value:
            raise CanaryError("the configured judge credential is empty")
        return value
    finally:
        cred_free(credential_pointer)


@dataclass(frozen=True, slots=True)
class TemporaryCredential:
    secret_id: str
    secret_key: str
    token: str

    @classmethod
    def load(cls, path: Path) -> TemporaryCredential:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            credential = payload["credential"]
            result = cls(
                secret_id=str(credential["tmpSecretId"]),
                secret_key=str(credential["tmpSecretKey"]),
                token=str(credential["tmpToken"]),
            )
        except (OSError, KeyError, TypeError, ValueError) as error:
            raise CanaryError("CloudBase temporary credential is unavailable") from error
        if not result.secret_id or not result.secret_key or not result.token:
            raise CanaryError("CloudBase temporary credential is incomplete")
        return result


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _hmac(key: bytes, value: str) -> bytes:
    return hmac.new(key, value.encode("utf-8"), hashlib.sha256).digest()


class SignedGatewayClient:
    def __init__(
        self,
        *,
        environment_id: str,
        service_name: str,
        credential: TemporaryCredential,
        timeout_seconds: float,
        routing_header_name: str | None = None,
        routing_header_value: str | None = None,
    ) -> None:
        if bool(routing_header_name) != bool(routing_header_value):
            raise CanaryError("routing header pair must be provided together")
        self._host = f"{environment_id}.api.tcloudbasegateway.com"
        self._service_name = service_name
        self._credential = credential
        self._routing_headers = (
            {routing_header_name: routing_header_value}
            if routing_header_name is not None and routing_header_value is not None
            else {}
        )
        self._client = httpx.Client(
            follow_redirects=False,
            timeout=httpx.Timeout(
                connect=30,
                read=timeout_seconds,
                write=timeout_seconds,
                pool=30,
            ),
        )

    @property
    def origin(self) -> str:
        return f"https://{self._host}"

    def close(self) -> None:
        self._client.close()

    def _gateway_path(self, application_path: str) -> str:
        if not application_path.startswith("/"):
            raise CanaryError("application path must be absolute")
        return f"/v1/cloudrun/{self._service_name}{application_path}"

    def _authorization(
        self,
        *,
        method: str,
        gateway_path: str,
        content_type: str,
        body: bytes,
    ) -> str:
        timestamp = int(time.time())
        date = time.strftime("%Y-%m-%d", time.gmtime(timestamp))
        canonical_headers = f"content-type:{content_type}\nhost:{self._host}\n"
        signed_headers = "content-type;host"
        canonical_request = "\n".join(
            [
                method.upper(),
                gateway_path,
                "",
                canonical_headers,
                signed_headers,
                _sha256(body),
            ]
        )
        credential_scope = f"{date}/tcb/tc3_request"
        string_to_sign = "\n".join(
            [
                "TC3-HMAC-SHA256",
                str(timestamp),
                credential_scope,
                _sha256(canonical_request.encode("utf-8")),
            ]
        )
        secret_date = _hmac(
            f"TC3{self._credential.secret_key}".encode("utf-8"),
            date,
        )
        secret_service = _hmac(secret_date, "tcb")
        secret_signing = _hmac(secret_service, "tc3_request")
        signature = hmac.new(
            secret_signing,
            string_to_sign.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()
        return (
            "TC3-HMAC-SHA256 "
            f"Credential={self._credential.secret_id}/{credential_scope}, "
            f"SignedHeaders={signed_headers}, Signature={signature}, "
            f"Timestamp={timestamp}, Token={self._credential.token}"
        )

    def request_json(
        self,
        method: str,
        application_path: str,
        *,
        payload: dict[str, Any] | None = None,
        same_origin: bool = False,
    ) -> httpx.Response:
        gateway_path = self._gateway_path(application_path)
        body = (
            b""
            if payload is None
            else json.dumps(
                payload,
                ensure_ascii=False,
                separators=(",", ":"),
            ).encode("utf-8")
        )
        content_type = "application/json"
        headers = {
            "Content-Type": content_type,
            "Authorization": self._authorization(
                method=method,
                gateway_path=gateway_path,
                content_type=content_type,
                body=body,
            ),
        }
        if same_origin:
            headers["Origin"] = self.origin
        headers.update(self._routing_headers)
        return self._client.request(
            method,
            f"{self.origin}{gateway_path}",
            content=body,
            headers=headers,
        )

    def upload_video(self, application_path: str, media_path: Path) -> httpx.Response:
        gateway_path = self._gateway_path(application_path)
        request = self._client.build_request(
            "POST",
            f"{self.origin}{gateway_path}",
            data={"local_source_id": f"local:{uuid4()}"},
            files={
                "media": (
                    "canary.mp4",
                    media_path.read_bytes(),
                    "video/mp4",
                )
            },
            headers={"Origin": self.origin, **self._routing_headers},
        )
        body = request.read()
        content_type = request.headers["Content-Type"]
        request.headers["Authorization"] = self._authorization(
            method="POST",
            gateway_path=gateway_path,
            content_type=content_type,
            body=body,
        )
        return self._client.send(request)

    def stream_events(self, application_path: str) -> tuple[int, list[str], bool]:
        gateway_path = self._gateway_path(application_path)
        content_type = "application/json"
        request = self._client.build_request(
            "GET",
            f"{self.origin}{gateway_path}",
            headers={
                "Accept": "text/event-stream",
                "Content-Type": content_type,
                "Authorization": self._authorization(
                    method="GET",
                    gateway_path=gateway_path,
                    content_type=content_type,
                    body=b"",
                ),
                **self._routing_headers,
            },
        )
        event_types: list[str] = []
        terminal = False
        response = self._client.send(request, stream=True)
        try:
            if response.status_code != 200:
                return response.status_code, event_types, terminal
            for line in response.iter_lines():
                if not line.startswith("event: "):
                    continue
                event_type = line.removeprefix("event: ").strip()
                if event_type and event_type not in event_types:
                    event_types.append(event_type)
                if event_type in {"run.completed", "run.failed", "run.cancelled"}:
                    terminal = True
                    break
        finally:
            response.close()
        return 200, event_types, terminal


def _expect_json(response: httpx.Response, expected_status: int, label: str) -> dict[str, Any]:
    if response.status_code != expected_status:
        raise CanaryError(f"{label} failed with HTTP {response.status_code}")
    try:
        payload = response.json()
    except ValueError as error:
        raise CanaryError(f"{label} returned invalid JSON") from error
    if not isinstance(payload, dict):
        raise CanaryError(f"{label} returned an invalid JSON shape")
    return payload


def _verify_anonymous_gymti_fallback(
    client: SignedGatewayClient,
) -> dict[str, str | None]:
    contract_path = Path(__file__).resolve().parents[2] / "contracts" / (
        "gymti-questionnaire.v1.json"
    )
    try:
        contract = json.loads(contract_path.read_text(encoding="utf-8"))
        version = str(contract["version"])
        first_question_id = str(contract["questions"][0]["id"])
    except (OSError, KeyError, IndexError, TypeError, ValueError) as error:
        raise CanaryError("GYMTI contract is unavailable to the private canary") from error
    response = _expect_json(
        client.request_json(
            "POST",
            "/api/v1/gymti/next-question",
            payload={
                "questionnaire_version": version,
                "scoring_version": version,
                "answered": [],
                "candidate_question_ids": [first_question_id],
            },
            same_origin=True,
        ),
        200,
        "anonymous GYMTI next question",
    )
    if response != {
        "question_id": first_question_id,
        "source": "local_fallback",
        "model": None,
        "version": version,
    }:
        raise CanaryError("anonymous GYMTI did not use the versioned local fallback")
    return {
        "next_question_source": "local_fallback",
        "model": None,
        "contract_version": version,
    }


def _verify_spa_candidate(client: SignedGatewayClient) -> dict[str, int]:
    shell = client.request_json("GET", "/")
    try:
        if shell.status_code != 200 or "text/html" not in shell.headers.get(
            "content-type", ""
        ):
            raise CanaryError("candidate SPA shell is unavailable")
        asset_match = re.search(
            r"(?:src|href)=[\"'](/assets/[A-Za-z0-9._-]+)[\"']",
            shell.text,
        )
        if asset_match is None:
            raise CanaryError("candidate SPA shell does not reference a built asset")
        asset_path = asset_match.group(1)
    finally:
        shell.close()

    asset = client.request_json("GET", asset_path)
    try:
        if (
            asset.status_code != 200
            or not asset.content
            or "text/html" in asset.headers.get("content-type", "")
        ):
            raise CanaryError("candidate SPA built asset is unavailable")
    finally:
        asset.close()
    return {"shell_http_status": 200, "asset_http_status": 200}


def _verify_release_identity(
    payload: dict[str, Any],
    expected_commit_sha: str,
) -> dict[str, str]:
    if (
        re.fullmatch(r"[0-9a-f]{40}", expected_commit_sha) is None
        or payload.get("status") != "ok"
        or payload.get("release_sha") != expected_commit_sha
    ):
        raise CanaryError("deployed release identity does not match the candidate")
    return {"commit_sha": expected_commit_sha}


def _verify_readiness(payload: dict[str, Any]) -> dict[str, str]:
    if payload != {"status": "ready"}:
        raise CanaryError("candidate failed the full production readiness contract")
    return {"status": "ready"}


def _wait_for_terminal(
    client: SignedGatewayClient,
    run_id: str,
    *,
    timeout_seconds: float,
) -> dict[str, Any]:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        response = client.request_json("GET", f"/api/v1/analysis-runs/{run_id}")
        payload = _expect_json(response, 200, "run status")
        if payload.get("status") in TERMINAL_STATUSES:
            return payload
        time.sleep(2)
    raise CanaryError("analysis run did not reach a terminal state")


def run_canary(args: argparse.Namespace) -> dict[str, Any]:
    media_path = Path(args.media).expanduser().resolve()
    if not media_path.is_file():
        raise CanaryError("canary media is unavailable")
    media_bytes = media_path.stat().st_size
    if media_bytes <= 0 or media_bytes > MAX_UPLOAD_BYTES:
        raise CanaryError("canary media is outside the upload byte boundary")
    judge_code = read_windows_credential(args.judge_credential_target)
    credential = TemporaryCredential.load(Path(args.auth_path).expanduser().resolve())
    client = SignedGatewayClient(
        environment_id=args.environment_id,
        service_name=args.service_name,
        credential=credential,
        timeout_seconds=args.timeout_seconds,
        routing_header_name=args.routing_header_name,
        routing_header_value=args.routing_header_value,
    )
    run_id: str | None = None
    try:
        spa = _verify_spa_candidate(client)
        release_identity = _verify_release_identity(
            _expect_json(
                client.request_json("GET", "/api/v1/health"),
                200,
                "health",
            ),
            args.expected_commit_sha,
        )
        readiness = _verify_readiness(
            _expect_json(
                client.request_json("GET", "/api/v1/ready"),
                200,
                "readiness",
            )
        )
        capabilities = _expect_json(
            client.request_json("GET", "/api/v1/capabilities"),
            200,
            "capabilities",
        )
        if capabilities.get("local_analysis_max_seconds") != 300:
            raise CanaryError("deployed local analysis duration boundary is not 300 seconds")

        gymti = _verify_anonymous_gymti_fallback(client)

        public_session = _expect_json(
            client.request_json("GET", "/api/v1/access/session"),
            200,
            "public session",
        )
        if public_session.get("tier") != "public" or public_session.get("can_analyze"):
            raise CanaryError("anonymous analysis is not fail-closed")

        judge_session = _expect_json(
            client.request_json(
                "POST",
                "/api/v1/access/session",
                payload={"access_code": judge_code},
                same_origin=True,
            ),
            200,
            "judge session",
        )
        if judge_session.get("tier") != "judge" or not judge_session.get("can_analyze"):
            raise CanaryError("judge session was not granted analysis capacity")

        created = _expect_json(
            client.upload_video("/api/v1/analysis-runs/local", media_path),
            202,
            "local analysis upload",
        )
        run_id_value = created.get("id")
        if not isinstance(run_id_value, str) or not run_id_value:
            raise CanaryError("analysis upload did not return a run id")
        run_id = run_id_value

        sse_status, event_types, terminal_observed = client.stream_events(
            f"/api/v1/analysis-runs/{run_id}/events"
        )
        terminal = _wait_for_terminal(
            client,
            run_id,
            timeout_seconds=args.timeout_seconds,
        )
        status = terminal.get("status")
        coverage_status = terminal.get("coverage_status")
        candidates = terminal.get("candidates")
        gaps = terminal.get("coverage_gaps")
        error = terminal.get("error")
        receipt = {
            "schema_version": 1,
            "service": args.service_name,
            "release": release_identity,
            "readiness": readiness,
            "access": "signed-private-http-api",
            "spa": spa,
            "media_bytes": media_bytes,
            "capabilities": {
                "local_analysis_max_seconds": capabilities.get(
                    "local_analysis_max_seconds"
                ),
                "local_upload_max_bytes": capabilities.get("local_upload_max_bytes"),
            },
            "access_gate": {
                "anonymous_can_analyze": bool(public_session.get("can_analyze")),
                "judge_can_analyze": bool(judge_session.get("can_analyze")),
            },
            "gymti": gymti,
            "upload_http_status": 202,
            "sse": {
                "http_status": sse_status,
                "event_types": event_types,
                "terminal_event_observed": terminal_observed,
            },
            "run": {
                "status": status,
                "coverage_status": coverage_status,
                "candidate_count": len(candidates) if isinstance(candidates, list) else 0,
                "coverage_gap_count": len(gaps) if isinstance(gaps, list) else 0,
                "source_duration_seconds": terminal.get("source_duration_seconds"),
                "processed_seconds": terminal.get("processed_seconds"),
                "error_code": error.get("code") if isinstance(error, dict) else None,
            },
        }
        if status != "completed":
            raise CanaryError(
                f"analysis run ended with {status!s}; receipt={json.dumps(receipt, separators=(',', ':'))}"
            )
        if coverage_status not in {"complete", "partial", "insufficient"}:
            raise CanaryError("analysis run returned an invalid coverage status")
        return receipt
    except BaseException:
        if run_id is not None:
            try:
                client.request_json(
                    "DELETE",
                    f"/api/v1/analysis-runs/{run_id}",
                    same_origin=True,
                )
            except Exception:
                pass
        raise
    finally:
        client.close()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run one content-redacted signed CloudBase private video canary."
    )
    parser.add_argument("--environment-id", required=True)
    parser.add_argument("--media", required=True)
    parser.add_argument("--service-name", default="trainpal-demo")
    parser.add_argument(
        "--auth-path",
        default=str(Path.home() / ".config" / ".cloudbase" / "auth.json"),
    )
    parser.add_argument(
        "--judge-credential-target",
        default="HakimiFitness.CloudBase.JudgeCode",
    )
    parser.add_argument("--timeout-seconds", type=float, default=240)
    parser.add_argument("--routing-header-name", required=True)
    parser.add_argument("--routing-header-value-stdin", action="store_true", required=True)
    parser.add_argument("--expected-commit-sha", required=True)
    parser.add_argument("--output", required=True)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        routing_header_value = sys.stdin.read().strip()
        if (
            not routing_header_value
            or len(routing_header_value) > 128
            or re.fullmatch(r"[!-~]+", routing_header_value) is None
        ):
            raise CanaryError("private routing token from standard input is invalid")
        args.routing_header_value = routing_header_value
        receipt = run_canary(args)
        output = Path(args.output).expanduser().resolve()
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(
            json.dumps(receipt, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        print(json.dumps(receipt, ensure_ascii=False, indent=2))
    except CanaryError as error:
        print(f"private canary failed: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
