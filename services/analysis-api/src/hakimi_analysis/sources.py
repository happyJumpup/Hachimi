import hashlib
import json
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from urllib.parse import quote, urlparse

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from hakimi_analysis.models import SourceSummary

MAX_ANALYZABLE_SOURCE_DURATION_SECONDS = 60.0


@dataclass(frozen=True, slots=True)
class VideoSource:
    id: str
    title: str
    path: Path
    duration_seconds: float
    public_media_url: str | None = None
    origin_url: str | None = None
    expected_sha256: str | None = None

    def summary(self) -> SourceSummary:
        return SourceSummary(
            id=self.id,
            title=self.title,
            media_url=f"/api/v1/sources/{self.id}/media",
            duration_seconds=self.duration_seconds,
            origin_url=self.origin_url,
        )


class SourceManifestError(ValueError):
    """A deployment source manifest failed its safe validation contract."""


class _ManifestSource(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(pattern=r"^[a-z0-9][a-z0-9._-]{0,63}$")
    title: str = Field(min_length=1)
    media_path: str = Field(min_length=1)
    duration_seconds: float = Field(gt=0, le=MAX_ANALYZABLE_SOURCE_DURATION_SECONDS)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    origin_url: str | None = None


class _SourceManifest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: int
    sources: list[_ManifestSource] = Field(min_length=1)


def load_source_manifest(manifest_path: Path) -> _SourceManifest:
    try:
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest = _SourceManifest.model_validate(payload)
    except (OSError, json.JSONDecodeError, ValidationError) as error:
        raise SourceManifestError("source manifest is invalid") from error
    if manifest.version != 1:
        raise SourceManifestError("source manifest version is unsupported")
    if len({source.id for source in manifest.sources}) != len(manifest.sources):
        raise SourceManifestError("source ids must be unique")
    media_paths = [_safe_media_path(source.media_path).as_posix() for source in manifest.sources]
    if len({path.casefold() for path in media_paths}) != len(media_paths):
        raise SourceManifestError("source media paths must be unique")
    return manifest


class SourceCatalog:
    def __init__(self, sources: list[VideoSource], *, manifest_backed: bool = False) -> None:
        if len({source.id for source in sources}) != len(sources):
            raise SourceManifestError("source ids must be unique")
        if any(
            source.duration_seconds <= 0
            or source.duration_seconds > MAX_ANALYZABLE_SOURCE_DURATION_SECONDS
            for source in sources
        ):
            raise SourceManifestError("source duration exceeds the analysis boundary")
        self._sources = {source.id: source for source in sources}
        self._manifest_backed = manifest_backed

    @classmethod
    def from_manifest(
        cls,
        *,
        manifest_path: Path,
        media_root: Path,
        public_media_base_url: str,
        duration_probe: Callable[[Path], float],
    ) -> "SourceCatalog":
        manifest = load_source_manifest(manifest_path)

        parsed_base = urlparse(public_media_base_url)
        if (
            parsed_base.scheme != "https"
            or not parsed_base.netloc
            or parsed_base.username is not None
            or parsed_base.password is not None
            or parsed_base.params
            or parsed_base.query
            or parsed_base.fragment
        ):
            raise SourceManifestError("public media base url must use https")

        resolved_root = media_root.expanduser().resolve()
        sources: list[VideoSource] = []
        for item in manifest.sources:
            relative_path = _safe_media_path(item.media_path)
            source_path = (resolved_root / Path(*relative_path.parts)).resolve()
            try:
                source_path.relative_to(resolved_root)
            except ValueError as error:
                raise SourceManifestError("source media path escapes its root") from error
            if not source_path.is_file():
                raise SourceManifestError("source media file is unavailable")
            try:
                digest = _sha256(source_path)
            except OSError as error:
                raise SourceManifestError("source media hash cannot be read") from error
            if digest != item.sha256:
                raise SourceManifestError("source media hash does not match")
            try:
                actual_duration = duration_probe(source_path)
            except Exception as error:
                raise SourceManifestError("source media duration cannot be read") from error
            if actual_duration > MAX_ANALYZABLE_SOURCE_DURATION_SECONDS:
                raise SourceManifestError("source media exceeds the analysis boundary")
            if abs(actual_duration - item.duration_seconds) > 1:
                raise SourceManifestError("source media duration does not match")
            if item.origin_url is not None:
                parsed_origin = urlparse(item.origin_url)
                if (
                    parsed_origin.scheme not in {"http", "https"}
                    or not parsed_origin.netloc
                    or parsed_origin.username is not None
                    or parsed_origin.password is not None
                ):
                    raise SourceManifestError("source origin url is invalid")
            encoded_path = quote(relative_path.as_posix(), safe="/")
            public_url = f"{public_media_base_url.rstrip('/')}/{encoded_path}"
            sources.append(
                VideoSource(
                    id=item.id,
                    title=item.title,
                    path=source_path,
                    duration_seconds=item.duration_seconds,
                    public_media_url=public_url,
                    origin_url=item.origin_url,
                    expected_sha256=item.sha256,
                )
            )
        return cls(sources, manifest_backed=True)

    def list(self) -> list[SourceSummary]:
        return [source.summary() for source in self._sources.values()]

    def get(self, source_id: str) -> VideoSource:
        try:
            return self._sources[source_id]
        except KeyError as error:
            raise KeyError(f"unknown source_id: {source_id}") from error

    @property
    def manifest_backed(self) -> bool:
        return self._manifest_backed

    def validate_media(self, duration_probe: Callable[[Path], float]) -> bool:
        if not self._manifest_backed or not self._sources:
            return False
        for source in self._sources.values():
            if source.expected_sha256 is None or not source.path.is_file():
                return False
            try:
                if _sha256(source.path) != source.expected_sha256:
                    return False
                actual_duration = duration_probe(source.path)
                if actual_duration > MAX_ANALYZABLE_SOURCE_DURATION_SECONDS:
                    return False
                if abs(actual_duration - source.duration_seconds) > 1:
                    return False
            except Exception:
                return False
        return True


class EmptySourceCatalog(SourceCatalog):
    def __init__(self) -> None:
        super().__init__([])


def _safe_media_path(value: str) -> PurePosixPath:
    if "\\" in value or value.startswith("/"):
        raise SourceManifestError("source media path must be relative POSIX")
    raw_parts = value.split("/")
    if any(part in {"", ".", ".."} for part in raw_parts):
        raise SourceManifestError("source media path contains unsafe segments")
    return PurePosixPath(value)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()
