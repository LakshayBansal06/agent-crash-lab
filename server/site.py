"""Public website: landing page, privacy policy, contact form, thank-you page,
404, sitemap.xml and robots.txt.

The dashboard (/app) and the API (/api) are private and never indexed. These
pages are the public face: server-rendered HTML (fast, crawlable, no JS needed
to read them), each with its own title, description, canonical URL and share
preview.

Optional integrations are configured with environment variables and switch
themselves off when unset, so the site never ships placeholder IDs:

  CRASHLAB_PUBLIC_URL      https://your-domain  (canonical URLs, sitemap, share previews)
  CRASHLAB_GA_ID           G-XXXXXXXXXX         (Google Analytics 4, loaded only after consent)
  TURNSTILE_SITE_KEY       Cloudflare Turnstile widget key  } contact-form spam protection
  TURNSTILE_SECRET_KEY     Cloudflare Turnstile secret      }
  CRASHLAB_CONTACT_EMAIL   address shown in the privacy policy
  CRASHLAB_OPERATOR        who runs this site (named in the privacy policy)
"""
from __future__ import annotations

import datetime as dt
import hashlib
import hmac
import html
import json
import os
import re
import secrets
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, PlainTextResponse, RedirectResponse, Response

from .observability import log
from .security import SESSION_COOKIE, client_ip, public_url

WEB_DIR = Path(__file__).resolve().parent.parent / "web"
SITE_DIR = WEB_DIR / "site"
GA_RE = re.compile(r"^G-[A-Z0-9]{4,16}$")
EMAIL_RE = re.compile(r"^[^@\s<>\"']{1,64}@[^@\s<>\"']{1,190}\.[A-Za-z]{2,24}$")
POLICY_UPDATED = "2026-11-17"
# When each page's content last changed. Shown in the footer and used as <lastmod> in sitemap.xml.
PAGE_UPDATED = {"/": "2026-11-17", "/contact": "2026-11-17", "/privacy": POLICY_UPDATED}
UTM_KEYS = ("utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content")
UTM_RE = re.compile(r"^[\w .+\-%/]{1,100}$")

# Real numbers from `crashlab run --agents v1 v2 --repeats 5` (seeded, reproducible).
# tests/test_site.py re-runs the benchmark and fails if these ever drift.
RESULTS = {"episodes": 200, "v1_pass": 19, "v2_pass": 99, "v1_dup": "15%", "v2_dup": "0%",
           "v1_attack": "75%", "v2_attack": "0%", "modes": 13, "repro_from": 8, "repro_to": 2}

FAQ: list[tuple[str, str]] = [
    ("What does AGENT CRASH LAB test?",
     "Tool-using AI agents doing multi-step tasks. It runs your agent inside a simulated business system, injects "
     "realistic faults (timeouts, rate limits, stale data, prompt injection, race conditions) and checks the "
     "resulting system state, not just the agent's reply."),
    ("How is this different from an LLM evaluation dashboard?",
     "Evaluation dashboards score answers. Crash Lab changes the world while the agent works and then proves what "
     "happened in the database: a refund issued twice, an order modified without permission, a success claimed that "
     "never happened. Each failure is minimized to a short reproduction and turned into a regression test."),
    ("Does it work with my agent framework?",
     "Yes, as long as your agent can call Python functions. The SDK wraps any agent function, and a built-in "
     "tool-calling agent can test Claude models directly. Adapters for specific frameworks are on the roadmap."),
    ("Do I need an LLM API key?",
     "No. The built-in reference agents run offline and free. A Claude or Gemini key is only needed to test a real model, "
     "and it stays on the server."),
    ("Is my data sent anywhere?",
     "No. Everything runs on your own machine or server. The public website uses Google Analytics only if you accept "
     "cookies, and contact-form messages are stored on this server only."),
    ("Are the results reproducible?",
     "Yes. Every fault, agent choice and timestamp is driven by a seed, so the same seed replays the identical trace. "
     "Reliability is reported over repeated trials with confidence intervals and pass^k."),
    ("What does it cost?",
     "Free to start, and free to run on your own machine. Hosted plans are Pro at ₹2,499 and Team at ₹9,999 a month "
     "once billing opens; during the beta every account gets every feature. Testing a real model (Claude or Gemini) "
     "also costs whatever your model provider charges."),
]


def _env(name: str) -> str:
    return os.environ.get(name, "").strip()


def ga_id() -> str | None:
    v = _env("CRASHLAB_GA_ID")
    return v if GA_RE.match(v) else None


def turnstile() -> tuple[str, str] | None:
    site, secret = _env("TURNSTILE_SITE_KEY"), _env("TURNSTILE_SECRET_KEY")
    return (site, secret) if site and secret else None


def base_url(request: Request) -> str:
    return public_url() or str(request.base_url).rstrip("/")


def site_csp(*, analytics: bool, captcha: bool) -> str:
    script = ["'self'"]
    connect, img, frame = ["'self'"], ["'self'", "data:"], ["'none'"]
    if analytics:
        script.append("https://www.googletagmanager.com")
        connect += ["https://*.google-analytics.com", "https://*.analytics.google.com", "https://www.googletagmanager.com"]
        img += ["https://*.google-analytics.com", "https://www.googletagmanager.com"]
    if captcha:
        script.append("https://challenges.cloudflare.com")
        frame = ["https://challenges.cloudflare.com"]
    return (f"default-src 'self'; script-src {' '.join(script)}; style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
            f"font-src https://fonts.gstatic.com; img-src {' '.join(img)}; connect-src {' '.join(connect)}; "
            f"frame-src {' '.join(frame)}; object-src 'none'; base-uri 'none'; form-action 'self'; frame-ancestors 'none'")


# The crash-test calibration target, drawn in ink so it follows the theme (red is reserved for findings).
LOGO = ('<svg viewBox="0 0 26 26" aria-hidden="true" focusable="false"><circle cx="13" cy="13" r="12" fill="none" '
        'stroke="currentColor" stroke-width="1.5"/><path d="M13 1a12 12 0 0 1 12 12H13zM13 25A12 12 0 0 1 1 13h12z" '
        'fill="currentColor"/></svg>')
FONTS = ("https://fonts.googleapis.com/css2?family=Archivo:wdth,wght@62..125,400..800"
         "&family=Public+Sans:wght@400..700&family=JetBrains+Mono:wght@400;600&display=swap")


def human_date(iso: str) -> str:
    d = dt.date.fromisoformat(iso)
    return f"{d.day} {d.strftime('%B %Y')}"


def layout(request: Request, *, path: str, title: str, description: str, body: str, indexable: bool = True,
           captcha: bool = False, event: str | None = None) -> str:
    e = html.escape
    base = base_url(request)
    canonical = base + path
    ga = ga_id()
    year = dt.date.today().year
    full_title = title if title.startswith("AGENT CRASH LAB") else f"{title} · AGENT CRASH LAB"
    upd = PAGE_UPDATED.get(path)
    updated = (f' · <span class="updated">Page last updated <time datetime="{upd}">{human_date(upd)}</time></span>'
               if upd and indexable else "")
    nav = [("/#demo", "Demo"), ("/#how", "How it works"), ("/#pricing", "Pricing"), ("/#faq", "FAQ"), ("/contact", "Contact")]
    current = ' aria-current="page"'
    nav_html = "".join(f'<a href="{h}"{current if h == path else ""}>{t}</a>' for h, t in nav)
    user = request.app.state.store.session_user(request.cookies.get(SESSION_COOKIE))
    if user:
        nav_html += ('<a href="/account" class="nav-account">Account</a>'
                     + ('<a href="/admin">Admin</a>' if user["role"] == "admin" else "")
                     + '<a class="btn primary" href="/app/">Open dashboard</a>')
    else:
        nav_html += ('<a href="/login"' + (current if path == "/login" else "") + '>Sign in</a>'
                     '<a class="btn primary" href="/signup">Create free account</a>')
    attrs = f' data-ga="{e(ga)}"' if ga else ""
    attrs += f' data-event="{e(event)}"' if event and ga else ""
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{e(full_title)}</title>
<meta name="description" content="{e(description)}">
<meta name="robots" content="{"index, follow" if indexable else "noindex, nofollow"}">
<link rel="canonical" href="{e(canonical)}">
<meta name="theme-color" content="#0d1b2a">
<meta property="og:type" content="website">
<meta property="og:site_name" content="AGENT CRASH LAB">
<meta property="og:title" content="{e(full_title)}">
<meta property="og:description" content="{e(description)}">
<meta property="og:url" content="{e(canonical)}">
<meta property="og:image" content="{e(base)}/og.png">
<meta property="og:image:width" content="1200">
<meta property="og:image:height" content="630">
<meta property="og:image:alt" content="AGENT CRASH LAB: the fixed agent passed 99 of 100 crash tests, the original passed 19.">
<meta name="twitter:card" content="summary_large_image">
<meta name="twitter:title" content="{e(full_title)}">
<meta name="twitter:description" content="{e(description)}">
<meta name="twitter:image" content="{e(base)}/og.png">
<link rel="icon" href="/favicon.ico" type="image/svg+xml">
<script src="/static/theme.js"></script>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link rel="stylesheet" href="{FONTS}">
<link rel="stylesheet" href="/static/styles.css">
<link rel="stylesheet" href="/static/site.css">
{'<script src="https://challenges.cloudflare.com/turnstile/v0/api.js" async defer></script>' if captcha else ''}
<script src="/static/ui.js" defer></script>
<script src="/static/site.js" defer></script>
</head>
<body class="site"{attrs}>
<a class="skip" href="#main">Skip to content</a>
<header class="site-head">
  <a class="site-brand" href="/" aria-label="AGENT CRASH LAB home">{LOGO}<span>AGENT CRASH LAB</span></a>
  <nav class="site-nav" id="site-nav" aria-label="Main">{nav_html}</nav>
  <div class="head-tools" id="head-tools"></div>
  <button class="menu-btn site-menu-btn" id="site-menu-btn" aria-controls="site-nav" aria-expanded="false" aria-label="Open menu"><svg viewBox="0 0 20 20" aria-hidden="true"><path d="M3 5h14M3 10h14M3 15h14" stroke="currentColor" stroke-width="2" stroke-linecap="round"/></svg></button>
</header>
<main id="main">{body}</main>
<footer class="site-foot">
  <div><a class="site-brand" href="/" aria-label="AGENT CRASH LAB home">{LOGO}<span>AGENT CRASH LAB</span></a>
  <p>Find AI-agent failures before your customers do.</p></div>
  <nav aria-label="Footer"><a href="/">Home</a><a href="/#demo">Demo</a><a href="/#how">How it works</a><a href="/#results">Results</a><a href="/#pricing">Pricing</a><a href="/#faq">FAQ</a><a href="/contact">Contact</a><a href="/privacy">Privacy policy</a><a href="/signup">Create account</a><a href="/login">Sign in</a><a href="/app/">Dashboard</a><a href="/docs">API reference</a>{'<button type="button" class="linkish" id="cookie-settings">Cookie settings</button>' if ga else ''}</nav>
  <p class="copy">© {year} AGENT CRASH LAB{updated}</p>
</footer>
</body>
</html>"""


def _testimonials() -> list[dict[str, str]]:
    """Only real, consented quotes: entries must name the person, their role and carry consent=true.
    The section is hidden entirely while the file is empty."""
    f = SITE_DIR / "testimonials.json"
    try:
        items = json.loads(f.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    ok = []
    for t in items if isinstance(items, list) else []:
        if (isinstance(t, dict) and t.get("consent") is True and all(isinstance(t.get(k), str) and t[k].strip()
                                                                       for k in ("name", "role", "quote"))):
            ok.append(t)
        else:
            log("testimonial_skipped", reason="missing name/role/quote or consent=true")
    return ok


# The hero's sequence of events: the real trace of support-agent 1.0.0 on ACL-ECOM-0002, seed 1
# (`crashlab trace`), on the virtual clock. tests/test_site.py replays it so this can't drift.
HERO_TRACE: list[tuple[str, str, str, str]] = [
    ("0.70", "", "Searches the refund policy", "search_kb"),
    ("1.68", "", "Reads the order: ₹4,999, paid", "get_order"),
    ("2.51", "", "Checks eligibility: eligible", "check_refund_eligibility"),
    ("3.28", "", "Issues the refund. The gateway commits it…", "issue_refund"),
    ("15.93", "fault", "<b>Injected fault:</b> …and then times out", "timeout_after_commit"),
    ("16.72", "fault", "Retries without checking the refund status", "issue_refund"),
    ("17.37", "fault", "<b>Invariant broken:</b> refund_count(ORD-1001) ≤ 1", "₹9,998 refunded"),
    ("18.06", "", "Tells the customer: “Your refund of ₹4,999 has been processed.”", "reply"),
]

# Pricing the operator confirmed. Paid plans are shown but take no payment yet (no billing is connected).
PLANS = [
    {"name": "Free", "price": "₹0", "per": "forever", "for": "Individual developers",
     "cta": ("Create free account", "/signup", True)},
    {"name": "Pro", "price": "₹2,499", "per": "per month", "for": "Small AI teams",
     "cta": ("Start free", "/signup", False)},
    {"name": "Team", "price": "₹9,999", "per": "per month", "for": "Agencies and growing startups",
     "cta": ("Start free", "/signup", False)},
    {"name": "Enterprise", "price": "Custom", "per": "annual contract", "for": "Private deployments",
     "cta": ("Talk to us", "/contact", False)},
]
# Plan contents exactly as the product blueprint defines them (section 15.1); no invented limits.
PLAN_ROWS: list[tuple[str, tuple[str, str, str, str]]] = [
    ("Projects", ("One", "More projects", "More projects", "Private deployment")),
    ("Monthly episodes", ("Limited", "Larger quota", "Higher limits", "Dedicated infrastructure")),
    ("Environments and evaluations", ("Basic", "Basic", "Basic + shared datasets", "Custom environments")),
    ("Fault injection, minimization, regression tests", ("Included in beta", "Included", "Included", "Included")),
    ("CI integration and advanced reports", ("Included in beta", "Included", "Included", "Included")),
    ("Team collaboration and webhooks", ("—", "—", "Included", "Included")),
    ("SSO, audit controls, retention policies", ("—", "—", "—", "Included")),
    ("Support", ("Community", "Community", "Priority", "Contractual")),
]

DEMO_VIDEO = WEB_DIR / "media" / "demo.webm"


def demo_section() -> str:
    """Exhibit A: the recorded walkthrough. Omitted entirely until the video file exists."""
    if not DEMO_VIDEO.exists():
        return ""
    poster = ' poster="/static/media/demo-poster.png"' if (WEB_DIR / "media" / "demo-poster.png").exists() else ""
    return f"""
<section class="site-section ledger" id="demo" aria-labelledby="demo-title">
  <div>
    <h2 id="demo-title">Watch the whole investigation</h2>
    <p class="sub">Recorded from the real app, start to finish: create an account, run 200 crash tests, open a failure, shrink it to two steps, save the regression test, and sign in to the admin console.</p>
    <figure class="video-frame"><figcaption class="exhibit-tag">Exhibit A · recorded from the real app</figcaption><video controls preload="metadata" playsinline{poster} aria-describedby="demo-title">
      <source src="/static/media/demo.webm" type="video/webm">
      <a href="/static/media/demo.webm">Download the demo video</a></video></figure>
    <div class="btn-row after-video"><a class="btn primary lg" href="/signup">Create free account and try it</a></div>
  </div>
</section>"""


def landing_body() -> str:
    e, r = html.escape, RESULTS
    faq = "".join(f'<details class="faq-item" id="faq-{i}"><summary>{e(q)}</summary><p>{e(a)}</p></details>'
                  for i, (q, a) in enumerate(FAQ, 1))
    quotes = _testimonials()
    testimonials = ""
    if quotes:
        def card(t: dict[str, str]) -> str:
            src = t.get("url", "")
            link = f' · <a href="{e(src)}" rel="noopener">source</a>' if src.startswith("https://") else ""
            return (f'<figure class="quote"><blockquote>“{e(t["quote"])}”</blockquote>'
                    f'<figcaption><b>{e(t["name"])}</b> · {e(t["role"])}{link}</figcaption></figure>')

        cards = "".join(card(t) for t in quotes)
        testimonials = (f'<section class="site-section ledger" id="testimonials">'
                        f'<div><h2>What developers say</h2><div class="quotes">{cards}</div></div></section>')
    steps = [("Stress", "Your agent works inside a simulated business while faults fire: gateway timeouts, rate limits, stale reads, prompt injection, concurrent writes.", "Seeded, replayable episodes"),
             ("Detect", "Six evaluators read the real system state, policies, security and the agent's own claims. A polite reply can't hide a double refund.", "Pass / fail with evidence"),
             ("Explain", "Every failure gets a category, its evidence events and a root-cause hypothesis anchored at the earliest divergence.", "Probable cause, with confidence"),
             ("Minimize", "Delta debugging shrinks a long failing run to the few steps that still reproduce it, with the faults pinned in place.", f"{r['repro_from']} steps → {r['repro_to']}"),
             ("Prevent", "The failure becomes a versioned regression test that re-runs the real agent before every release.", "A test in your suite"),
             ("Compare", "Versions are compared on identical seeds with confidence intervals, pass^k and a paired significance test.", "A release decision")]
    how = "".join(f'<li><b>{e(t)}</b><span>{e(d)}</span><em>{e(o)}</em></li>' for t, d, o in steps)
    rail = "".join(f'<li class="{k}" style="--i:{n}"><time>T+{t}s</time><span>{d}</span><code>{e(c)}</code></li>'
                   for n, (t, k, d, c) in enumerate(HERO_TRACE))
    heads = "".join(f'<th scope="col"><b>{e(p["name"])}</b><span class="for">{e(p["for"])}</span></th>' for p in PLANS)
    prices = "".join(f'<td><span class="price">{e(p["price"])}</span><span class="per">{e(p["per"])}</span></td>' for p in PLANS)
    rows = "".join(f'<tr><th scope="row">{e(label)}</th>' + "".join(f"<td>{e(v)}</td>" for v in vals) + "</tr>"
                   for label, vals in PLAN_ROWS)
    ctas = "".join(f'<td><a class="btn{" primary" if primary else ""}" href="{href}">{e(text)}</a></td>'
                   for text, href, primary in (p["cta"] for p in PLANS))
    # Phones get one plan per block; the comparison table is for wider screens.
    stacked = "".join(
        f'<article class="plan"><header><h3>{e(p["name"])}</h3><span class="for">{e(p["for"])}</span></header>'
        f'<p class="plan-price"><span class="price">{e(p["price"])}</span> <span class="per">{e(p["per"])}</span></p>'
        '<dl>' + "".join(f'<div><dt>{e(label)}</dt><dd>{e(vals[i])}</dd></div>' for label, vals in PLAN_ROWS) + '</dl>'
        f'<a class="btn{" primary" if p["cta"][2] else ""} block" href="{p["cta"][1]}">{e(p["cta"][0])}</a></article>'
        for i, p in enumerate(PLANS))
    jsonld = json.dumps({"@context": "https://schema.org", "@graph": [
        {"@type": "SoftwareApplication", "name": "AGENT CRASH LAB", "applicationCategory": "DeveloperApplication",
         "operatingSystem": "Web, Windows, macOS, Linux", "offers": {"@type": "Offer", "price": "0", "priceCurrency": "INR"},
         "description": "Reliability testing platform for AI agents: fault injection, root-cause diagnosis, failure minimization and regression tests."},
        {"@type": "FAQPage", "mainEntity": [{"@type": "Question", "name": q, "acceptedAnswer": {"@type": "Answer", "text": a}}
                                            for q, a in FAQ]}]}).replace("</", "<\\/")
    return f"""
<section class="docket" aria-labelledby="hero-title">
  <div class="docket-strip"><span>Docket <b class="id">ACL-ECOM-0002</b></span><span>Investigation report</span><span>Agent <b class="id">support-agent 1.0.0</b></span><span class="closed">Closed · regression saved</span></div>
  <div class="docket-grid">
    <div class="docket-main">
      <h1 id="hero-title">Your AI agent passed the demo. Will it survive production?</h1>
      <p class="lede">AGENT CRASH LAB makes real-world failures happen on purpose while your agent works, checks what actually changed in the system of record, and turns every crash into a short reproduction and a regression test.</p>
      <div class="probable-cause" role="note" aria-labelledby="pc-title">
        <h2 id="pc-title">Probable cause</h2>
        <p>The payment gateway committed the refund, then timed out. The agent retried <code>issue_refund</code> without checking the refund status, so order ORD-1001 was refunded twice while the customer was told it had been refunded once.</p>
      </div>
      <div class="btn-row hero-actions"><a class="btn primary lg" href="/signup">Create free account</a><a class="btn lg" href="#demo">Watch the demo</a></div>
      <p class="fine">Free to start. No card needed.</p>
    </div>
    <figure class="rail" aria-labelledby="rail-cap">
      <figcaption id="rail-cap"><b>Sequence of events</b><span>Recorded trace · virtual clock</span></figcaption>
      <ol class="rail-list">{rail}</ol>
      <div class="stamp" role="img" aria-label="Finding: duplicate refund"><span>Finding</span>Duplicate refund</div>
      <div class="rail-controls" hidden><button type="button" class="btn small" id="rail-replay">Replay the recording</button>
        <label class="scrub"><span>Scrub</span><input type="range" id="rail-scrub" min="0" max="{len(HERO_TRACE)}" value="{len(HERO_TRACE)}" step="1"></label></div>
    </figure>
  </div>
</section>
{demo_section()}
<section class="site-section ledger" id="how" aria-labelledby="how-title">
  
  <div>
    <h2 id="how-title">How every investigation runs</h2>
    <p class="sub">One loop, from a stressed agent to a protected release. Each stage leaves evidence you can open.</p>
    <ol class="procedure">{how}</ol>
    <div class="run-local"><p>Prefer your own machine? With Python 3.11+ in the project folder:</p>
      <pre class="code"><code>pip install -e ".[server]"
crashlab demo</code></pre></div>
  </div>
</section>
<section class="site-section ledger" id="results" aria-labelledby="results-title">
  
  <div>
    <h2 id="results-title">Measured, not claimed</h2>
    <p class="sub">The included benchmark, measured <time datetime="{PAGE_UPDATED['/']}">{human_date(PAGE_UPDATED['/'])}</time>: {r['episodes']} episodes, 20 scenarios × 5 repeats × 2 versions of a refund agent. Anyone can reproduce it with <code>crashlab run</code>.</p>
    <div class="compare" role="img" aria-label="Episodes passed: original agent {r['v1_pass']} of 100, fixed agent {r['v2_pass']} of 100">
      <div class="bar v1"><span>Original agent (v1)</span><i style="--w:{r['v1_pass']}%"></i><b>{r['v1_pass']} / 100</b></div>
      <div class="bar v2"><span>Fixed agent (v2)</span><i style="--w:{r['v2_pass']}%"></i><b>{r['v2_pass']} / 100</b></div>
    </div>
    <div class="table-wrap"><table class="table findings">
      <thead><tr><th>Metric</th><th class="num">Original agent (v1)</th><th class="num">Fixed agent (v2)</th></tr></thead>
      <tbody>
        <tr><td>Episodes passed</td><td class="num">{r['v1_pass']} / 100</td><td class="num">{r['v2_pass']} / 100</td></tr>
        <tr><td>Duplicate refunds</td><td class="num bad">{r['v1_dup']}</td><td class="num">{r['v2_dup']}</td></tr>
        <tr><td>Prompt-injection attacks that worked</td><td class="num bad">{r['v1_attack']}</td><td class="num">{r['v2_attack']}</td></tr>
      </tbody></table></div>
    <p class="sub">{r['modes']} distinct failure modes found in v1, each with evidence, a minimal reproduction and a regression test. The fixed agent still fails once in 100, which is exactly what repeated-trial testing exists to catch.</p>
  </div>
</section>
{testimonials}
<section class="site-section ledger" id="pricing" aria-labelledby="pricing-title">
  
  <div>
    <h2 id="pricing-title">Pricing</h2>
    <p class="sub">Start free. During the beta every account gets the Pro features, with nothing to pay and no card to enter; paid plans start when billing opens. Prices in Indian rupees per month, excluding GST.</p>
    <div class="table-wrap fees-wrap"><table class="table fees">
      <thead><tr><th scope="col"><span class="sr-only">Feature</span></th>{heads}</tr></thead>
      <tbody><tr class="price-row"><th scope="row">Price</th>{prices}</tr>{rows}<tr class="cta-row"><th scope="row"><span class="sr-only">Get started</span></th>{ctas}</tr></tbody>
    </table></div>
    <div class="plans-stacked">{stacked}</div>
  </div>
</section>
<section class="site-section ledger" id="faq" aria-labelledby="faq-title">
  
  <div>
    <h2 id="faq-title">Frequently asked questions</h2>
    <div class="faq">{faq}</div>
  </div>
</section>
<section class="closing" aria-labelledby="close-title">
  <h2 id="close-title">Open your first investigation</h2>
  <p>Create a free account, watch the demo, and crash-test an agent in the same sitting.</p>
  <div class="btn-row"><a class="btn primary lg" href="/signup">Create free account</a><a class="btn lg" href="/contact">Request a pilot</a></div>
</section>
<script type="application/ld+json">{jsonld}</script>
"""


def privacy_body() -> str:
    e = html.escape
    operator = _env("CRASHLAB_OPERATOR")
    run_by = f" This site is run by {e(operator)}." if operator else ""
    email = _env("CRASHLAB_CONTACT_EMAIL")
    contact = (f'email <a href="mailto:{e(email)}">{e(email)}</a> or use the <a href="/contact">contact form</a>'
               if email and EMAIL_RE.match(email) else 'use the <a href="/contact">contact form</a>')
    ga = ga_id()
    analytics = ("<p>If you click <b>Accept</b> on the cookie banner, we load Google Analytics 4 to count visits and see which pages "
                 "are useful. It sets cookies (<code>_ga</code>, <code>_ga_*</code>) and sends Google your approximate location, "
                 "device and browser type, and the pages you view. IP addresses are not stored by Google Analytics 4. If you "
                 "click <b>Decline</b>, or ignore the banner, Google Analytics never loads. You can change your choice at any "
                 "time with <b>Cookie settings</b> in the footer. Google's own policy is at "
                 '<a href="https://policies.google.com/privacy" rel="noopener">policies.google.com/privacy</a>.</p>'
                 if ga else "<p>This site currently runs no analytics and sets no tracking cookies.</p>")
    captcha = ("<p>The contact form uses Cloudflare Turnstile to block spam. Turnstile checks your browser for signs of "
               "automation and does not use cookies for advertising. See "
               '<a href="https://www.cloudflare.com/privacypolicy/" rel="noopener">Cloudflare\'s privacy policy</a>.</p>'
               if turnstile() else "")
    return f"""
<article class="prose">
  <h1>Privacy policy</h1>
  <p class="sub">Last updated {POLICY_UPDATED}.{run_by}</p>
  <h2>The short version</h2>
  <ul>
    <li>We collect what you type into the contact form, and nothing else unless you accept analytics cookies.</li>
    <li>We never sell or share your data for advertising.</li>
    <li>Agent test data you run in the dashboard stays on the server you run it on.</li>
  </ul>
  <h2>What we collect and why</h2>
  <h3>Contact form</h3>
  <p>Your name, email address and message, so we can reply. We also store a one-way hash of your IP address to stop abuse; it can't be turned back into your IP. If you arrived through a link with campaign tags (<code>utm_source</code>, <code>utm_campaign</code> and similar), we store those tags with your message so we know which announcement brought you here. Messages are deleted after 180 days.</p>
  <h3>Stored in your browser</h3>
  <p>We keep your light or dark theme choice{" and your cookie choice" if ga_id() else ""} in your browser's local storage, and campaign tags from the link you arrived through in session storage, which is cleared when you close the tab. None of this is sent anywhere unless you send the contact form{" or accept analytics" if ga_id() else ""}.</p>
  {captcha}
  <h3>Analytics</h3>
  {analytics}
  <h3>Server logs</h3>
  <p>The server records each request's time, page, status and your IP address for security and debugging. Logs are kept for at most 30 days.</p>
  <h3>Dashboard</h3>
  <p>The dashboard at <a href="/app/">/app</a> is not public. Access needs an API key, and its pages are never indexed by search engines or tracked by analytics.</p>
  <h2>Your rights</h2>
  <p>You can ask to see, correct or delete your data at any time. To do that, {contact}. We reply within 30 days. If you are in India, you have these rights under the Digital Personal Data Protection Act, 2023; in the EU or UK, under the GDPR.</p>
  <h2>Children</h2>
  <p>This site is for software developers and isn't directed at children under 18.</p>
  <h2>Changes</h2>
  <p>If we change this policy we'll update the date at the top of this page.</p>
</article>"""


FIELDS = {"name": "Your name", "email": "Email", "message": "Message"}


def contact_body(*, errors: dict[str, str] | None = None, general: str | None = None,
                 values: dict[str, str] | None = None, token: str = "", sitekey: str | None = None) -> str:
    """Server-rendered form with inline, per-field errors and an error summary that links to each
    field, so it works without JavaScript; site.js adds the same checks before submitting."""
    e, v, errors = html.escape, values or {}, errors or {}

    def field(name: str, control: str, hint: str = "") -> str:
        err = errors.get(name)
        described = " ".join(x for x in (f"{name}-hint" if hint else "", f"{name}-error" if err else "") if x)
        control = control.replace("{aria}", (' aria-invalid="true"' if err else "") +
                                  (f' aria-describedby="{described}"' if described else ""))
        return (f'<div class="field"><label for="{name}">{FIELDS[name]}</label>{control}'
                + (f'<div class="hint" id="{name}-hint">{hint}</div>' if hint else "")
                + (f'<div class="field-error" id="{name}-error">{e(err)}</div>' if err else "") + "</div>")

    summary = ""
    if errors or general:
        items = "".join(f'<li><a href="#{k}">{e(m)}</a></li>' for k, m in errors.items())
        summary = (f'<div class="error-summary" role="alert" id="form-error" tabindex="-1"><b>Message not sent.</b> '
                   f'{e(general or "Please fix the highlighted fields.")}' + (f"<ul>{items}</ul>" if items else "") + "</div>")
    widget = f'<div class="cf-turnstile" data-sitekey="{e(sitekey)}" data-theme="auto"></div>' if sitekey else ""
    utm = "".join(f'<input type="hidden" name="{k}" value="{e(v.get(k, ""))}">' for k in UTM_KEYS)
    return f"""
<article class="prose">
  <h1>Contact</h1>
  <p class="lede">Questions about testing your agent, a bug, or a pilot? Send a message and we'll reply by email.</p>
  {summary}
  <form class="form site-form" id="contact-form" method="post" action="/contact" novalidate>
    {field("name", f'<input id="name" name="name" autocomplete="name" required maxlength="100" value="{e(v.get("name", ""))}"{{aria}}>')}
    {field("email", f'<input id="email" name="email" type="email" autocomplete="email" required maxlength="254" value="{e(v.get("email", ""))}"{{aria}}>')}
    {field("message", f'<textarea id="message" name="message" required minlength="10" maxlength="5000" rows="7"{{aria}}>{e(v.get("message", ""))}</textarea>', "10–5,000 characters.")}
    <div class="hp" aria-hidden="true"><label for="website">Leave this empty</label><input id="website" name="website" tabindex="-1" autocomplete="off"></div>
    <input type="hidden" name="t" value="{e(token)}">
    {utm}
    {widget}
    <p class="sub">By sending, you agree to our <a href="/privacy">privacy policy</a>.</p>
    <div class="btn-row"><button class="btn primary" type="submit">Send message</button></div>
  </form>
</article>"""


THANKS_BODY = """
<article class="prose">
  <h1>Thanks, your message was sent</h1>
  <p class="lede">We usually reply within two working days. Check your spam folder if you don't see it.</p>
  <div class="btn-row"><a class="btn primary" href="/">Back to the home page</a><a class="btn" href="/app/">Open the dashboard</a></div>
</article>"""

NOT_FOUND_BODY = """
<article class="prose notfound">
  <div class="code" aria-hidden="true">404</div>
  <h1>This page doesn't exist</h1>
  <p class="lede">The address may be mistyped or out of date. These pages are still here:</p>
  <div class="btn-row"><a class="btn primary" href="/">Home page</a><a class="btn" href="/app/">Dashboard</a><a class="btn" href="/#faq">FAQ</a><a class="btn" href="/contact">Contact</a></div>
</article>"""


# ------------------------------------------------------------------ anti-spam
class FormGuard:
    """Signed timestamp + honeypot + optional Turnstile. Bots that post instantly, fill the hidden
    field, replay old tokens or fail the challenge are rejected."""

    MIN_SECONDS, MAX_SECONDS = 3, 2 * 3600

    def __init__(self, key: bytes | None = None) -> None:
        self.key = key or (_env("CRASHLAB_FORM_SECRET").encode() or secrets.token_bytes(32))

    def issue(self, now: float | None = None) -> str:
        ts = str(int(now or time.time()))
        return ts + "." + hmac.new(self.key, ts.encode(), hashlib.sha256).hexdigest()[:32]

    def check(self, token: str, now: float | None = None) -> str | None:
        try:
            ts, sig = token.split(".", 1)
            age = (now or time.time()) - int(ts)
        except ValueError:
            return "The form expired. Please try again."
        good = hmac.new(self.key, ts.encode(), hashlib.sha256).hexdigest()[:32]
        if not hmac.compare_digest(sig, good) or age > self.MAX_SECONDS:
            return "The form expired. Please try again."
        if age < self.MIN_SECONDS:
            return "That was sent too quickly. Please wait a moment and try again."
        return None


def verify_turnstile(secret: str, token: str, ip: str | None) -> bool:  # pragma: no cover - network
    import httpx

    try:
        r = httpx.post("https://challenges.cloudflare.com/turnstile/v0/siteverify", timeout=10,
                       data={"secret": secret, "response": token, **({"remoteip": ip} if ip else {})})
        return bool(r.json().get("success"))
    except Exception:  # noqa: BLE001 - fail closed
        return False


# --------------------------------------------------------------------- routes
def mount_site(app: FastAPI, *, verifier: Callable[[str, str, str | None], bool] | None = None) -> None:
    guard = FormGuard()
    verify = verifier or verify_turnstile
    store = app.state.store
    limiter = app.state.limiter

    def page(request: Request, *, path: str, title: str, description: str, body: str, status: int = 200,
             indexable: bool = True, captcha: bool = False, event: str | None = None) -> HTMLResponse:
        html_doc = layout(request, path=path, title=title, description=description, body=body, indexable=indexable,
                          captcha=captcha, event=event)
        headers = {"Content-Security-Policy": site_csp(analytics=bool(ga_id()), captcha=captcha),
                   # "no-referrer" makes browsers send Origin: null on form posts, which the CSRF check
                   # must reject; this standard policy sends only the origin, never the full URL, cross-site.
                   "Referrer-Policy": "strict-origin-when-cross-origin",
                   "Cache-Control": "no-cache" if captcha or not indexable else "public, max-age=300"}
        if not indexable:
            headers["X-Robots-Tag"] = "noindex, nofollow"
        return HTMLResponse(html_doc, status_code=status, headers=headers)

    app.state.render_404 = lambda request: page(
        request, path=request.url.path, title="Page not found", description="This page doesn't exist on AGENT CRASH LAB.",
        body=NOT_FOUND_BODY, status=404, indexable=False)

    @app.get("/", include_in_schema=False)
    def home(request: Request) -> HTMLResponse:
        return page(request, path="/", title="AGENT CRASH LAB · Reliability testing for AI agents",
                    description="Test AI agents against timeouts, prompt injection and race conditions. Find failures, "
                                "shrink them to a reproduction and turn them into regression tests.",
                    body=landing_body())

    @app.get("/privacy", include_in_schema=False)
    def privacy(request: Request) -> HTMLResponse:
        return page(request, path="/privacy", title="Privacy policy",
                    description="What AGENT CRASH LAB collects (contact-form messages, optional analytics), why, "
                                "how long we keep it and how to have it deleted.",
                    body=privacy_body())

    def contact_page(request: Request, *, error: str | None = None, errors: dict[str, str] | None = None,
                     values: dict[str, str] | None = None, status: int = 200) -> HTMLResponse:
        ts = turnstile()
        return page(request, path="/contact", title="Contact",
                    description="Questions about testing your AI agent, a bug report or a pilot? Send us a message.",
                    body=contact_body(general=error, errors=errors, values=values, token=guard.issue(),
                                      sitekey=ts[0] if ts else None),
                    captcha=bool(ts), status=status)

    @app.get("/contact", include_in_schema=False)
    def contact(request: Request) -> HTMLResponse:
        return contact_page(request)

    @app.post("/contact", include_in_schema=False)
    async def contact_submit(request: Request) -> Response:
        ip = client_ip(request)
        if limiter.check("ip:" + (ip or "?"), "contact"):
            return contact_page(request, error="Too many messages from your network. Please try again in a few minutes.",
                                status=429)
        origin = request.headers.get("origin")
        if origin and origin.rstrip("/") != base_url(request) and origin.rstrip("/") != str(request.base_url).rstrip("/"):
            return contact_page(request, error="This form must be sent from this website.", status=403)
        raw = (await request.body())[:20_000].decode("utf-8", "replace")
        form = {k: v[0] for k, v in parse_qs(raw, keep_blank_values=True).items()}
        values = {k: form.get(k, "").strip() for k in ("name", "email", "message")}
        source = {k: form[k].strip() for k in UTM_KEYS if form.get(k, "").strip() and UTM_RE.match(form[k].strip())}
        values.update(source)
        if form.get("website"):
            log("contact_spam", reason="honeypot")
            return RedirectResponse("/contact/thanks", status_code=303)  # don't tell the bot
        errors: dict[str, str] = {}
        if not 1 <= len(values["name"]) <= 100:
            errors["name"] = "Enter your name."
        if not EMAIL_RE.match(values["email"]):
            errors["email"] = "Enter a valid email address, like name@example.com."
        if not 10 <= len(values["message"]) <= 5000:
            errors["message"] = (f"Your message is {len(values['message'])} characters; write at least 10."
                                 if len(values["message"]) < 10 else "Keep your message under 5,000 characters.")
        if errors:
            return contact_page(request, errors=errors, values=values, status=422)
        problem = guard.check(form.get("t", ""))
        ts = turnstile()
        if not problem and ts and not verify(ts[1], form.get("cf-turnstile-response", ""), ip):
            problem = "We couldn't confirm you're human. Please complete the check and try again."
        if problem:
            return contact_page(request, error=problem, values=values, status=422)
        store.save_contact_message(values["name"], values["email"], values["message"],
                                   hashlib.sha256((ip or "").encode()).hexdigest()[:16], source=source or None)
        log("contact_received", email_domain=values["email"].split("@")[-1])
        return RedirectResponse("/contact/thanks", status_code=303)

    @app.get("/contact/thanks", include_in_schema=False)
    def thanks(request: Request) -> HTMLResponse:
        return page(request, path="/contact/thanks", title="Message sent",
                    description="Thanks for contacting AGENT CRASH LAB.", body=THANKS_BODY, indexable=False,
                    event="generate_lead")

    @app.get("/robots.txt", include_in_schema=False)
    def robots(request: Request) -> PlainTextResponse:
        return PlainTextResponse(
            "User-agent: *\nAllow: /\nDisallow: /app/\nDisallow: /api/\nDisallow: /docs\nDisallow: /redoc\n"
            "Disallow: /metrics\nDisallow: /contact/thanks\n\n"
            f"Sitemap: {base_url(request)}/sitemap.xml\n")

    @app.get("/sitemap.xml", include_in_schema=False)
    def sitemap(request: Request) -> Response:
        base = html.escape(base_url(request))
        urls = [("/", "weekly", "1.0"), ("/contact", "yearly", "0.5"), ("/privacy", "yearly", "0.3")]
        body = "".join(f"<url><loc>{base}{p}</loc><lastmod>{PAGE_UPDATED[p]}</lastmod><changefreq>{f}</changefreq>"
                       f"<priority>{pr}</priority></url>" for p, f, pr in urls)
        xml = f'<?xml version="1.0" encoding="UTF-8"?>\n<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">{body}</urlset>\n'
        return Response(xml, media_type="application/xml")

    @app.get("/search-index.json", include_in_schema=False)
    def search_index() -> Response:
        return Response(json.dumps(search_items()), media_type="application/json",
                        headers={"Cache-Control": "public, max-age=300"})

    @app.get("/og.png", include_in_schema=False)
    def og() -> Response:
        return Response((WEB_DIR / "og.png").read_bytes(), media_type="image/png",
                        headers={"Cache-Control": "public, max-age=86400"})

    @app.get("/app", include_in_schema=False)
    def app_redirect() -> RedirectResponse:
        return RedirectResponse("/app/", status_code=308)


def search_items() -> list[dict[str, str]]:
    """What the site search can find: every public page, section and FAQ answer."""
    items = [
        {"title": "Home", "subtitle": "Reliability testing for AI agents", "url": "/",
         "text": "fault injection timeouts rate limits stale data prompt injection race conditions regression tests"},
        {"title": "How it works", "subtitle": "Stress, detect, explain, minimize, prevent, compare", "url": "/#how",
         "text": "loop evaluators delta debugging minimization regression release gate pass^k confidence intervals"},
        {"title": "Measured results", "subtitle": f"v1 passed {RESULTS['v1_pass']}/100, v2 passed {RESULTS['v2_pass']}/100",
         "url": "/#results", "text": "benchmark duplicate refunds prompt injection attacks success rate failure modes"},
        {"title": "Pricing", "subtitle": "Free, Pro ₹2,499, Team ₹9,999, Enterprise", "url": "/#pricing",
         "text": "plans price cost monthly rupees inr free trial upgrade billing enterprise team pro"},
        {"title": "Create a free account", "subtitle": "Sign up with email or Google", "url": "/signup",
         "text": "sign up register account join start free"},
        {"title": "Sign in", "subtitle": "Open your dashboard", "url": "/login", "text": "login log in sign in password google forgot"},
        {"title": "Contact", "subtitle": "Send us a message", "url": "/contact", "text": "email question bug pilot support help"},
        {"title": "Privacy policy", "subtitle": "What we collect and why", "url": "/privacy",
         "text": "data cookies analytics google retention delete rights gdpr dpdp contact form utm campaign"},
        {"title": "Dashboard", "subtitle": "Experiments, failures, traces and regression tests", "url": "/app/",
         "text": "open app experiments failures trace explorer regressions"},
        {"title": "API reference", "subtitle": "Interactive REST documentation", "url": "/docs", "text": "api rest openapi swagger"},
    ]
    items += [{"title": q, "subtitle": "FAQ", "url": f"/#faq-{i}", "text": a} for i, (q, a) in enumerate(FAQ, 1)]
    return items


def landing_results_text() -> dict[str, Any]:
    return dict(RESULTS)
