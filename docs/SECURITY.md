# Security & production-readiness audit

This file tracks the common ways a quickly-built ("vibe-coded") app fails, and what this repo does about each one. **Verified** means a named test in `tests/test_security.py` (or another test file) fails if the protection is removed.

| # | Risk | Status | How it's handled | Verified by |
|---|---|---|---|---|
| 1 | API key in the frontend | ✅ | The dashboard holds no secrets. The LLM key is server-side only. A user's own API key lives in `sessionStorage` for one tab and is sent as a Bearer header. | `test_no_secrets_in_frontend` |
| 2 | No auth / role check | ✅ | People sign in with email + password or Google; sessions live in an HttpOnly, SameSite=Lax cookie and cookie-authenticated writes must come from this site (CSRF check). Roles: user and admin (admin console at `/admin`, separate admin sign-in). The first admin comes from a one-time setup link printed at startup. Scripts use scoped API keys (`read`, `write`, `admin`); 401 means "who are you?", 403 "not allowed". Disabling a user or revoking a key takes effect immediately. | `tests/test_auth.py` (11 tests: sign-up, sign-in, admin gate, setup link, CSRF, open redirect, reset, Google, disabled users), `test_every_api_route_requires_a_key`, `test_scopes_are_enforced` |
| 3 | SQL injection | ✅ | Every query is parameterised. The only f-strings in SQL interpolate constant column and table lists. Scenario assertions use an AST interpreter, never `eval`. | `test_sql_injection_payloads_are_inert`, `test_store_has_no_string_built_sql`, `test_assertions_are_safe_and_correct` |
| 4 | XSS / no input sanitising | ✅ | All dynamic values pass through `esc()`. A strict CSP blocks inline scripts and handlers even if something slips through. Ingested runs have typed, pattern-checked fields. Checked in a real browser with `<img onerror>` and `<script>` payloads: nothing executed. | `test_security_headers_and_csp`, `test_ingest_rejects_html_in_typed_fields`, `test_frontend_escapes_dynamic_values` |
| 5 | Passwords / keys not hashed | ✅ | User passwords use scrypt (salted, deliberately slow) and failed sign-ins take the same time whether or not the account exists. Sessions and reset links are random tokens stored only as SHA-256 hashes; reset links work once, for 60 minutes. API keys are 256-bit tokens stored as SHA-256 hashes and shown once. | `test_passwords_are_scrypt_hashed`, `test_password_reset_flow`, `test_api_keys_are_hashed_at_rest` |
| 6 | CORS open to all origins | ✅ | CORS is off by default (same-origin only). Origins must be listed exactly, and `*` is refused at startup. | `test_cors_closed_by_default`, `test_cors_wildcard_is_refused`, `test_cors_allowlist` |
| 7 | `.env` pushed to GitHub | ✅ | `.env`, databases and backups are git-ignored and docker-ignored. CI runs gitleaks on every push. | `test_env_files_are_ignored_and_absent`, CI `gitleaks` step |
| 8 | No rate limit | ✅ | Token buckets per key (or per IP): reads 300/min, writes 60/min, expensive calls 10/min, all configurable. Also a cap on concurrent experiments, a 5 MB body cap, and bounded experiment sizes. | `test_rate_limit`, `test_request_size_limit`, `test_experiment_size_is_bounded` |
| 9 | IDOR | ✅ | Every query joins on the caller's project. Another project's objects return 404 (no enumeration), and ingestion can't overwrite another project's ids. | `test_projects_cannot_see_each_other` |
| 10 | Outdated packages | ✅ | No npm dependencies at all (the frontend is plain JS). Python versions are pinned in `requirements.lock` and pip-audited (0 known vulnerabilities on 2026-11-17). Dependabot opens weekly update PRs. | CI `pip-audit` step |
| 11 | No DB schema planning | ✅ | Tables mirror the blueprint schema, and changes go through numbered migrations (`PRAGMA user_version`). | `test_migrations_are_versioned_and_idempotent` |
| 12 | N+1 queries | ✅ | List endpoints are single queries. Run lists come from a precomputed index stored with the experiment, and the dashboard never loops over requests to build a page. | code review; `test_hot_queries_use_indexes` |
| 13 | No indexes | ✅ | Every foreign key and hot filter is indexed. A test reads `EXPLAIN QUERY PLAN` to prove there are no full scans. | `test_hot_queries_use_indexes` |
| 14 | No caching layer | ✅ | Completed experiments and runs are immutable, so they're held in an LRU cache with ETags (304 on repeat). Live data is never cached. | `test_completed_experiments_are_cached_with_etags` |
| 15 | Zero error handling | ✅ | A global handler returns a request id, never a stack trace. Validation errors are trimmed. A failed worker marks the experiment FAILED and logs why. | `test_errors_return_request_id_not_stack_trace` |
| 16 | No logs | ✅ | JSON logs to stdout with request id, route, status, latency and key id. Auth headers are never logged. | `test_logs_are_structured_and_never_contain_keys` |
| 17 | No monitoring | ✅ | `/health` (liveness), `/ready` (readiness, no internals) and `/metrics` (Prometheus, admin-only). Example alert rules are in `docs/alerts.yml`. | `test_metrics_endpoint`, `test_readiness_reveals_no_internals` |
| 18 | No backup or rollback | ✅ | `crashlab backup create` uses SQLite's online backup API plus an integrity check. `restore` refuses corrupt files and keeps the previous database for rollback. | `test_backup_restore_roundtrip`, `test_restore_refuses_corrupt_backup` |
| 19 | No tests | ✅ | 129 tests (engine, evaluators, minimizer, SDK, CLI, API, security, website, real-browser checks), run in CI on every PR. | `pytest` |
| 20 | Copy-paste, no structure | ✅ | One module per blueprint component, with no duplicated logic between CLI, API and SDK (all call the same engine). Ruff lints style, bugs and security rules in CI. | CI `ruff` step |
| 21 | "Prompt fix = new bug" | ✅ | Nothing merges unless all 129 tests, lint, the dataset validation and the agent release gate pass. A fix that breaks something else fails CI before it ships. | CI |
| 22 | Can't debug your own code | ⚠️ partly | The tooling helps: request ids tie API errors to log lines, and the trace explorer replays any episode step by step. The real fix is human: read the code until you can explain every module. Start with `docs/ARCHITECTURE.md`, then `runner.py` → `evaluators.py` → `diagnosis.py`. | — |

## Not done yet (deliberately out of scope for a single-node slice)

- **TLS:** terminate HTTPS at a reverse proxy (Caddy, nginx, a cloud load balancer). Compose binds to `127.0.0.1` so the plain-HTTP port isn't exposed by accident.
- **Multi-node:** rate-limit buckets and the response cache are in-process. With more than one API node, move both to Redis.
- **Scheduled, off-site backups:** the command exists; schedule it (cron or k8s CronJob) and copy the output to object storage. A backup that's never been restored isn't proven, so run `restore` against a scratch path monthly.
- **Error tracking:** Sentry or similar is not wired in yet. Logs carry request ids so it can be added without code changes elsewhere.
- **Sandboxing customer agent code:** today agents run in-process. Before accepting untrusted code, run each episode in an isolated container (blueprint §9.1).
- **SSO (SAML/OIDC for companies):** sign-in is email + password or Google; enterprise SSO is not built.

## Website quality checklist

| Item | Status | Where / verified by |
|---|---|---|
| No horizontal scrolling, mobile overflow fixed | ✅ | Checked at 320, 390, 768 and 1440 px on every page: `tests/test_browser.py::test_no_errors_no_sideways_scroll` |
| Broken links | ✅ | Every in-app link is followed and must resolve: `test_links_resolve_and_unknown_routes_show_404` |
| Mobile menu | ✅ | Slide-in drawer with hamburger, scrim, Escape to close, closes on navigation: `test_mobile_menu_and_clickable_logo` |
| Favicon | ✅ | Inline SVG on the dashboard and report; `/favicon.ico` on the server: `test_favicon` |
| Page titles | ✅ | Each section sets `<Section> · AGENT CRASH LAB`: `test_every_view_sets_a_title` |
| Meta descriptions | ✅ | In `index.html` and `404.html`, updated per section: `test_pages_have_metadata` |
| Footer links | ✅ | Overview, Scenarios, Regression tests, Integrations, plus API reference when connected to a server |
| Custom 404 | ✅ | In-app 404 for unknown routes, HTML 404 page on the server (JSON for `/api`): `test_custom_404_page_for_browsers_json_for_api` |
| Copyright year | ✅ | Computed from the visitor's clock, never hard-coded |
| Compress images | n/a + ✅ | The app has no raster images (the logo is a 300-byte SVG). Responses are gzip-compressed instead: `test_dashboard_and_assets_are_compressed` |
| Broken buttons | ✅ | Every button on the main pages is clicked by the browser audit. "New experiment" in the read-only report now explains it needs the server instead of erroring. |
| Success and error messages | ✅ | Every action (start, cancel, minimize, save regression, run suite, validate, copy, connect) shows a success or error message. Errors are announced to screen readers and never leave a button stuck disabled. |
| Placeholder text | ✅ | None found. Loading states say what is loading. |
| Unused navigation | ✅ | "Live runs" is hidden in the read-only report, where it can't work |
| Clickable logo | ✅ | Sidebar logo and mobile header logo both go to the overview |
| Clickable phone / email | n/a | The app shows no phone number or email address. Add them as `tel:` / `mailto:` links if contact details are added. |
| Every page mobile-optimized | ✅ | Same browser tests, plus visual review at 390 px |

## Launch checklist (public website)

| # | Item | Status | How | Verified by (`tests/test_site.py` unless noted) |
|---|---|---|---|---|
| 1 | Custom 404 page | ✅ | Branded, noindex, links to the main pages. JSON 404 for the API. | `test_custom_404` |
| 2 | A different title on every page | ✅ | Server-rendered per page | `test_unique_titles_and_descriptions` |
| 3 | Meta description on every page | ✅ | 50–170 characters, unique | same |
| 4 | Share preview image | ✅ | Open Graph + Twitter card tags, 1200×630 `og.png` (17 KB) | `test_share_preview_tags_and_image` |
| 5 | Alt text on every image | ✅ | Decorative SVG icons are `aria-hidden`; charts carry `role="img"` + a label | `test_every_image_has_alt_and_icons_are_hidden` |
| 6 | Internal links | ✅ | Header, footer, CTAs, FAQ anchors, privacy links; every one returns 200 | `test_internal_links_resolve` |
| 7 | Sitemap for Google Search Console | ✅ | `/sitemap.xml` lists only live, indexable pages with absolute URLs | `test_sitemap_lists_only_live_indexable_pages` |
| 8 | robots.txt | ✅ | Allows the site; blocks `/app/`, `/api/`, `/docs`, `/metrics`, the thank-you page | `test_robots_txt` |
| 9 | HTTPS | ✅ config | Caddy with automatic Let's Encrypt and http→https redirect; HSTS via `CRASHLAB_HSTS=1`. Needs your domain and server to go live. | `test_https_setup` |
| 10 | Contact-form spam protection (Cloudflare Turnstile) | ✅ | Turnstile verified server-side when keys are set; always: signed time token, honeypot, rate limit, origin check | `test_turnstile_is_enforced_when_configured`, `test_contact_spam_defences` |
| 11 | Thank-you page after the form | ✅ | `/contact/thanks` (POST → 303 redirect, noindex), fires a `generate_lead` analytics event | `test_thank_you_page`, `test_browser_site.py` |
| 12 | Google Analytics, included and wired up | ✅ | GA4 with consent mode: nothing loads until Accept; never on the dashboard | `test_analytics_with_consent`, `test_browser_site.py::test_analytics_waits_for_consent` |
| 13 | Privacy policy | ✅ | Generated from what is actually switched on (analytics, Turnstile), with 180-day retention that is enforced in code. Not legal advice: have it reviewed before a commercial launch. | `test_privacy_policy_matches_configuration`, `test_contact_messages_expire_after_retention` |
| 14 | FAQ section | ✅ | 7 real questions + FAQPage structured data for Google | `test_faq_section_and_structured_data` |
| 15 | Testimonials, real ones only | ✅ | Hidden until `web/site/testimonials.json` has quotes with name, role and `"consent": true`; none were invented. The landing page shows measured benchmark results instead, and a test re-runs the benchmark so those numbers can't drift. | `test_no_fake_testimonials`, `test_published_numbers_match_the_benchmark` |

## Interface checklist

| Item | Status | Where | Verified by |
|---|---|---|---|
| Dark / light mode toggle | ✅ | Site + dashboard. Follows the OS until chosen, remembered, no flash (`theme.js` runs before CSS). Light chart colours re-validated for contrast and colour blindness. | `test_theme_toggle_persists_and_both_themes_fit`, `test_dashboard_theme_search_and_print` |
| Cookie banner | ✅ | Site only, and only when analytics is configured. Accept / Decline, reopen from the footer. | `test_analytics_waits_for_consent` |
| Site search | ✅ | `/` or Ctrl+K. Site: pages, sections, every FAQ answer. Dashboard: pages, scenarios, failure modes, failed runs. Keyboard navigation; exact IDs rank first. | `test_site_search`, `test_search_index_covers_pages_and_every_faq` |
| Back-to-top button | ✅ | Appears after scrolling, moves focus back to the content | `test_scrolling_aids_floating_buttons_and_copy` |
| Mobile menu | ✅ | Site + dashboard | `test_public_mobile_menu`, `test_mobile_menu_and_clickable_logo` |
| Loading animations | ✅ | Dashboard skeletons, spinners on busy buttons, a one-time episode-wall fill and terminal playback. All off under reduced motion. | visual review |
| Hover states | ✅ | Buttons (incl. pressed), links, nav underline, rows, cards, FAQ, chart marks | visual review |
| Scroll progress bar | ✅ | Long pages only | same scroll test |
| Copy button | ✅ | Every code block (site + dashboard), with a fallback when clipboard access is blocked | same scroll test |
| Print stylesheet | ✅ | Hides chrome, forces light colours, prints link URLs, keeps tables and charts whole | print assertions in both browser suites |
| Sticky header | ✅ | Site + dashboard | visual review |
| Skip to content | ✅ | Site + dashboard (works with the dashboard's hash router) | both browser suites |
| Password visibility toggle | ✅ | API-key field; re-hides on submit | `test_password_toggle_and_confirm_dialog` |
| UTM tracking | ✅ | Captured for the tab, URL tidied, passed to GA after consent and stored with contact leads; malformed tags dropped; disclosed in the privacy policy | `test_success_page_and_utm_attribution`, `test_utm_tags_are_validated_and_stored_with_the_lead` |
| Form success page | ✅ | `/contact/thanks` | same |
| Form error states + validation | ✅ | Inline per-field errors, error summary with links, `aria-invalid`, focus to the first problem, input kept. Server re-checks everything. | `test_contact_form_validation_and_error_states`, `test_field_level_errors_work_without_javascript` |
| Confirmation dialogs | ✅ | Cancel experiment, run regression suite, start a large experiment, sign out. Safe choice focused; Esc and backdrop cancel. | `test_password_toggle_and_confirm_dialog` |
| Last-updated date | ✅ | Every public page footer, results section, privacy policy, sitemap `lastmod`, dashboard run time | `test_last_updated_dates` |
| Expandable FAQ | ✅ | Native `<details>`, Expand all, opens from links and search | site browser tests |
| Floating contact button | ✅ | Public pages except the contact pages | same scroll test |
