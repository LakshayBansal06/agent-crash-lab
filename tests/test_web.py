"""Web serving: compression, favicon, 404 page, API docs, page metadata."""
import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

WEB = Path(__file__).resolve().parent.parent / "web"


@pytest.fixture()
def c(tmp_path, monkeypatch):
    monkeypatch.setenv("CRASHLAB_AUTH", "off")
    from server.app import create_app

    return TestClient(create_app(str(tmp_path / "w.db")))


def test_dashboard_and_assets_are_compressed(c):
    r = c.get("/static/app.js", headers={"Accept-Encoding": "gzip"})
    assert r.status_code == 200 and r.headers["content-encoding"] == "gzip"
    r = c.get("/api/v1/scenarios", headers={"Accept-Encoding": "gzip"})
    assert r.headers["content-encoding"] == "gzip"


def test_favicon(c):
    r = c.get("/favicon.ico")
    assert r.status_code == 200 and r.headers["content-type"].startswith("image/svg+xml")


def test_custom_404_page_for_browsers_json_for_api(c):
    r = c.get("/no/such/page", headers={"Accept": "text/html"})
    assert r.status_code == 404 and "This page doesn't exist" in r.text and "<title>Page not found" in r.text
    r = c.get("/api/v1/runs/nope", headers={"Accept": "text/html"})
    assert r.status_code == 404 and r.json()["detail"]


def test_api_reference_loads(c):
    r = c.get("/docs")
    assert r.status_code == 200 and "cdn.jsdelivr.net" in r.headers["content-security-policy"]
    assert c.get("/openapi.json").status_code == 200


def test_pages_have_metadata(c):
    pages = [(WEB / "index.html").read_text()] + [c.get(p, headers={"Accept": "text/html"}).text for p in ("/", "/privacy", "/contact", "/nope")]
    for html in pages:
        assert re.search(r'<meta name="description" content="[^"]{30,}">', html), html[:200]
        assert '<meta name="viewport"' in html and "<title>" in html and 'rel="icon"' in html


def test_every_view_sets_a_title():
    js = (WEB / "app.js").read_text()
    views = set(re.findall(r"VIEWS\.(\w+) = ", js)) - {"notfound"}
    titled = set(re.findall(r"^\s{4}(\w+): \[\"", js, re.M))
    assert views <= titled, views - titled
