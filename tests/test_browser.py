"""Real-browser checks of the dashboard (skipped when Playwright isn't installed).

Catches what unit tests can't: JavaScript that fails at startup, pages that
scroll sideways on phones, broken in-app links and a missing mobile menu."""
import json

import pytest

pw = pytest.importorskip("playwright.sync_api")

ROUTES = ["#/overview", "#/experiments", "#/experiments/new", "#/datasets", "#/datasets/ACL-ECOM-0008", "#/live",
          "#/failures", "#/traces", "#/results", "#/regressions", "#/integrations"]


@pytest.fixture(scope="module")
def report(tmp_path_factory):
    from crashlab.export import export_static

    return export_static(tmp_path_factory.mktemp("r") / "index.html", repeats=2)


@pytest.fixture(scope="module")
def browser():
    with pw.sync_playwright() as p:
        try:
            b = p.chromium.launch()
        except Exception as exc:  # browsers not installed
            pytest.skip(f"chromium unavailable: {exc}")
        yield b
        b.close()


@pytest.mark.parametrize("width", [320, 390, 768, 1440])
def test_no_errors_no_sideways_scroll(browser, report, width):
    page = browser.new_page(viewport={"width": width, "height": 900})
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.goto(report.as_uri() + "#/overview")
    page.wait_for_timeout(500)
    titles = set()
    for r in ROUTES:
        page.evaluate(f"location.hash = {json.dumps(r)}")
        page.wait_for_timeout(250)
        assert page.evaluate("document.documentElement.scrollWidth - window.innerWidth") <= 0, (width, r)
        assert not page.query_selector(".notfound"), r
        titles.add(page.title())
    assert not errors
    assert len(titles) >= 9  # each section has its own title
    page.close()


def test_links_resolve_and_unknown_routes_show_404(browser, report):
    page = browser.new_page(viewport={"width": 1280, "height": 900})
    page.goto(report.as_uri() + "#/overview")
    page.wait_for_timeout(500)
    hrefs = set()
    for r in ROUTES:
        page.evaluate(f"location.hash = {json.dumps(r)}")
        page.wait_for_timeout(200)
        hrefs |= set(page.evaluate("[...document.querySelectorAll('a[href^=\"#/\"]')].map(a => a.getAttribute('href'))"))
    for h in sorted(hrefs)[:80]:
        page.evaluate(f"location.hash = {json.dumps(h)}")
        page.wait_for_timeout(150)
        assert not page.query_selector(".notfound"), f"broken link {h}"
    page.evaluate("location.hash = '#/definitely-not-a-page'")
    page.wait_for_timeout(200)
    assert page.query_selector(".notfound") and page.title().startswith("Page not found")
    page.close()


def test_mobile_menu_and_clickable_logo(browser, report):
    page = browser.new_page(viewport={"width": 390, "height": 844})
    page.goto(report.as_uri() + "#/results")
    page.wait_for_timeout(500)
    offscreen = "document.querySelector('#side').getBoundingClientRect().right <= 0"
    assert page.evaluate(offscreen)  # menu is closed and off-canvas
    page.click("#menu-btn")
    page.wait_for_timeout(300)
    assert not page.evaluate(offscreen)
    assert page.get_attribute("#menu-btn", "aria-expanded") == "true"
    page.click("#nav a[href='#/failures']")
    page.wait_for_timeout(200)
    assert page.get_attribute("#menu-btn", "aria-expanded") == "false" and page.title().startswith("Failures")
    page.click(".top-brand")
    page.wait_for_timeout(200)
    assert page.evaluate("location.hash") == "#/overview"
    footer = page.inner_text("#foot")
    from datetime import date

    assert f"© {date.today().year}" in footer
    page.close()


def test_dashboard_theme_search_and_print(browser, report):
    page = browser.new_page(viewport={"width": 1280, "height": 900})
    page.goto(report.as_uri() + "#/overview")
    page.wait_for_timeout(500)
    before = page.evaluate("document.documentElement.dataset.theme")
    page.click(".theme-toggle")
    after = page.evaluate("document.documentElement.dataset.theme")
    assert {before, after} == {"light", "dark"}
    page.keyboard.press("/")
    page.fill(".search-dialog input", "0017")
    page.wait_for_selector(".search-dialog .results a")
    page.keyboard.press("Enter")
    page.wait_for_timeout(300)
    assert page.evaluate("location.hash") == "#/datasets/ACL-ECOM-0017"
    assert page.query_selector(".copy-wrap .copy-btn")  # scenario JSON is copyable
    first = page.evaluate("document.querySelector('a[href], button, input, select, textarea, [tabindex]:not([tabindex=\"-1\"])').className")
    assert first == "skip"                      # first stop in the tab order
    page.focus("#nav a")                        # e.g. a keyboard user sitting in the sidebar...
    page.focus("a.skip")
    page.keyboard.press("Enter")                # ...jumps straight to the content
    assert page.evaluate("document.activeElement.id") == "page" and page.evaluate("location.hash") == "#/datasets/ACL-ECOM-0017"
    page.emulate_media(media="print")
    assert not page.is_visible(".side") and not page.is_visible(".topbar")
    page.close()
