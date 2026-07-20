from dataclasses import dataclass
from pathlib import Path

from hakimi_analysis.models import SourceSummary


@dataclass(frozen=True, slots=True)
class VideoSource:
    id: str
    title: str
    path: Path
    duration_seconds: float

    def summary(self) -> SourceSummary:
        return SourceSummary(
            id=self.id,
            title=self.title,
            media_url=f"/api/v1/sources/{self.id}/media",
            duration_seconds=self.duration_seconds,
        )


class SourceCatalog:
    def __init__(self, sources: list[VideoSource]) -> None:
        self._sources = {source.id: source for source in sources}

    def list(self) -> list[SourceSummary]:
        return [source.summary() for source in self._sources.values()]

    def get(self, source_id: str) -> VideoSource:
        try:
            return self._sources[source_id]
        except KeyError as error:
            raise KeyError(f"unknown source_id: {source_id}") from error


class EmptySourceCatalog(SourceCatalog):
    def __init__(self) -> None:
        super().__init__([])
