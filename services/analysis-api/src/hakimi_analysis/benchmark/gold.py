import json

from hakimi_analysis.benchmark.manifest import BenchmarkManifest
from hakimi_analysis.benchmark.models import GoldAnnotation, GoldStatus


def write_blank_gold_templates(manifest: BenchmarkManifest) -> None:
    for sample in manifest.samples:
        sample.gold_path.parent.mkdir(parents=True, exist_ok=True)
        if sample.gold_path.exists():
            continue
        annotation = GoldAnnotation(
            version=1,
            sample_id=sample.sample_id,
            status=GoldStatus.DRAFT,
            reviewed_by=[],
            events=[],
        )
        sample.gold_path.write_text(
            json.dumps(annotation.model_dump(mode="json"), ensure_ascii=False, indent=2)
            + "\n",
            encoding="utf-8",
        )
