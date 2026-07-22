from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from time import monotonic
from typing import Any
from uuid import uuid4

from qcloud_cos import CosConfig, CosS3Client

from hakimi_analysis.observability import log_safe_fields
from hakimi_analysis.providers.base import ProviderError

LOGGER = logging.getLogger("hakimi_analysis.temporary_cos")
_PUBLIC_GRANTEE_URIS = {
    "http://cam.qcloud.com/groups/global/AllUsers",
    "http://cam.qcloud.com/groups/global/AuthenticatedUsers",
}


class BackupSafetyGate:
    def __init__(self) -> None:
        self._failure_code: str | None = None

    @property
    def available(self) -> bool:
        return self._failure_code is None

    def ensure_available(self) -> None:
        if self._failure_code is not None:
            raise ProviderError(
                "security_error",
                "temporary fallback storage is disabled after a cleanup failure",
                retryable=False,
            )

    def block(self, failure_code: str) -> None:
        self._failure_code = failure_code


class TencentCosTemporaryStore:
    def __init__(
        self,
        *,
        secret_id: str,
        secret_key: str,
        region: str,
        bucket: str,
        object_prefix: str,
        signed_url_ttl_seconds: int = 600,
        lifecycle_days: int = 1,
        safety_gate: BackupSafetyGate | None = None,
        client: Any | None = None,
    ) -> None:
        self._region = region
        self._bucket = bucket
        self._object_prefix = object_prefix.strip("/")
        self._signed_url_ttl_seconds = signed_url_ttl_seconds
        self._lifecycle_days = lifecycle_days
        self._safety_gate = safety_gate or BackupSafetyGate()
        self._client = client or CosS3Client(
            CosConfig(
                Region=region,
                SecretId=secret_id,
                SecretKey=secret_key,
                Scheme="https",
            )
        )

    @property
    def safety_gate(self) -> BackupSafetyGate:
        return self._safety_gate

    @asynccontextmanager
    async def signed_read_url(self, video_path: Path) -> AsyncIterator[str]:
        self._safety_gate.ensure_available()
        try:
            input_bytes = video_path.stat().st_size
        except OSError as error:
            raise ProviderError(
                "media_error",
                "visual fallback chunk is unavailable",
                retryable=False,
            ) from error
        object_key = f"{self._object_prefix}/{uuid4().hex}.mp4"
        uploaded = False
        primary_error: BaseException | None = None
        started_at = monotonic()
        try:
            await _shielded_thread_call(self._upload, video_path, object_key)
            uploaded = True
            signed_url = await _shielded_thread_call(self._signed_url, object_key)
            log_safe_fields(
                LOGGER,
                provider="tencent_cos",
                adapter_version="cos-private-transfer-v1",
                input_bytes=input_bytes,
                media_ms=round((monotonic() - started_at) * 1000),
            )
            yield signed_url
        except asyncio.CancelledError as error:
            primary_error = error
            raise
        except ProviderError as error:
            primary_error = error
            raise
        except Exception as error:
            primary_error = error
            raise ProviderError(
                "provider_error",
                "temporary fallback upload failed",
                retryable=True,
            ) from error
        finally:
            if uploaded:
                cleanup_started_at = monotonic()
                try:
                    await _shielded_thread_call(self._delete, object_key)
                except BaseException as cleanup_error:
                    self._safety_gate.block("cos_cleanup_failed")
                    log_safe_fields(
                        LOGGER,
                        provider="tencent_cos",
                        adapter_version="cos-private-transfer-v1",
                        cleanup_ms=round((monotonic() - cleanup_started_at) * 1000),
                        error_code="cos_cleanup_failed",
                    )
                    if primary_error is None:
                        raise ProviderError(
                            "security_error",
                            "temporary fallback object cleanup failed",
                            retryable=False,
                        ) from cleanup_error
                else:
                    log_safe_fields(
                        LOGGER,
                        provider="tencent_cos",
                        adapter_version="cos-private-transfer-v1",
                        cleanup_ms=round((monotonic() - cleanup_started_at) * 1000),
                    )

    def check_security_configuration(self) -> bool:
        try:
            acl = self._client.get_bucket_acl(Bucket=self._bucket)
            encryption = self._client.get_bucket_encryption(Bucket=self._bucket)
            lifecycle = self._client.get_bucket_lifecycle(Bucket=self._bucket)
        except Exception:
            return False
        return (
            not _has_public_grant(acl)
            and _uses_aes256(encryption)
            and _has_one_day_lifecycle(
                lifecycle,
                prefix=self._object_prefix,
                max_days=self._lifecycle_days,
            )
        )

    def _upload(self, video_path: Path, object_key: str) -> None:
        with video_path.open("rb") as stream:
            self._client.put_object(
                Bucket=self._bucket,
                Key=object_key,
                Body=stream,
                ContentType="video/mp4",
                ServerSideEncryption="AES256",
            )

    def _signed_url(self, object_key: str) -> str:
        result = self._client.get_presigned_url(
            Method="GET",
            Bucket=self._bucket,
            Key=object_key,
            Expired=self._signed_url_ttl_seconds,
        )
        if not isinstance(result, str) or not result.startswith("https://"):
            raise ValueError("COS did not return an HTTPS signed URL")
        return result

    def _delete(self, object_key: str) -> None:
        self._client.delete_object(Bucket=self._bucket, Key=object_key)


async def _shielded_thread_call(function: Any, *args: Any) -> Any:
    task = asyncio.create_task(asyncio.to_thread(function, *args))
    try:
        return await asyncio.shield(task)
    except asyncio.CancelledError:
        await asyncio.shield(asyncio.gather(task, return_exceptions=True))
        raise


def _has_public_grant(payload: object) -> bool:
    if not isinstance(payload, dict):
        return True
    access_control = payload.get("AccessControlList", {})
    if not isinstance(access_control, dict):
        return True
    grants = access_control.get("Grant", [])
    if isinstance(grants, dict):
        grants = [grants]
    if not isinstance(grants, list):
        return True
    for grant in grants:
        if not isinstance(grant, dict):
            return True
        grantee = grant.get("Grantee", {})
        if isinstance(grantee, dict) and grantee.get("URI") in _PUBLIC_GRANTEE_URIS:
            return True
    return False


def _uses_aes256(payload: object) -> bool:
    if not isinstance(payload, dict):
        return False
    rule = payload.get("Rule")
    rules = rule if isinstance(rule, list) else [rule]
    return any(
        isinstance(item, dict)
        and isinstance(item.get("ApplyServerSideEncryptionByDefault"), dict)
        and item["ApplyServerSideEncryptionByDefault"].get("SSEAlgorithm") == "AES256"
        for item in rules
    )


def _has_one_day_lifecycle(payload: object, *, prefix: str, max_days: int) -> bool:
    if not isinstance(payload, dict):
        return False
    raw_rules = payload.get("Rule", [])
    rules = raw_rules if isinstance(raw_rules, list) else [raw_rules]
    for rule in rules:
        if not isinstance(rule, dict) or rule.get("Status") != "Enabled":
            continue
        rule_prefix = str(rule.get("Filter", {}).get("Prefix", rule.get("Prefix", "")))
        expiration = rule.get("Expiration", {})
        if (
            isinstance(expiration, dict)
            and rule_prefix.strip("/") == prefix.strip("/")
            and isinstance(expiration.get("Days"), int)
            and 1 <= expiration["Days"] <= max_days
        ):
            return True
    return False


__all__ = ["BackupSafetyGate", "TencentCosTemporaryStore"]
