import asyncio
import base64
import hashlib
import hmac
import json
import math
import secrets
import time
from collections.abc import Callable
from contextlib import suppress
from dataclasses import dataclass
from typing import Literal

from hakimi_analysis.models import AccessSessionView, AccessTier

ACCESS_COOKIE_NAME = "hachimi_access"
ACCESS_SESSION_SECONDS = 12 * 60 * 60
ADMISSION_REASON_HEADER = "X-TrainPal-Admission-Reason"
AdmissionReason = Literal["capacity", "rate_limit", "session_active"]


@dataclass(frozen=True, slots=True)
class AccessSession:
    id: str
    tier: AccessTier
    expires_at: int


class AdmissionDenied(RuntimeError):
    def __init__(
        self,
        *,
        reason: AdmissionReason,
        retry_after_seconds: int,
    ) -> None:
        super().__init__("analysis admission denied")
        self.reason = reason
        self.retry_after_seconds = retry_after_seconds


class AccessCodeRateLimited(RuntimeError):
    def __init__(self, *, retry_after_seconds: int) -> None:
        super().__init__("access code attempts rate limited")
        self.retry_after_seconds = retry_after_seconds


@dataclass(slots=True)
class AnalysisLease:
    manager: "AccessManager"
    session_id: str
    tier: AccessTier
    source_id: str
    client_ip: str
    provisional: bool = False
    run_id: str | None = None
    attempt_recorded: bool = False
    attempt_recorded_at: float | None = None
    committed: bool = False
    released: bool = False

    async def activate(self, source_id: str) -> None:
        await self.manager.activate(self, source_id)

    async def bind(self, run_id: str) -> None:
        await self.manager.bind(self, run_id)

    def commit(self, run_id: str) -> None:
        """Transfer a provisional lease to a created run before yielding control."""

        self.run_id = run_id
        self.committed = True

    async def release(self) -> None:
        await self.manager.release(self)


@dataclass(frozen=True, slots=True)
class ActiveRun:
    source_id: str
    run_id: str | None


class AccessManager:
    def __init__(
        self,
        *,
        cookie_secret: str,
        judge_access_code: str,
        judge_concurrency: int = 2,
        public_concurrency: int = 0,
        judge_attempt_limit: int = 10,
        judge_attempt_window_seconds: int = 3_600,
        public_attempt_limit: int = 1,
        public_attempt_window_seconds: int = 600,
        upgrade_attempt_limit: int = 5,
        upgrade_attempt_window_seconds: int = 600,
        time_source: Callable[[], float] = time.monotonic,
    ) -> None:
        if len(cookie_secret.encode("utf-8")) < 32:
            raise ValueError("access cookie secret must contain at least 32 bytes")
        if not judge_access_code:
            raise ValueError("judge access code must not be empty")
        if judge_concurrency < 0 or public_concurrency < 0:
            raise ValueError("analysis concurrency must not be negative")
        if min(
            judge_attempt_limit,
            judge_attempt_window_seconds,
            public_attempt_limit,
            public_attempt_window_seconds,
            upgrade_attempt_limit,
            upgrade_attempt_window_seconds,
        ) < 1:
            raise ValueError("analysis attempt policy must be positive")
        self._cookie_secret = cookie_secret.encode("utf-8")
        self._judge_access_code = judge_access_code
        self._capacities = {
            AccessTier.JUDGE: judge_concurrency,
            AccessTier.PUBLIC: public_concurrency,
        }
        self._active_counts = {AccessTier.JUDGE: 0, AccessTier.PUBLIC: 0}
        self._attempt_policies = {
            AccessTier.JUDGE: (judge_attempt_limit, judge_attempt_window_seconds),
            AccessTier.PUBLIC: (public_attempt_limit, public_attempt_window_seconds),
        }
        self._active_by_session: dict[str, AnalysisLease] = {}
        self._session_attempts: dict[tuple[AccessTier, str], list[float]] = {}
        self._ip_attempts: dict[tuple[AccessTier, str], list[float]] = {}
        self._upgrade_attempt_limit = upgrade_attempt_limit
        self._upgrade_attempt_window_seconds = upgrade_attempt_window_seconds
        self._upgrade_session_attempts: dict[str, list[float]] = {}
        self._upgrade_ip_attempts: dict[str, list[float]] = {}
        self._time_source = time_source
        self._lock = asyncio.Lock()

    def resolve(self, cookie_value: str | None) -> AccessSession:
        if cookie_value:
            session = self._decode(cookie_value)
            if session is not None:
                return session
        return AccessSession(
            id=secrets.token_urlsafe(24),
            tier=AccessTier.PUBLIC,
            expires_at=int(time.time()) + ACCESS_SESSION_SECONDS,
        )

    async def upgrade(
        self,
        session: AccessSession,
        access_code: str,
        *,
        client_ip: str,
    ) -> AccessSession | None:
        async with self._lock:
            retry_after = self._upgrade_retry_after(session.id, client_ip)
            if retry_after is not None:
                raise AccessCodeRateLimited(retry_after_seconds=retry_after)
            if not hmac.compare_digest(access_code, self._judge_access_code):
                now = self._time_source()
                self._upgrade_session_attempts.setdefault(session.id, []).append(now)
                self._upgrade_ip_attempts.setdefault(client_ip, []).append(now)
                return None
            self._upgrade_session_attempts.pop(session.id, None)
            self._upgrade_ip_attempts.pop(client_ip, None)
            return AccessSession(
                id=session.id,
                tier=AccessTier.JUDGE,
                expires_at=int(time.time()) + ACCESS_SESSION_SECONDS,
            )

    def encode(self, session: AccessSession) -> str:
        payload = json.dumps(
            {"id": session.id, "tier": session.tier.value, "exp": session.expires_at},
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
        encoded = base64.urlsafe_b64encode(payload).rstrip(b"=")
        signature = hmac.new(self._cookie_secret, encoded, hashlib.sha256).digest()
        encoded_signature = base64.urlsafe_b64encode(signature).rstrip(b"=")
        return f"{encoded.decode('ascii')}.{encoded_signature.decode('ascii')}"

    def view(
        self,
        session: AccessSession,
        *,
        client_ip: str | None = None,
    ) -> AccessSessionView:
        retry_after = self._rate_retry_after(session.tier, session.id, client_ip)
        can_analyze = (
            session.id not in self._active_by_session
            and self._active_counts[session.tier] < self._capacities[session.tier]
            and retry_after is None
        )
        return AccessSessionView(
            tier=session.tier,
            can_analyze=can_analyze,
            retry_after_seconds=retry_after,
        )

    async def active_run(self, session_id: str) -> ActiveRun | None:
        async with self._lock:
            lease = self._active_by_session.get(session_id)
            if lease is None or lease.released:
                return None
            return ActiveRun(source_id=lease.source_id, run_id=lease.run_id)

    async def reserve(
        self,
        session: AccessSession,
        *,
        source_id: str,
        client_ip: str,
    ) -> AnalysisLease:
        async with self._lock:
            lease = self._reserve_locked(
                session,
                source_id=source_id,
                client_ip=client_ip,
                provisional=False,
            )
            self._record_attempt_locked(lease)
            return lease

    async def reserve_provisional(
        self,
        session: AccessSession,
        *,
        source_id: str,
        client_ip: str,
    ) -> AnalysisLease:
        """Hold capacity before reading a body without charging an attempt."""

        async with self._lock:
            return self._reserve_locked(
                session,
                source_id=source_id,
                client_ip=client_ip,
                provisional=True,
            )

    def _reserve_locked(
        self,
        session: AccessSession,
        *,
        source_id: str,
        client_ip: str,
        provisional: bool,
    ) -> AnalysisLease:
        # A full pool is the most immediate retry condition. The public canary
        # depends on this precedence when concurrent clients share one egress IP.
        if self._active_counts[session.tier] >= self._capacities[session.tier]:
            raise AdmissionDenied(reason="capacity", retry_after_seconds=15)
        retry_after = self._rate_retry_after(session.tier, session.id, client_ip)
        if retry_after is not None:
            raise AdmissionDenied(
                reason="rate_limit",
                retry_after_seconds=retry_after,
            )
        if session.id in self._active_by_session:
            raise AdmissionDenied(reason="session_active", retry_after_seconds=1)
        lease = AnalysisLease(
            manager=self,
            session_id=session.id,
            tier=session.tier,
            source_id=source_id,
            client_ip=client_ip,
            provisional=provisional,
        )
        self._active_counts[session.tier] += 1
        self._active_by_session[session.id] = lease
        return lease

    async def activate(self, lease: AnalysisLease, source_id: str) -> None:
        """Turn a provisional body-ingress hold into one analysis attempt."""

        async with self._lock:
            if lease.released or self._active_by_session.get(lease.session_id) is not lease:
                raise RuntimeError("analysis lease is no longer active")
            lease.source_id = source_id
            self._record_attempt_locked(lease)

    def _record_attempt_locked(self, lease: AnalysisLease) -> None:
        if lease.attempt_recorded:
            return
        lease.attempt_recorded = True
        now = self._time_source()
        lease.attempt_recorded_at = now
        self._session_attempts.setdefault((lease.tier, lease.session_id), []).append(now)
        self._ip_attempts.setdefault((lease.tier, lease.client_ip), []).append(now)

    async def bind(self, lease: AnalysisLease, run_id: str) -> None:
        async with self._lock:
            if lease.released:
                return
            lease.run_id = run_id
            lease.committed = True

    async def release(self, lease: AnalysisLease) -> None:
        async with self._lock:
            if lease.released:
                return
            lease.released = True
            if lease.provisional and not lease.committed:
                self._rollback_attempt_locked(lease)
            if self._active_by_session.get(lease.session_id) is lease:
                self._active_by_session.pop(lease.session_id, None)
            self._active_counts[lease.tier] = max(0, self._active_counts[lease.tier] - 1)

    def _rollback_attempt_locked(self, lease: AnalysisLease) -> None:
        recorded_at = lease.attempt_recorded_at
        if not lease.attempt_recorded or recorded_at is None:
            return
        for attempts, key in (
            (self._session_attempts, (lease.tier, lease.session_id)),
            (self._ip_attempts, (lease.tier, lease.client_ip)),
        ):
            values = attempts.get(key)
            if values is None:
                continue
            with suppress(ValueError):
                values.remove(recorded_at)
            if not values:
                attempts.pop(key, None)
        lease.attempt_recorded = False
        lease.attempt_recorded_at = None

    def _rate_retry_after(
        self,
        tier: AccessTier,
        session_id: str,
        client_ip: str | None,
    ) -> int | None:
        limit, window_seconds = self._attempt_policies[tier]
        now = self._time_source()
        retry_values = [
            self._retry_for_window(
                self._session_attempts,
                (tier, session_id),
                now=now,
                limit=limit,
                window_seconds=window_seconds,
            )
        ]
        if client_ip is not None:
            retry_values.append(
                self._retry_for_window(
                    self._ip_attempts,
                    (tier, client_ip),
                    now=now,
                    limit=limit,
                    window_seconds=window_seconds,
                )
            )
        active_retries = [value for value in retry_values if value is not None]
        return max(active_retries, default=None)

    def _upgrade_retry_after(self, session_id: str, client_ip: str) -> int | None:
        now = self._time_source()
        retries = (
            self._retry_for_simple_window(
                self._upgrade_session_attempts,
                session_id,
                now=now,
                limit=self._upgrade_attempt_limit,
                window_seconds=self._upgrade_attempt_window_seconds,
            ),
            self._retry_for_simple_window(
                self._upgrade_ip_attempts,
                client_ip,
                now=now,
                limit=self._upgrade_attempt_limit,
                window_seconds=self._upgrade_attempt_window_seconds,
            ),
        )
        return max((value for value in retries if value is not None), default=None)

    @staticmethod
    def _retry_for_simple_window(
        attempts_by_key: dict[str, list[float]],
        key: str,
        *,
        now: float,
        limit: int,
        window_seconds: int,
    ) -> int | None:
        cutoff = now - window_seconds
        attempts = [attempt for attempt in attempts_by_key.get(key, []) if attempt > cutoff]
        if attempts:
            attempts_by_key[key] = attempts
        else:
            attempts_by_key.pop(key, None)
        if len(attempts) < limit:
            return None
        return max(1, math.ceil(window_seconds - (now - attempts[-limit])))

    @staticmethod
    def _retry_for_window(
        attempts_by_key: dict[tuple[AccessTier, str], list[float]],
        key: tuple[AccessTier, str],
        *,
        now: float,
        limit: int,
        window_seconds: int,
    ) -> int | None:
        cutoff = now - window_seconds
        attempts = [attempt for attempt in attempts_by_key.get(key, []) if attempt > cutoff]
        if attempts:
            attempts_by_key[key] = attempts
        else:
            attempts_by_key.pop(key, None)
        if len(attempts) < limit:
            return None
        return max(1, math.ceil(window_seconds - (now - attempts[-limit])))

    def _decode(self, value: str) -> AccessSession | None:
        try:
            encoded, encoded_signature = value.split(".", maxsplit=1)
            encoded_bytes = encoded.encode("ascii")
            signature = _decode_base64(encoded_signature)
            expected = hmac.new(self._cookie_secret, encoded_bytes, hashlib.sha256).digest()
            if not hmac.compare_digest(signature, expected):
                return None
            payload = json.loads(_decode_base64(encoded).decode("utf-8"))
            session_id = payload["id"]
            tier = AccessTier(payload["tier"])
            expires_at = payload["exp"]
            if (
                not isinstance(session_id, str)
                or not session_id
                or not isinstance(expires_at, int)
                or expires_at <= int(time.time())
            ):
                return None
            return AccessSession(id=session_id, tier=tier, expires_at=expires_at)
        except (KeyError, TypeError, ValueError, UnicodeError, json.JSONDecodeError):
            return None


def _decode_base64(value: str) -> bytes:
    padding = "=" * (-len(value) % 4)
    return base64.urlsafe_b64decode(value + padding)
