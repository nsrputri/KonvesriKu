"""Ikon situs: hanya file di daftar tetap yang disajikan, halaman menautkannya, manifest bernama."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

IKON = Path(__file__).resolve().parents[1] / "ikon"
FILES = {
    "/favicon.ico": "image/x-icon",
    "/favicon-16x16.png": "image/png",
    "/favicon-32x32.png": "image/png",
    "/apple-touch-icon.png": "image/png",
    "/android-chrome-192x192.png": "image/png",
    "/android-chrome-512x512.png": "image/png",
    "/site.webmanifest": "application/manifest+json",
}


@pytest.mark.parametrize("path", FILES)
def test_icon_served_with_type_and_cache(client, path):
    response = client.get(path)
    assert response.status_code == 200, path
    assert response.headers["content-type"].startswith(FILES[path])
    assert response.headers["x-content-type-options"] == "nosniff"
    assert "max-age=" in response.headers["cache-control"]
    assert response.content == (IKON / path.lstrip("/")).read_bytes()


def test_page_links_icons(client):
    html = client.get("/").text
    assert 'href="data:,"' not in html
    for path in ("/favicon.ico", "/favicon-32x32.png", "/favicon-16x16.png", "/apple-touch-icon.png",
                 "/site.webmanifest"):
        assert f'href="{path}"' in html, path


def test_manifest_names_app_and_icons_exist(client):
    manifest = client.get("/site.webmanifest").json()
    assert manifest["name"] == manifest["short_name"] == "KonversiKu"
    for icon in manifest["icons"]:
        assert client.get(icon["src"]).status_code == 200, icon["src"]


@pytest.mark.parametrize("path", ["/ikon/favicon.ico", "/app.py", "/index.html", "/favicon.png",
                                  "/ikon/../app.py", "/konversiku/limits.py"])
def test_only_listed_icon_paths_are_served(client, path):
    assert client.get(path).status_code == 404, path


def test_manifest_is_valid_json_file():
    assert json.loads((IKON / "site.webmanifest").read_text(encoding="utf-8"))["display"] == "standalone"
