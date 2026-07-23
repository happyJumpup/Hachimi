from __future__ import annotations

import os
import subprocess
import threading
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol


class RuntimeCleanupProbeError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class RuntimeCleanupSnapshot:
    residue_count: int
    ffmpeg_process_count: int

    @property
    def clean(self) -> bool:
        return self.residue_count == 0 and self.ffmpeg_process_count == 0


class RuntimeCleanupProbe(Protocol):
    def snapshot(self) -> RuntimeCleanupSnapshot: ...


class RuntimeCleanupMonitor:
    """Count transient analysis residue without exposing its identity."""

    def __init__(
        self,
        temp_root: Path,
        *,
        descendant_ffmpeg_probe: Callable[[], set[int]] | None = None,
    ) -> None:
        self._temp_root = temp_root
        self._descendant_ffmpeg_probe = (
            descendant_ffmpeg_probe or _descendant_ffmpeg_process_ids
        )
        self._tracked_processes: set[subprocess.Popen[bytes]] = set()
        self._lock = threading.Lock()

    def register_ffmpeg_process(self, process: subprocess.Popen[bytes]) -> None:
        with self._lock:
            self._tracked_processes.add(process)

    def unregister_ffmpeg_process(self, process: subprocess.Popen[bytes]) -> None:
        if process.poll() is None:
            return
        with self._lock:
            self._tracked_processes.discard(process)

    def snapshot(self) -> RuntimeCleanupSnapshot:
        try:
            if not self._temp_root.exists():
                residue_count = 0
            elif not self._temp_root.is_dir():
                raise RuntimeCleanupProbeError(
                    "analysis temporary root is not a directory"
                )
            else:
                residue_count = sum(1 for _entry in self._temp_root.iterdir())
            descendant_process_ids = self._descendant_ffmpeg_probe()
        except RuntimeCleanupProbeError:
            raise
        except OSError as error:
            raise RuntimeCleanupProbeError(
                "runtime cleanup state could not be inspected"
            ) from error

        with self._lock:
            active_processes = {
                process for process in self._tracked_processes if process.poll() is None
            }
            self._tracked_processes.intersection_update(active_processes)
            tracked_process_ids = {process.pid for process in active_processes}
        return RuntimeCleanupSnapshot(
            residue_count=residue_count,
            ffmpeg_process_count=len(
                tracked_process_ids.union(descendant_process_ids)
            ),
        )


def _descendant_ffmpeg_process_ids() -> set[int]:
    """Return only this service process tree's live FFmpeg process IDs."""

    proc_root = Path("/proc")
    if os.name != "posix" or not proc_root.is_dir():
        return set()

    children_by_parent: dict[int, set[int]] = {}
    try:
        entries = tuple(proc_root.iterdir())
    except OSError as error:
        raise RuntimeCleanupProbeError(
            "process state could not be inspected"
        ) from error
    for entry in entries:
        if not entry.name.isdecimal():
            continue
        try:
            process_id = int(entry.name)
            status = (entry / "status").read_text(
                encoding="utf-8", errors="replace"
            )
            parent_line = next(
                line for line in status.splitlines() if line.startswith("PPid:")
            )
            parent_id = int(parent_line.split(":", maxsplit=1)[1].strip())
        except (OSError, StopIteration, ValueError):
            continue
        children_by_parent.setdefault(parent_id, set()).add(process_id)

    descendants: set[int] = set()
    pending = list(children_by_parent.get(os.getpid(), set()))
    while pending:
        process_id = pending.pop()
        if process_id in descendants:
            continue
        descendants.add(process_id)
        pending.extend(children_by_parent.get(process_id, set()))

    ffmpeg_process_ids: set[int] = set()
    for process_id in descendants:
        try:
            executable = (proc_root / str(process_id) / "exe").resolve(strict=True)
        except OSError:
            continue
        if executable.name.lower() in {"ffmpeg", "ffmpeg.exe"}:
            ffmpeg_process_ids.add(process_id)
    return ffmpeg_process_ids
