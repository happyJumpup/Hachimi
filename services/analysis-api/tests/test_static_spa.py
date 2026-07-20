from pathlib import Path

import httpx
import pytest

from hakimi_analysis.app import create_app


@pytest.mark.asyncio
async def test_configured_web_root_serves_spa_routes_and_built_assets(tmp_path: Path) -> None:
    web_root = tmp_path / "dist"
    assets_root = web_root / "assets"
    assets_root.mkdir(parents=True)
    (web_root / "index.html").write_text(
        '<!doctype html><div id="app">competition-web</div>',
        encoding="utf-8",
    )
    (assets_root / "app.js").write_text("globalThis.hachimi = true", encoding="utf-8")

    app = create_app(web_static_root=web_root)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="https://test"
    ) as client:
        home = await client.get("/")
        nested_route = await client.get("/result/record-1")
        asset = await client.get("/assets/app.js")

    assert home.status_code == 200
    assert "competition-web" in home.text
    assert home.headers["content-type"].startswith("text/html")
    assert nested_route.status_code == 200
    assert nested_route.text == home.text
    assert asset.status_code == 200
    assert asset.text == "globalThis.hachimi = true"
    assert "javascript" in asset.headers["content-type"]


@pytest.mark.asyncio
async def test_spa_fallback_never_masks_api_or_missing_built_assets(tmp_path: Path) -> None:
    web_root = tmp_path / "dist"
    (web_root / "assets").mkdir(parents=True)
    (web_root / "index.html").write_text("<!doctype html>spa-shell", encoding="utf-8")

    app = create_app(web_static_root=web_root)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="https://test"
    ) as client:
        health = await client.get("/api/v1/health")
        missing_api = await client.get("/api/v1/not-real")
        missing_asset = await client.get("/assets/not-real.js")

    assert health.status_code == 200
    assert health.json() == {"status": "ok"}
    assert missing_api.status_code == 404
    assert missing_api.json() == {"detail": "页面不存在"}
    assert missing_asset.status_code == 404
    assert missing_asset.json() == {"detail": "页面不存在"}


@pytest.mark.asyncio
async def test_missing_web_root_leaves_development_api_behavior_unchanged(tmp_path: Path) -> None:
    app = create_app(web_static_root=tmp_path / "missing-dist")
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="https://test"
    ) as client:
        root = await client.get("/")
        health = await client.get("/api/v1/health")

    assert root.status_code == 404
    assert root.json() == {"detail": "Not Found"}
    assert health.status_code == 200
    assert health.json() == {"status": "ok"}
