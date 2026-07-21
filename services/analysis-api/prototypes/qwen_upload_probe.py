"""Diagnose the model-bound DashScope temporary-OSS upload boundary only."""

from __future__ import annotations

import argparse
import mimetypes
import os
import socket
import subprocess
import time
from email.utils import formatdate
from pathlib import Path
from urllib.parse import urlparse

import httpx
import requests
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[3]
POLICY_URL = "https://dashscope.aliyuncs.com/api/v1/uploads"


def required_env(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--client", choices=("httpx", "requests", "curl"), default="requests"
    )
    parser.add_argument("--ignore-proxy", action="store_true")
    parser.add_argument("--resolve-ip")
    parser.add_argument("--proxy-url")
    args = parser.parse_args()

    load_dotenv(ROOT / ".env.local", override=False)
    api_key = required_env("DASHSCOPE_API_KEY")
    model = os.getenv("QWEN_VIDEO_MODEL", "qwen3-vl-flash").strip()
    video_path = Path(required_env("QWEN_SPIKE_VIDEO"))
    if not video_path.is_file():
        raise FileNotFoundError("Probe video does not exist")

    size_mb = video_path.stat().st_size / 1024 / 1024
    print(
        f"probe_started client={args.client} ignore_proxy={args.ignore_proxy} "
        f"size_mb={size_mb:.3f}",
        flush=True,
    )
    headers = {"Authorization": f"Bearer {api_key}"}
    policy_started = time.perf_counter()
    if args.client == "httpx":
        with httpx.Client(timeout=60, trust_env=not args.ignore_proxy) as client:
            policy_response = client.get(
                POLICY_URL,
                params={"action": "getPolicy", "model": model},
                headers=headers,
            )
            policy_response.raise_for_status()
            policy_payload = policy_response.json()
    else:
        session = requests.Session()
        session.trust_env = not args.ignore_proxy
        policy_response = session.get(
            POLICY_URL,
            params={"action": "getPolicy", "model": model},
            headers=headers,
            timeout=(30, 60),
        )
        policy_response.raise_for_status()
        policy_payload = policy_response.json()
    policy = policy_payload["data"]
    host = urlparse(policy["upload_host"]).hostname
    print(
        "policy_completed "
        f"seconds={time.perf_counter() - policy_started:.2f} "
        f"host={host} limit_mb={policy['max_file_size_mb']}",
        flush=True,
    )

    object_key = f"{policy['upload_dir']}/{video_path.name}"
    fields = {
        "OSSAccessKeyId": policy["oss_access_key_id"],
        "policy": policy["policy"],
        "Signature": policy["signature"],
        "key": object_key,
        "x-oss-object-acl": policy["x_oss_object_acl"],
        "x-oss-forbid-overwrite": policy["x_oss_forbid_overwrite"],
        "success_action_status": "200",
        "x-oss-content-type": mimetypes.guess_type(video_path)[0]
        or "application/octet-stream",
    }
    upload_headers = {
        "Accept": "application/json",
        "Date": formatdate(timeval=None, localtime=False, usegmt=True),
        "User-Agent": "dashscope-upload-probe",
    }
    upload_started = time.perf_counter()
    if args.client == "curl":
        if not host or (not args.resolve_ip and not args.proxy_url):
            raise RuntimeError("curl transport requires --resolve-ip or --proxy-url")
        command = [
            "curl.exe",
            "--connect-timeout",
            "30",
            "--max-time",
            "180",
            "--silent",
            "--show-error",
            "--output",
            "NUL",
            "--write-out",
            "%{http_code}",
        ]
        if args.proxy_url:
            command.extend(["--proxy", args.proxy_url])
        else:
            command.extend(
                ["--noproxy", "*", "--resolve", f"{host}:443:{args.resolve_ip}"]
            )
        for name, value in upload_headers.items():
            command.extend(["--header", f"{name}: {value}"])
        for name, value in fields.items():
            command.extend(["--form-string", f"{name}={value}"])
        command.extend(
            [
                "--form",
                f"file=@{video_path};type=video/mp4",
                policy["upload_host"],
            ]
        )
        completed = subprocess.run(command, capture_output=True, text=True, check=False)
        if completed.returncode != 0:
            raise RuntimeError(f"curl upload failed with exit code {completed.returncode}")
        status_code = int(completed.stdout.strip())
        if status_code != 200:
            raise RuntimeError(f"curl upload returned HTTP {status_code}")
        print(
            f"upload_completed seconds={time.perf_counter() - upload_started:.2f} "
            f"status={status_code}",
            flush=True,
        )
        return 0

    original_getaddrinfo = socket.getaddrinfo

    def resolve_upload_host(
        requested_host: str,
        port: int,
        family: int = 0,
        type_: int = 0,
        proto: int = 0,
        flags: int = 0,
    ) -> list[tuple[int, int, int, str, tuple[str, int]]]:
        target = args.resolve_ip if requested_host == host and args.resolve_ip else requested_host
        return original_getaddrinfo(target, port, family, type_, proto, flags)

    socket.getaddrinfo = resolve_upload_host
    try:
        with video_path.open("rb") as video_file:
            files = {"file": (video_path.name, video_file, "video/mp4")}
            if args.client == "httpx":
                with httpx.Client(timeout=180, trust_env=not args.ignore_proxy) as client:
                    upload_response = client.post(
                        policy["upload_host"],
                        data=fields,
                        files=files,
                        headers=upload_headers,
                    )
            else:
                session = requests.Session()
                session.trust_env = not args.ignore_proxy
                upload_response = session.post(
                    policy["upload_host"],
                    data=fields,
                    files=files,
                    headers=upload_headers,
                    timeout=(30, 180),
                )
    finally:
        socket.getaddrinfo = original_getaddrinfo
    upload_response.raise_for_status()
    print(
        f"upload_completed seconds={time.perf_counter() - upload_started:.2f} "
        f"status={upload_response.status_code}",
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
