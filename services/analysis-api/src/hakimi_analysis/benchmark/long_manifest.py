import json
from pathlib import Path

from hakimi_analysis.benchmark.long_models import (
    EXPECTED_LONG_EXPERIMENT_MODELS,
    LongExperimentManifest,
)

__all__ = [
    "EXPECTED_LONG_EXPERIMENT_MODELS",
    "LongExperimentManifest",
    "load_long_experiment_manifest",
    "validate_long_experiment_paths",
    "write_long_experiment_manifest",
]

_IGNORED_WORKING_ROOTS = frozenset({".benchmark-work", "benchmark-results", "tmp"})


def load_long_experiment_manifest(
    path: Path,
    *,
    repository_root: Path,
) -> LongExperimentManifest:
    manifest = LongExperimentManifest.model_validate_json(path.read_text(encoding="utf-8"))
    _validate_frozen_models(manifest)
    validate_long_experiment_paths(manifest, repository_root=repository_root)
    return manifest


def write_long_experiment_manifest(path: Path, manifest: LongExperimentManifest) -> None:
    _validate_frozen_models(manifest)
    path.write_text(
        json.dumps(manifest.model_dump(mode="json"), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def validate_long_experiment_paths(
    manifest: LongExperimentManifest,
    *,
    repository_root: Path,
) -> None:
    for source in manifest.sources:
        if not _is_safe_source_input(
            source.source_path,
            repository_root=repository_root,
        ):
            raise ValueError("source path must be external or in a Git-ignored working root")
        if not _is_in_ignored_working_root(source.gold_path, repository_root=repository_root):
            raise ValueError("gold path must be in a Git-ignored working root")

    for path in (
        manifest.output.working_root,
        manifest.output.temporary_root,
        manifest.output.raw_results_root,
        manifest.output.summary_path,
    ):
        if not _is_in_ignored_working_root(path, repository_root=repository_root):
            raise ValueError("output path must be in a Git-ignored working root")


def _validate_frozen_models(manifest: LongExperimentManifest) -> None:
    if manifest.models.model_dump() != EXPECTED_LONG_EXPERIMENT_MODELS:
        raise ValueError("long experiment model IDs must exactly match the frozen protocol")


def _is_in_ignored_working_root(path: Path, *, repository_root: Path) -> bool:
    resolved_root = repository_root.resolve()
    candidate = path.resolve() if path.is_absolute() else (resolved_root / path).resolve()
    try:
        relative = candidate.relative_to(resolved_root)
    except ValueError:
        return False
    return bool(relative.parts) and relative.parts[0] in _IGNORED_WORKING_ROOTS


def _is_safe_source_input(path: Path, *, repository_root: Path) -> bool:
    resolved_root = repository_root.resolve()
    if not path.is_absolute():
        return _is_in_ignored_working_root(path, repository_root=repository_root)
    try:
        path.resolve().relative_to(resolved_root)
    except ValueError:
        return True
    return _is_in_ignored_working_root(path, repository_root=repository_root)
