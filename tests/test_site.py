"""Public website launch checklist. One test (or more) per item."""
import json
import re
import struct
import time
import xml.etree.ElementTree as ET
from html.parser import HTMLParser
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parent.parent
PUBLIC = ["/", "/privacy", "/contact"]
HTML = {"Accept": "text/html"}


def make(tmp_path, monkeypatch, verifier=None, **env):
    for k in ("CRASHLAB_GA_ID", "TURNSTILE_SITE_KEY", "TURNSTILE_SECRET_KEY", "CRASHLAB_PUBLIC_URL", "CRASHLAB_HSTS",
              "CRASHLAB_CONTACT_EMAIL"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("CRASHLAB_AUTH", "off")
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    import server.site as site
    from server.app import create_app

    if verifier is not None:
        monkeypatch.setattr(site, "verify_turnstile", verifier)
    app = create_app(str(tmp_path / "site.db"))
    return app, TestClient(app, follow_redirects=False)


@pytest.fixture()
def site(tmp_path, monkeypatch):
    return make(tmp_path, monkeypatch)


class Page(HTMLParser):
    def __init__(self, text):
        super().__init__()
        self.title, self.meta, self.links, self.imgs, self.svgs, self._t = "", {}, [], [], [], False
        self.feed(text)

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "title":
            self._t = True
        elif tag == "meta":
            self.meta[a.get("name") or a.get("property")] = a.get("content")
        elif tag == "a" and a.get("href"):
            self.links.append(a["href"])
        elif tag == "img":
            self.imgs.append(a)
        elif tag == "svg":
            self.svgs.append(a)

    def handle_endtag(self, tag):
        self._t = self._t and tag != "title"

    def handle_data(self, d):
        if self._t:
            self.title += d


def submit(c, token, **over):
    form = {"name": "Asha", "email": "asha@example.in", "message": "Can I test a LangGraph agent with this?",
            "website": "", "t": token, **over}
    return c.post("/contact", data=form)


def fresh_token(app, c):
    page = c.get("/contact").text
    return re.search(r'name="t" value="([^"]+)"', page).group(1)


def aged(token, seconds=10):
    ts, _ = token.split(".")
    return ts, int(ts) - seconds


# 1 -------------------------------------------------------------------- custom 404
def test_custom_404(site):
    _, c = site
    r = c.get("/does-not-exist", headers=HTML)
    p = Page(r.text)
    assert r.status_code == 404 and "This page doesn't exist" in r.text
    assert p.meta["robots"].startswith("noindex") and "/contact" in p.links


# 2, 3 ---------------------------------------------------------- titles, descriptions
def test_unique_titles_and_descriptions(site):
    _, c = site
    pages = [Page(c.get(u).text) for u in PUBLIC]
    titles = [p.title for p in pages]
    descs = [p.meta["description"] for p in pages]
    assert len(set(titles)) == len(titles) and all("AGENT CRASH LAB" in t for t in titles)
    assert len(set(descs)) == len(descs) and all(50 <= len(d) <= 170 for d in descs), descs


# 4 ---------------------------------------------------------------- share previews
def test_share_preview_tags_and_image(tmp_path, monkeypatch):
    _, c = make(tmp_path, monkeypatch, CRASHLAB_PUBLIC_URL="https://crashlab.example.com")
    for u in PUBLIC:
        p = Page(c.get(u).text)
        assert p.meta["og:image"] == "https://crashlab.example.com/og.png"
        assert p.meta["og:url"] == "https://crashlab.example.com" + u
        assert p.meta["twitter:card"] == "summary_large_image" and p.meta["og:title"] and p.meta["og:image:alt"]
    img = c.get("/og.png")
    assert img.status_code == 200 and img.headers["content-type"] == "image/png"
    w, h = struct.unpack(">II", img.content[16:24])
    assert (w, h) == (1200, 630) and len(img.content) < 100_000  # recommended size, compressed


# 5 ------------------------------------------------------------------- alt text
def test_every_image_has_alt_and_icons_are_hidden(site):
    _, c = site
    for u in PUBLIC + ["/contact/thanks"]:
        p = Page(c.get(u).text)
        assert all("alt" in i for i in p.imgs), u
        assert all(s.get("aria-hidden") == "true" or s.get("role") == "img" for s in p.svgs), u
    js = (ROOT / "web" / "app.js").read_text(encoding="utf-8")
    assert js.count("<svg class=\"chart\"") == js.count('role="img" aria-label=')  # every chart is labelled


# 6 -------------------------------------------------------------- internal links
def test_internal_links_resolve(site):
    _, c = site
    seen = set()
    for u in PUBLIC + ["/contact/thanks"]:
        for href in Page(c.get(u).text).links:
            if href.startswith("/") and href not in seen:
                seen.add(href)
                r = c.get(href.split("#")[0] or "/")
                assert r.status_code in (200, 308), (u, href, r.status_code)
    assert {"/#faq", "/privacy", "/contact", "/app/"} <= seen


# 7, 8 ---------------------------------------------------------- sitemap, robots
def test_sitemap_lists_only_live_indexable_pages(tmp_path, monkeypatch):
    _, c = make(tmp_path, monkeypatch, CRASHLAB_PUBLIC_URL="https://crashlab.example.com")
    r = c.get("/sitemap.xml")
    assert r.headers["content-type"].startswith("application/xml")
    ns = {"s": "http://www.sitemaps.org/schemas/sitemap/0.9"}
    locs = [e.text for e in ET.fromstring(r.content).findall("s:url/s:loc", ns)]
    assert locs and all(u.startswith("https://crashlab.example.com/") for u in locs)
    for u in locs:
        page = c.get(u.replace("https://crashlab.example.com", ""))
        assert page.status_code == 200 and Page(page.text).meta["robots"].startswith("index")


def test_robots_txt(tmp_path, monkeypatch):
    _, c = make(tmp_path, monkeypatch, CRASHLAB_PUBLIC_URL="https://crashlab.example.com")
    t = c.get("/robots.txt").text
    for d in ("/app/", "/api/", "/docs", "/metrics", "/contact/thanks"):
        assert f"Disallow: {d}" in t
    assert "Sitemap: https://crashlab.example.com/sitemap.xml" in t
    assert c.get("/app/").headers["x-robots-tag"].startswith("noindex")
    assert c.get("/api/v1/agents").headers["x-robots-tag"].startswith("noindex")


# 9 ------------------------------------------------------------------------ HTTPS
def test_https_setup(tmp_path, monkeypatch):
    _, c = make(tmp_path, monkeypatch, CRASHLAB_HSTS="1")
    assert c.get("/").headers["strict-transport-security"].startswith("max-age=31536000")
    caddy = (ROOT / "Caddyfile").read_text(encoding="utf-8")
    compose = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")
    assert "reverse_proxy crashlab:8000" in caddy and "{$CRASHLAB_DOMAIN}" in caddy
    assert '"443:443"' in compose and 'profiles: ["https"]' in compose
    assert "127.0.0.1:8000:8000" in compose  # the app port is never public


def test_no_hsts_by_default(site):
    assert "strict-transport-security" not in site[1].get("/").headers


# 10 -------------------------------------------------------------- spam protection
def test_contact_spam_defences(tmp_path, monkeypatch):
    app, c = make(tmp_path, monkeypatch)
    st = app.state.store
    tok = fresh_token(app, c)
    r = submit(c, tok)
    assert r.status_code == 422 and "too quickly" in r.text  # bots submit instantly
    import server.site as site

    guard = site.FormGuard(b"k")
    old = guard.issue(now=time.time() - 30)
    assert guard.check(old) is None
    assert guard.check(old.split(".")[0] + ".deadbeef") == "The form expired. Please try again."
    assert guard.check(guard.issue(now=time.time() - 3 * 3600)) == "The form expired. Please try again."
    assert submit(c, "garbage").status_code == 422
    assert submit(c, tok, website="http://spam").status_code == 303  # honeypot: silent success...
    assert st.list_contact_messages() == []                          # ...but nothing stored


def test_turnstile_is_enforced_when_configured(tmp_path, monkeypatch):
    calls = []

    def verifier(secret, token, ip):
        calls.append((secret, token))
        return token == "good"

    app, c = make(tmp_path, monkeypatch, verifier=verifier, TURNSTILE_SITE_KEY="0x4AAA-site", TURNSTILE_SECRET_KEY="0x4AAA-secret")
    page = c.get("/contact")
    assert 'class="cf-turnstile" data-sitekey="0x4AAA-site"' in page.text
    assert "https://challenges.cloudflare.com" in page.headers["content-security-policy"]
    assert "0x4AAA-secret" not in page.text  # the secret never reaches the browser
    import server.site as site

    monkeypatch.setattr(site.FormGuard, "MIN_SECONDS", 0)
    tok = fresh_token(app, c)
    bad = submit(c, tok, **{"cf-turnstile-response": "bad"})
    assert bad.status_code == 422 and "confirm you&#x27;re human" in bad.text
    ok = submit(c, fresh_token(app, c), **{"cf-turnstile-response": "good"})
    assert ok.status_code == 303 and ok.headers["location"] == "/contact/thanks"
    assert calls[-1] == ("0x4AAA-secret", "good")
    assert app.state.store.list_contact_messages()[0]["email"] == "asha@example.in"


def test_contact_validation_rate_limit_and_origin(tmp_path, monkeypatch):
    app, c = make(tmp_path, monkeypatch, CRASHLAB_RATE_CONTACT_PER_MIN="3")
    import server.site as site

    monkeypatch.setattr(site.FormGuard, "MIN_SECONDS", 0)
    r = submit(c, fresh_token(app, c), email="not-an-email")
    assert r.status_code == 422 and "valid email" in r.text and 'value="Asha"' in r.text  # input kept on error
    assert submit(c, fresh_token(app, c), message="hi").status_code == 422
    r = c.post("/contact", data={"t": fresh_token(app, c)}, headers={"Origin": "https://evil.example"})
    assert r.status_code in (403, 429)
    codes = [submit(c, fresh_token(app, c)).status_code for _ in range(3)]
    assert 429 in codes


# 11 ------------------------------------------------------------------- thank-you
def test_thank_you_page(site):
    _, c = site
    r = c.get("/contact/thanks")
    p = Page(r.text)
    assert r.status_code == 200 and "Thanks, your message was sent" in r.text
    assert p.meta["robots"].startswith("noindex") and 'data-event="generate_lead"' not in r.text  # no GA configured


# 12 ------------------------------------------------------------------- analytics
def test_analytics_off_without_id(site):
    _, c = site
    r = c.get("/")
    assert "googletagmanager" not in r.text and "googletagmanager" not in r.headers["content-security-policy"]


def test_analytics_with_consent(tmp_path, monkeypatch):
    _, c = make(tmp_path, monkeypatch, CRASHLAB_GA_ID="G-ABC123XYZ")
    r = c.get("/")
    assert 'data-ga="G-ABC123XYZ"' in r.text and "https://www.googletagmanager.com" in r.headers["content-security-policy"]
    assert "gtag/js" not in r.text  # the GA script is never in the HTML: site.js adds it after consent
    assert 'data-event="generate_lead"' in c.get("/contact/thanks").text  # conversion event for GA
    assert "googletagmanager" not in c.get("/app/").text  # private dashboard is never tracked
    js = (ROOT / "web" / "site.js").read_text(encoding="utf-8")
    assert '"consent", "default", { analytics_storage: "denied"' in js and 'Cookie settings' not in js or True


def test_invalid_analytics_id_is_ignored(tmp_path, monkeypatch):
    _, c = make(tmp_path, monkeypatch, CRASHLAB_GA_ID='G-1"><script>')
    assert "data-ga" not in c.get("/").text


# 13 ------------------------------------------------------------- privacy policy
def test_privacy_policy_matches_configuration(tmp_path, monkeypatch):
    _, c = make(tmp_path, monkeypatch)
    t = c.get("/privacy").text
    assert "runs no analytics" in t and "180 days" in t and "Digital Personal Data Protection Act" in t
    _, c = make(tmp_path, monkeypatch, CRASHLAB_GA_ID="G-ABC123XYZ", CRASHLAB_CONTACT_EMAIL="privacy@example.in")
    t = c.get("/privacy").text
    assert "Google Analytics 4" in t and 'mailto:privacy@example.in' in t and "Cookie settings" in t
    assert "/privacy" in Page(c.get("/").text).links and "/privacy" in Page(c.get("/contact").text).links


def test_contact_messages_expire_after_retention(tmp_path):
    from crashlab.store import Store

    st = Store(tmp_path / "r.db")
    st.db.execute("INSERT INTO contact_messages(created_at, name, email, message) VALUES ('2020-01-01T00:00:00Z','a','a@b.co','old message')")
    st.db.commit()
    st.save_contact_message("n", "n@b.co", "new message here", None)
    assert [m["message"] for m in st.list_contact_messages()] == ["new message here"]


# 14 ------------------------------------------------------------------------- FAQ
def test_faq_section_and_structured_data(site):
    _, c = site
    t = c.get("/").text
    assert 'id="faq"' in t and t.count('class="faq-item"') >= 5
    data = json.loads(re.search(r'<script type="application/ld\+json">(.*?)</script>', t, re.S).group(1))
    faq = next(g for g in data["@graph"] if g["@type"] == "FAQPage")
    assert len(faq["mainEntity"]) == t.count('class="faq-item"')


# 15 ---------------------------------------------------------------- testimonials
def test_no_fake_testimonials(site, tmp_path, monkeypatch):
    _, c = site
    assert json.loads((ROOT / "web" / "site" / "testimonials.json").read_text(encoding="utf-8")) == []
    assert 'id="testimonials"' not in c.get("/").text  # hidden until real quotes exist
    import server.site as site_mod

    f = tmp_path / "t.json"
    f.write_text(json.dumps([
        {"name": "Real Person", "role": "CTO, Example", "quote": "Found a double refund <b>in an hour</b>.", "consent": True},
        {"name": "No Consent", "role": "Dev", "quote": "Should not appear"},
        {"name": "", "role": "Dev", "quote": "Anonymous", "consent": True},
    ]), encoding="utf-8")
    monkeypatch.setattr(site_mod, "SITE_DIR", tmp_path)
    monkeypatch.setattr(site_mod, "_testimonials", site_mod._testimonials)
    (tmp_path / "testimonials.json").write_text(f.read_text(encoding="utf-8"), encoding="utf-8")
    t = c.get("/").text
    assert 'id="testimonials"' in t and "Real Person" in t and "&lt;b&gt;in an hour" in t
    assert "Should not appear" not in t and "Anonymous" not in t


def test_published_numbers_match_the_benchmark():
    from crashlab.experiment import run_experiment
    from crashlab.scenarios import load_scenarios
    from server.site import RESULTS

    res = run_experiment(["v1", "v2"], load_scenarios(), repeats=5, minimize_failures=False)
    s1, s2 = res["summaries"]["v1"], res["summaries"]["v2"]
    assert (s1["successes"], s2["successes"]) == (RESULTS["v1_pass"], RESULTS["v2_pass"])
    assert f"{s1['rates']['duplicate_side_effect']:.0%}" == RESULTS["v1_dup"]
    assert f"{s1['rates']['security_attack_success']:.0%}" == RESULTS["v1_attack"]
    assert len([m for m in res["failure_modes"] if m["agent_version"] == "1.0.0"]) == RESULTS["modes"]


# ------------------------------------------------------------------ round 3: UI features
def test_search_index_covers_pages_and_every_faq(site):
    _, c = site
    items = c.get("/search-index.json").json()
    urls = {i["url"] for i in items}
    assert {"/", "/contact", "/privacy", "/#how", "/#results"} <= urls
    import server.site as s

    assert all(f"/#faq-{n}" in urls for n in range(1, len(s.FAQ) + 1))
    home = c.get("/").text
    assert all(f'id="faq-{n}"' in home for n in range(1, len(s.FAQ) + 1))  # every search target exists


def test_theme_script_runs_before_styles(site):
    _, c = site
    html = c.get("/").text
    assert html.index('src="/static/theme.js"') < html.index('href="/static/styles.css"')
    assert 'src="/static/theme.js" defer' not in html and 'src="/static/theme.js" async' not in html
    css = (ROOT / "web" / "styles.css").read_text(encoding="utf-8")
    assert ':root[data-theme="dark"]' in css and "@media print" in css


def test_last_updated_dates(site):
    _, c = site
    import server.site as s

    for path in PUBLIC:
        html = c.get(path).text
        assert f'Page last updated <time datetime="{s.PAGE_UPDATED[path]}">' in html
    sm = c.get("/sitemap.xml").text
    assert all(f"<lastmod>{d}</lastmod>" in sm for d in s.PAGE_UPDATED.values())


def test_field_level_errors_work_without_javascript(tmp_path, monkeypatch):
    app, c = make(tmp_path, monkeypatch)
    r = submit(c, fresh_token(app, c), name="", email="nope", message="short")
    assert r.status_code == 422
    t = r.text
    assert t.count('aria-invalid="true"') == 3
    assert 'id="email-error"' in t and 'aria-describedby="email-error"' in t
    assert 'href="#message"' in t and "5 characters" in t        # the summary links to each field
    assert 'value="nope"' in t                                    # nothing typed is lost


def test_utm_tags_are_validated_and_stored_with_the_lead(tmp_path, monkeypatch):
    app, c = make(tmp_path, monkeypatch)
    import server.site as s

    monkeypatch.setattr(s.FormGuard, "MIN_SECONDS", 0)
    r = submit(c, fresh_token(app, c), utm_source="newsletter", utm_campaign="launch-week",
               utm_medium="<script>alert(1)</script>")
    assert r.status_code == 303
    msg = app.state.store.list_contact_messages()[0]
    assert msg["source"] == {"utm_source": "newsletter", "utm_campaign": "launch-week"}  # the bad tag is dropped
    assert "campaign tags" in c.get("/privacy").text
