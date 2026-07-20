import json

from hakimi_analysis.app import create_app
from hakimi_analysis.bootstrap import UnconfiguredPipeline
from hakimi_analysis.settings import PROJECT_ROOT
from hakimi_analysis.sources import EmptySourceCatalog


def main() -> None:
    app = create_app(catalog=EmptySourceCatalog(), pipeline=UnconfiguredPipeline())
    output = PROJECT_ROOT / "contracts" / "openapi.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(app.openapi(), ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
