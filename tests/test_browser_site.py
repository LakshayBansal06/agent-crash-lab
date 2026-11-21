"""Real-browser checks of the public website against a live server (skipped without Playwright)."""
import socket
import threading
import time

import pytest

pw = pytest.importorskip("playwright.sync_api")
uvicorn = pytest.importorskip("uvicorn")

PAGES = ["/", "/privacy", "/contact", "/contact/thanks", "/no-such-page"]


@pytest.fixture(scope="module")
def server(tmp_path_factory):
    mp = pytest.MonkeyPatch()
    mp.setenv("CRASHLAB_AUTH", "off")
    mp.setenv("CRASHLAB_GA_ID", "G-TEST12345")
    from server.app import create_app

    app = create_app(str(tmp_path_factory.mktemp("b") / "b.db"))
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    srv = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error"))
    th = threading.Thread(target=srv.run, daemon=True)
    th.start()
    for _ in range(100):
        if srv.started:
            break
        time.sleep(0.05)
    yield f"http://127.0.0.1:{port}"
    srv.should_exit = True
    th.join(5)
    mp.undo()


@pytest.fixture(scope="module")
def browser():
    with pw.sync_playwright() as p:
        try:
            b = p.chromium.launch()
        except Exception as exc:
            pytest.skip(f"chromium unavailable: {exc}")
        yield b
        b.close()


def block_externals(page, hits):
    def handle(route):
        hits.append(route.request.url)
        route.fulfill(status=204, body="")
    page.route("**/*googletagmanager.com/**", handle)
    page.route("**/fonts.googleapis.com/**", lambda r: r.fulfill(status=200, body="", content_type="text/css"))


@pytest.mark.parametrize("width", [320, 390, 768, 1440])
def test_public_pages_fit_every_screen(server, browser, width):
    page = browser.new_page(viewport={"width": width, "height": 900})
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    block_externals(page, [])
    for p in PAGES:
        page.goto(server + p)
        assert page.evaluate("document.documentElement.scrollWidth - window.innerWidth") <= 0, (width, p)
    assert not errors
    page.close()


def test_analytics_waits_for_consent(server, browser):
    ctx = browser.new_context()
    page = ctx.new_page()
    hits = []
    block_externals(page, hits)
    page.goto(server + "/")
    page.wait_for_timeout(300)
    assert page.is_visible("#consent") and hits == []          # banner shown, nothing loaded
    page.click("#consent button[data-c='denied']")
    page.reload()
    page.wait_for_timeout(300)
    assert not page.is_visible("#consent") and hits == []      # declined: still nothing, banner remembered
    page.click("#cookie-settings")
    page.click("#consent button[data-c='granted']")
    page.wait_for_timeout(300)
    assert any("gtag/js?id=G-TEST12345" in h for h in hits)    # accepted: GA loads
    page.goto(server + "/contact/thanks")
    page.wait_for_timeout(300)
    assert page.evaluate("window.dataLayer.some(e => e[0] === 'event' && e[1] === 'generate_lead')")
    ctx.close()


def test_public_mobile_menu(server, browser):
    page = browser.new_page(viewport={"width": 390, "height": 844})
    block_externals(page, [])
    page.goto(server + "/privacy")
    assert not page.is_visible("#site-nav a[href='/contact']")
    page.click("#site-menu-btn")
    assert page.is_visible("#site-nav a[href='/contact']")
    page.click("#site-nav a[href='/contact']")
    page.wait_for_url("**/contact")
    assert page.title().startswith("Contact")
    page.close()


def test_contact_form_validation_and_error_states(server, browser):
    page = browser.new_page()
    block_externals(page, [])
    page.goto(server + "/contact")
    # 1. inline validation in the browser: nothing is sent, the field explains what's wrong
    page.fill("#name", "Asha")
    page.fill("#email", "not-an-email")
    page.fill("#message", "Too quick")
    page.click("button[type=submit]")
    assert page.url.endswith("/contact")
    assert page.get_attribute("#email", "aria-invalid") == "true"
    assert "valid email" in page.inner_text("#email-error")
    assert "9 characters" in page.inner_text("#message-error")
    assert page.evaluate("document.activeElement.id") == "email"          # focus moves to the first problem
    assert "Message not sent" in page.inner_text("#form-error")
    # 2. fixing a field clears its error as you type
    page.fill("#email", "asha@example.in")
    assert page.get_attribute("#email", "aria-invalid") is None
    # 3. the server still checks: a submission faster than a human could type is refused, input kept
    page.fill("#message", "A real question about LangGraph agents.")
    with page.expect_navigation():
        page.click("button[type=submit]")
    assert "too quickly" in page.inner_text("[role=alert]")
    assert page.input_value("#name") == "Asha"
    page.close()


def test_success_page_and_utm_attribution(server, browser):
    import server.site as site

    page = browser.new_page()
    block_externals(page, [])
    page.goto(server + "/?utm_source=newsletter&utm_campaign=launch-week&utm_medium=email")
    assert "utm_" not in page.url                                      # the address bar is tidied
    page.goto(server + "/contact")
    assert page.input_value("input[name=utm_source]") == "newsletter"
    page.fill("#name", "Ravi")
    page.fill("#email", "ravi@example.in")
    page.fill("#message", "Saw the launch post, keen to try this on our agent.")
    old = site.FormGuard.MIN_SECONDS
    site.FormGuard.MIN_SECONDS = 0
    try:
        with page.expect_navigation():
            page.click("button[type=submit]")
    finally:
        site.FormGuard.MIN_SECONDS = old
    assert page.url.endswith("/contact/thanks") and "Thanks, your message was sent" in page.inner_text("h1")
    page.close()


def test_theme_toggle_persists_and_both_themes_fit(server, browser):
    ctx = browser.new_context(viewport={"width": 390, "height": 844}, color_scheme="dark")
    page = ctx.new_page()
    block_externals(page, [])
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.goto(server + "/")
    assert page.evaluate("document.documentElement.dataset.theme") == "dark"
    dark_bg = page.evaluate("getComputedStyle(document.body).backgroundColor")
    page.click(".theme-toggle")
    assert page.evaluate("document.documentElement.dataset.theme") == "light"
    assert page.evaluate("getComputedStyle(document.body).backgroundColor") != dark_bg
    assert page.get_attribute(".theme-toggle", "aria-label") == "Switch to dark theme"
    for p in PAGES:
        page.goto(server + p)
        assert page.evaluate("document.documentElement.dataset.theme") == "light"   # remembered
        assert page.evaluate("document.documentElement.scrollWidth - window.innerWidth") <= 0
    assert not errors
    ctx.close()
    light = browser.new_context(color_scheme="light").new_page()
    block_externals(light, [])
    light.goto(server + "/")
    assert light.evaluate("document.documentElement.dataset.theme") == "light"      # follows the OS by default


def test_site_search(server, browser):
    page = browser.new_page()
    block_externals(page, [])
    page.goto(server + "/privacy")
    page.keyboard.press("/")
    page.fill(".search-dialog input", "api key")
    page.wait_for_selector(".search-dialog .results a")
    assert "Do I need an LLM API key?" in page.inner_text(".search-dialog .results a[aria-selected=true]")
    page.keyboard.press("Enter")
    page.wait_for_url("**/#faq-4")
    page.wait_for_timeout(200)
    assert page.evaluate("document.getElementById('faq-4').open")
    page.keyboard.press("Control+k")
    page.fill(".search-dialog input", "zzzz-nothing")
    assert "No results" in page.inner_text(".search-dialog .results")
    page.keyboard.press("Escape")
    assert not page.query_selector("dialog[open]")
    page.close()


def test_scrolling_aids_floating_buttons_and_copy(server, browser):
    ctx = browser.new_context(viewport={"width": 1280, "height": 700})
    ctx.grant_permissions(["clipboard-read", "clipboard-write"], origin=server)
    page = ctx.new_page()
    block_externals(page, [])
    page.goto(server + "/")
    page.click("#consent button[data-c='denied']")
    assert page.is_visible(".fab.primary[href='/contact']")              # floating contact button
    assert not page.is_visible(".fab.to-top")
    page.mouse.wheel(0, 2500)
    page.wait_for_timeout(400)
    assert page.is_visible(".fab.to-top")
    assert page.evaluate("new DOMMatrix(getComputedStyle(document.querySelector('.progress-bar')).transform).a") > 0.2
    page.click(".fab.to-top")
    for _ in range(40):  # (wait_for_function would need eval, which our CSP rightly forbids)
        if page.evaluate("window.scrollY") < 5:
            break
        page.wait_for_timeout(50)
    assert page.evaluate("window.scrollY") < 5
    page.click(".terminal .copy-btn")
    assert "crashlab demo" in page.evaluate("navigator.clipboard.readText()")
    assert page.inner_text(".terminal .copy-btn") == "Copied"
    page.click(".faq-tools .btn")
    assert page.evaluate("[...document.querySelectorAll('.faq details')].every(d => d.open)")
    page.reload()
    page.keyboard.press("Tab")
    assert page.evaluate("document.activeElement.className") == "skip"       # first Tab reaches the skip link
    page.keyboard.press("Enter")
    assert page.evaluate("document.activeElement.id") == "main"
    page.goto(server + "/contact")
    assert not page.query_selector(".fab.primary")                         # not on the contact page itself
    page.emulate_media(media="print")
    assert not page.is_visible(".site-head") and not page.is_visible(".floaters")
    ctx.close()


def test_password_toggle_and_confirm_dialog(server, browser):
    page = browser.new_page()
    block_externals(page, [])
    page.goto(server + "/app/")
    page.evaluate("""() => { const f = document.createElement('div'); f.innerHTML = '<input id="k" type="password" value="acl_secret">';
                    document.body.appendChild(f); window.crashlabUI.enhancePasswords(f); }""")
    page.click(".pw-wrap .icon-btn")
    assert page.get_attribute("#k", "type") == "text" and page.get_attribute(".pw-wrap .icon-btn", "aria-pressed") == "true"
    page.click(".pw-wrap .icon-btn")
    assert page.get_attribute("#k", "type") == "password"
    for how, expected in (("cancel", False), ("escape", False), ("confirm", True)):
        page.evaluate("() => { window.__ans = undefined; window.crashlabUI.confirm({title: 'Delete?', confirmLabel: 'Delete', danger: true}).then(a => { window.__ans = a; }); }")
        assert page.evaluate("document.activeElement.textContent") == "Cancel"   # the safe choice has focus
        if how == "cancel":
            page.click("dialog [data-a=no]")
        elif how == "escape":
            page.keyboard.press("Escape")
        else:
            page.click("dialog [data-a=yes]")
        for _ in range(40):
            if page.evaluate("window.__ans !== undefined"):
                break
            page.wait_for_timeout(25)
        assert page.evaluate("window.__ans") is expected, how
    page.close()
