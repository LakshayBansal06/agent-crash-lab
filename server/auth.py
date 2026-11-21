"""Human accounts: sign up, sign in (password or Google), admin panel.

* Passwords are hashed with scrypt; sessions are random tokens in an HttpOnly,
  SameSite=Lax cookie, stored server-side only as SHA-256 hashes, so signing out
  or disabling an account takes effect immediately.
* Every form POST checks the browser's Origin (CSRF) and is rate-limited per IP.
* The first admin is created from a one-time setup link printed in the server
  log (nobody can race you to it), or by signing in with Google using an email
  listed in CRASHLAB_ADMIN_EMAILS (Google has verified that address).
* Each account gets its own project, so users only ever see their own
  experiments (the same isolation the API keys use).

Environment (all optional):
  GOOGLE_CLIENT_ID / GOOGLE_CLIENT_SECRET   "Continue with Google"
  CRASHLAB_ADMIN_EMAILS                     comma list; these Google accounts become admins
  SMTP_HOST / SMTP_PORT / SMTP_USER / SMTP_PASSWORD / SMTP_FROM   password-reset emails
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import html
import json
import os
import secrets
import smtplib
import time
from collections.abc import Callable
from email.message import EmailMessage
from typing import Any
from urllib.parse import parse_qs, quote, urlencode

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response

from .observability import log
from .security import SESSION_COOKIE, public_url, same_origin
from .site import EMAIL_RE, LOGO, ga_id, layout, site_csp

GOOGLE_AUTH = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN = "https://oauth2.googleapis.com/token"  # noqa: S105 - a URL, not a secret
GOOGLE_USERINFO = "https://openidconnect.googleapis.com/v1/userinfo"
OAUTH_COOKIE = "acl_oauth"
MIN_PASSWORD = 10

e = html.escape
HttpCall = Callable[..., dict[str, Any]]


def _env(name: str) -> str:
    return os.environ.get(name, "").strip()


def google_enabled() -> bool:
    return bool(_env("GOOGLE_CLIENT_ID") and _env("GOOGLE_CLIENT_SECRET"))


def smtp_enabled() -> bool:
    return bool(_env("SMTP_HOST") and _env("SMTP_FROM"))


def admin_emails() -> set[str]:
    return {x.strip().lower() for x in _env("CRASHLAB_ADMIN_EMAILS").split(",") if x.strip()}


def safe_next(target: str | None, default: str = "/app/") -> str:
    """Only same-site paths: '/app/' yes, '//evil.com' or 'https://evil.com' no (open-redirect guard)."""
    t = (target or "").strip()
    return t if t.startswith("/") and not t.startswith(("//", "/\\")) and len(t) < 300 else default


def password_problem(pw: str, email: str = "") -> str | None:
    if len(pw) < MIN_PASSWORD:
        return f"Use at least {MIN_PASSWORD} characters."
    if len(pw) > 200:
        return "Use at most 200 characters."
    if email and pw.lower() == email.lower():
        return "Don't use your email address as your password."
    return None


def send_reset_email(to: str, link: str) -> None:  # pragma: no cover - needs a mail server
    msg = EmailMessage()
    msg["Subject"], msg["From"], msg["To"] = "Reset your AGENT CRASH LAB password", _env("SMTP_FROM"), to
    msg.set_content(f"Someone (hopefully you) asked to reset your password.\n\nSet a new one here (valid for 60 minutes):\n"
                    f"{link}\n\nIf it wasn't you, ignore this email; your password is unchanged.")
    with smtplib.SMTP(_env("SMTP_HOST"), int(_env("SMTP_PORT") or 587), timeout=15) as s:
        s.starttls()
        if _env("SMTP_USER"):
            s.login(_env("SMTP_USER"), _env("SMTP_PASSWORD"))
        s.send_message(msg)


def google_http(method: str, url: str, **kw: Any) -> dict[str, Any]:  # pragma: no cover - network
    import httpx

    r = httpx.request(method, url, timeout=15, **kw)
    r.raise_for_status()
    return r.json()


# ------------------------------------------------------------------- markup
GOOGLE_ICON = ('<svg viewBox="0 0 18 18" aria-hidden="true" width="18" height="18"><path fill="#4285F4" d="M17.64 9.2c0-.64-.06-1.25-.16-1.84H9v3.48h4.84a4.14 4.14 0 0 1-1.8 2.72v2.26h2.92a8.78 8.78 0 0 0 2.68-6.62z"/>'
               '<path fill="#34A853" d="M9 18c2.43 0 4.47-.8 5.96-2.18l-2.92-2.26c-.8.54-1.84.86-3.04.86-2.34 0-4.32-1.58-5.03-3.7H.96v2.33A9 9 0 0 0 9 18z"/>'
               '<path fill="#FBBC05" d="M3.97 10.72A5.4 5.4 0 0 1 3.68 9c0-.6.1-1.18.29-1.72V4.95H.96A9 9 0 0 0 0 9c0 1.45.35 2.82.96 4.05l3.01-2.33z"/>'
               '<path fill="#EA4335" d="M9 3.58c1.32 0 2.5.45 3.44 1.35l2.58-2.58A9 9 0 0 0 .96 4.95l3.01 2.33C4.68 5.16 6.66 3.58 9 3.58z"/></svg>')


def field(name: str, label: str, control: str, errors: dict[str, str], hint: str = "") -> str:
    err = errors.get(name)
    described = " ".join(x for x in (f"{name}-hint" if hint else "", f"{name}-error" if err else "") if x)
    control = control.replace("{aria}", (' aria-invalid="true"' if err else "") +
                              (f' aria-describedby="{described}"' if described else ""))
    return (f'<div class="field"><label for="{name}">{label}</label>{control}'
            + (f'<div class="hint" id="{name}-hint">{hint}</div>' if hint else "")
            + (f'<div class="field-error" id="{name}-error">{e(err)}</div>' if err else "") + "</div>")


def pw_input(name: str, autocomplete: str) -> str:
    # site.js adds the show/hide button (shared with the dashboard's key field)
    return f'<input id="{name}" name="{name}" type="password" autocomplete="{autocomplete}" required maxlength="200"{{aria}}>'


def notice(kind: str, text: str) -> str:
    role = "alert" if kind == "error" else "status"
    return f'<div class="auth-notice {kind}" role="{role}" tabindex="-1" id="form-notice">{text}</div>' if text else ""


def auth_card(*, title: str, lede: str, body: str, foot: str = "", admin: bool = False) -> str:
    # The cover of a case file: real findings from the included benchmark, never invented proof.
    return f"""
<section class="auth-shell{' is-admin' if admin else ''}">
  <aside class="case-cover" aria-label="About AGENT CRASH LAB">
    <div class="cover-strip"><span class="cover-mark">{LOGO}</span></div>
    <p class="cover-quote">Your agent passed the demo. <b>Will it survive production?</b></p>
    <dl class="cover-findings">
      <div><dt>Duplicate refunds</dt><dd>15% <span>→</span> 0%</dd></div>
      <div><dt>Prompt injections that worked</dt><dd>75% <span>→</span> 0%</dd></div>
      <div><dt>Failing run, minimized</dt><dd>8 steps <span>→</span> 2</dd></div>
    </dl>
    <p class="cover-note">Measured on the included benchmark: reference agent v1 against the fixed v2, 200 episodes.</p>
  </aside>
  <div class="auth-card">
    <h1>{title}</h1>
    <p class="lede">{lede}</p>
    {body}
    {f'<p class="auth-foot">{foot}</p>' if foot else ''}
  </div>
</section>"""


def google_button(next_path: str, label: str = "Continue with Google") -> str:
    if not google_enabled():
        return ""
    return (f'<a class="btn google-btn" href="/auth/google?next={quote(next_path)}">{GOOGLE_ICON}<span>{label}</span></a>'
            '<div class="or" role="separator"><span>or use email</span></div>')


# ------------------------------------------------------------------- routes
def mount_auth(app: FastAPI, *, http: HttpCall | None = None, mailer: Callable[[str, str], None] | None = None) -> None:
    store, limiter = app.state.store, app.state.limiter
    call = http or google_http
    mail = mailer or send_reset_email
    signer = (_env("CRASHLAB_FORM_SECRET").encode() or secrets.token_bytes(32))
    app.state.setup_token = setup_token = secrets.token_urlsafe(24)

    def page(request: Request, *, path: str, title: str, body: str, status: int = 200,
             description: str = "Sign in to AGENT CRASH LAB.") -> HTMLResponse:
        doc = layout(request, path=path, title=title, description=description, body=body, indexable=False)
        return HTMLResponse(doc, status_code=status, headers={
            "Content-Security-Policy": site_csp(analytics=bool(ga_id()), captcha=False),
            "Referrer-Policy": "strict-origin-when-cross-origin", "Cache-Control": "no-store",
            "X-Robots-Tag": "noindex, nofollow"})

    def current_user(request: Request) -> dict[str, Any] | None:
        return store.session_user(request.cookies.get(SESSION_COOKIE))

    def secure(request: Request) -> bool:
        return request.url.scheme == "https" or public_url().startswith("https://")

    def sign_in(request: Request, user: dict[str, Any], to: str) -> Response:
        resp = RedirectResponse(to, status_code=303)
        resp.set_cookie(SESSION_COOKIE, store.create_session(user["id"]), max_age=14 * 86400, httponly=True,
                        samesite="lax", secure=secure(request), path="/")
        log("sign_in", user=user["id"], role=user["role"])
        return resp

    async def form(request: Request) -> dict[str, str]:
        raw = (await request.body())[:20_000].decode("utf-8", "replace")
        return {k: v[0] for k, v in parse_qs(raw, keep_blank_values=True).items()}

    def guard(request: Request) -> str | None:
        """Common checks for every auth POST: same-site origin, then the per-IP rate limit."""
        if not same_origin(request):
            return "This form must be sent from this website."
        ip = request.client.host if request.client else "?"
        if limiter.check("ip:" + ip, "auth"):
            return "Too many attempts from your network. Wait a minute and try again."
        return None

    # ---------------------------------------------------------------- login
    def login_body(*, admin: bool, nxt: str, error: str = "", info: str = "", email: str = "") -> str:
        action = "/admin/login" if admin else "/login"
        setup = ""
        if store.count_admins() == 0:
            setup = notice("info", "First run: no admin account exists yet. Open the <b>setup link printed in the "
                                   "server window</b> to create one.")
        body = f"""{setup}{notice("error", e(error)) if error else notice("info", info)}
    {google_button(nxt, "Continue with Google")}
    <form class="form auth-form" method="post" action="{action}" novalidate>
      <input type="hidden" name="next" value="{e(nxt)}">
      {field("email", "Email", f'<input id="email" name="email" type="email" autocomplete="username" required maxlength="254" value="{e(email)}"{{aria}}>', {})}
      {field("password", "Password", pw_input("password", "current-password"), {})}
      <div class="auth-row"><a href="/forgot">Forgot password?</a></div>
      <button class="btn primary block" type="submit">{"Sign in to admin" if admin else "Sign in"}</button>
    </form>"""
        foot = ('Not an admin? <a href="/login">Regular sign in</a>' if admin else
                'New here? <a href="/signup">Create a free account</a> · <a href="/admin/login">Admin sign in</a>')
        return auth_card(title="Admin sign in" if admin else "Welcome back",
                         lede=("Manage users, roles and platform health." if admin else
                               "Sign in to run crash tests and see your agents' failures."),
                         body=body, foot=foot, admin=admin)

    def show_login(request: Request, *, admin: bool, status: int = 200, **kw: Any) -> HTMLResponse:
        nxt = safe_next(kw.pop("nxt", None) or request.query_params.get("next"), "/admin" if admin else "/app/")
        msgs = {"signed-out": "You're signed out.", "reset": "Password changed. Sign in with your new password.",
                "expired": "Please sign in to continue."}
        info = kw.pop("info", "") or msgs.get(request.query_params.get("m", ""), "")
        gerr = {"google": "Google sign-in didn't complete. Please try again.",
                "disabled": "This account is disabled. Contact an admin."}.get(request.query_params.get("e", ""), "")
        return page(request, path="/admin/login" if admin else "/login", title="Admin sign in" if admin else "Sign in",
                    body=login_body(admin=admin, nxt=nxt, info=info, error=kw.pop("error", "") or gerr, **kw), status=status)

    @app.get("/login", include_in_schema=False)
    def login_page(request: Request) -> Response:
        if current_user(request):
            return RedirectResponse(safe_next(request.query_params.get("next")), status_code=303)
        return show_login(request, admin=False)

    @app.get("/admin/login", include_in_schema=False)
    def admin_login_page(request: Request) -> Response:
        u = current_user(request)
        if u and u["role"] == "admin":
            return RedirectResponse("/admin", status_code=303)
        return show_login(request, admin=True)

    async def do_login(request: Request, admin: bool) -> Response:
        f = await form(request)
        email, nxt = f.get("email", "").strip(), f.get("next")
        if problem := guard(request):
            return show_login(request, admin=admin, status=429 if "Too many" in problem else 403, error=problem,
                              email=email, nxt=nxt)
        user = store.verify_login(email, f.get("password", "")) if email else None
        if not user:
            log("sign_in_failed", email_domain=email.split("@")[-1][:60])
            return show_login(request, admin=admin, status=401, error="That email and password don't match an account.",
                              email=email, nxt=nxt)
        if user["disabled_at"]:
            return show_login(request, admin=admin, status=403, error="This account is disabled. Contact an admin.",
                              email=email, nxt=nxt)
        if admin and user["role"] != "admin":
            return show_login(request, admin=True, status=403, email=email, nxt=nxt,
                              error="This account doesn't have admin access. Use the regular sign in.")
        return sign_in(request, user, safe_next(nxt, "/admin" if admin else "/app/"))

    @app.post("/login", include_in_schema=False)
    async def login_submit(request: Request) -> Response:
        return await do_login(request, admin=False)

    @app.post("/admin/login", include_in_schema=False)
    async def admin_login_submit(request: Request) -> Response:
        return await do_login(request, admin=True)

    @app.post("/logout", include_in_schema=False)
    async def logout(request: Request) -> Response:
        if same_origin(request):
            store.end_session(request.cookies.get(SESSION_COOKIE))
        resp = RedirectResponse("/login?m=signed-out", status_code=303)
        resp.delete_cookie(SESSION_COOKIE, path="/")
        return resp

    # --------------------------------------------------------------- signup
    def signup_body(errors: dict[str, str] | None = None, values: dict[str, str] | None = None, error: str = "") -> str:
        errors, v = errors or {}, values or {}
        summary = notice("error", e(error or "Please fix the highlighted fields.")) if errors or error else ""
        body = f"""{summary}
    {google_button("/app/", "Sign up with Google")}
    <form class="form auth-form" method="post" action="/signup" novalidate>
      {field("name", "Your name", f'<input id="name" name="name" autocomplete="name" required maxlength="100" value="{e(v.get("name", ""))}"{{aria}}>', errors)}
      {field("email", "Work email", f'<input id="email" name="email" type="email" autocomplete="email" required maxlength="254" value="{e(v.get("email", ""))}"{{aria}}>', errors)}
      {field("password", "Password", pw_input("password", "new-password"), errors, f"At least {MIN_PASSWORD} characters. A short sentence works well.")}
      <button class="btn primary block" type="submit">Create account</button>
      <p class="sub">By creating an account you agree to our <a href="/privacy">privacy policy</a>.</p>
    </form>"""
        return auth_card(title="Create your account", lede="Free to start. Crash-test your first agent in two minutes.",
                         body=body, foot='Already have an account? <a href="/login">Sign in</a>')

    @app.get("/signup", include_in_schema=False)
    def signup_page(request: Request) -> Response:
        if current_user(request):
            return RedirectResponse("/app/", status_code=303)
        return page(request, path="/signup", title="Create your account", body=signup_body())

    @app.post("/signup", include_in_schema=False)
    async def signup_submit(request: Request) -> Response:
        f = await form(request)
        v = {k: f.get(k, "").strip() for k in ("name", "email")}
        if problem := guard(request):
            return page(request, path="/signup", title="Create your account", body=signup_body(values=v, error=problem),
                        status=429 if "Too many" in problem else 403)
        errors: dict[str, str] = {}
        if not 1 <= len(v["name"]) <= 100:
            errors["name"] = "Enter your name."
        if not EMAIL_RE.match(v["email"]):
            errors["email"] = "Enter a valid email address, like name@company.com."
        elif store.user_by_email(v["email"]):
            errors["email"] = "An account with this email already exists. Sign in instead, or reset your password."
        if pw := password_problem(f.get("password", ""), v["email"]):
            errors["password"] = pw
        if errors:
            return page(request, path="/signup", title="Create your account", body=signup_body(errors, v), status=422)
        user = store.create_user(v["email"], v["name"], password=f["password"])
        log("sign_up", user=user["id"], method="password")
        return sign_in(request, user, "/app/?welcome=1")

    # ---------------------------------------------------------- first admin
    def setup_body(token: str, errors: dict[str, str] | None = None, values: dict[str, str] | None = None) -> str:
        errors, v = errors or {}, values or {}
        body = f"""{notice("error", "Please fix the highlighted fields.") if errors else ""}
    <form class="form auth-form" method="post" action="/setup" novalidate>
      <input type="hidden" name="token" value="{e(token)}">
      {field("name", "Your name", f'<input id="name" name="name" autocomplete="name" required maxlength="100" value="{e(v.get("name", ""))}"{{aria}}>', errors)}
      {field("email", "Email", f'<input id="email" name="email" type="email" autocomplete="email" required maxlength="254" value="{e(v.get("email", ""))}"{{aria}}>', errors)}
      {field("password", "Password", pw_input("password", "new-password"), errors, f"At least {MIN_PASSWORD} characters.")}
      <button class="btn primary block" type="submit">Create admin account</button>
    </form>"""
        return auth_card(title="Create the first admin", lede="This one-time page sets up the account that manages everyone else.",
                         body=body, admin=True)

    def setup_allowed(token: str) -> bool:
        return store.count_admins() == 0 and hmac.compare_digest(token.encode(), setup_token.encode())

    @app.get("/setup", include_in_schema=False)
    def setup_page(request: Request) -> Response:
        token = request.query_params.get("token", "")
        if not setup_allowed(token):
            return RedirectResponse("/admin/login", status_code=303)
        return page(request, path="/setup", title="Create the first admin", body=setup_body(token))

    @app.post("/setup", include_in_schema=False)
    async def setup_submit(request: Request) -> Response:
        f = await form(request)
        token, v = f.get("token", ""), {k: f.get(k, "").strip() for k in ("name", "email")}
        if guard(request) or not setup_allowed(token):
            return RedirectResponse("/admin/login", status_code=303)
        errors: dict[str, str] = {}
        if not 1 <= len(v["name"]) <= 100:
            errors["name"] = "Enter your name."
        if not EMAIL_RE.match(v["email"]):
            errors["email"] = "Enter a valid email address."
        if pw := password_problem(f.get("password", ""), v["email"]):
            errors["password"] = pw
        if errors:
            return page(request, path="/setup", title="Create the first admin", body=setup_body(token, errors, v), status=422)
        existing = store.user_by_email(v["email"])
        if existing:
            store.update_user(existing["id"], role="admin")
            store.set_password(existing["id"], f["password"])
            user = store.get_user(existing["id"])
        else:
            user = store.create_user(v["email"], v["name"], password=f["password"], role="admin")
        log("admin_created", user=user["id"], via="setup_link")
        return sign_in(request, user, "/admin")

    # ------------------------------------------------------- password reset
    def forgot_body(sent: bool = False, error: str = "") -> str:
        if sent:
            msg = ("If an account exists for that email, we've sent a reset link. It works for 60 minutes. "
                   "Check your spam folder too.")
            return auth_card(title="Check your email", lede=msg, body='<a class="btn primary block" href="/login">Back to sign in</a>')
        how = ("We'll email you a link to set a new password." if smtp_enabled() else
               "Email isn't set up on this server yet, so ask an admin for a reset link"
               + (", or sign in with Google." if google_enabled() else "."))
        form_html = f"""<form class="form auth-form" method="post" action="/forgot" novalidate>
      {field("email", "Email", '<input id="email" name="email" type="email" autocomplete="email" required maxlength="254"{aria}>', {})}
      <button class="btn primary block" type="submit">Send reset link</button></form>""" if smtp_enabled() else ""
        return auth_card(title="Reset your password", lede=how, body=notice("error", e(error)) + form_html,
                         foot='Remembered it? <a href="/login">Sign in</a>')

    @app.get("/forgot", include_in_schema=False)
    def forgot_page(request: Request) -> HTMLResponse:
        return page(request, path="/forgot", title="Reset your password", body=forgot_body())

    @app.post("/forgot", include_in_schema=False)
    async def forgot_submit(request: Request) -> HTMLResponse:
        f = await form(request)
        if problem := guard(request):
            return page(request, path="/forgot", title="Reset your password", body=forgot_body(error=problem), status=429)
        user = store.user_by_email(f.get("email", "")) if smtp_enabled() else None
        if user and not user["disabled_at"]:
            link = (public_url() or str(request.base_url).rstrip("/")) + "/reset?token=" + store.create_reset(user["id"])
            try:
                mail(user["email"], link)
            except Exception:  # noqa: BLE001 - never reveal whether the account exists
                log("reset_email_failed", user=user["id"])
        return page(request, path="/forgot", title="Check your email", body=forgot_body(sent=True))  # same answer either way

    def reset_body(token: str, error: str = "") -> str:
        errs = {"password": error} if error else {}
        body = f"""{notice("error", "Please fix the highlighted field.") if error else ""}
    <form class="form auth-form" method="post" action="/reset" novalidate>
      <input type="hidden" name="token" value="{e(token)}">
      {field("password", "New password", pw_input("password", "new-password"), errs, f"At least {MIN_PASSWORD} characters.")}
      <button class="btn primary block" type="submit">Set new password</button></form>"""
        return auth_card(title="Choose a new password", lede="This signs you out on every other device.", body=body)

    @app.get("/reset", include_in_schema=False)
    def reset_page(request: Request) -> HTMLResponse:
        token = request.query_params.get("token", "")
        if not store.reset_user(token):
            return page(request, path="/reset", title="Link expired", status=410, body=auth_card(
                title="This link has expired", lede="Reset links work once, for 60 minutes. Ask for a new one.",
                body='<a class="btn primary block" href="/forgot">Get a new link</a>'))
        return page(request, path="/reset", title="Choose a new password", body=reset_body(token))

    @app.post("/reset", include_in_schema=False)
    async def reset_submit(request: Request) -> Response:
        f = await form(request)
        token = f.get("token", "")
        if guard(request):
            return RedirectResponse("/forgot", status_code=303)
        user = store.reset_user(token)
        if not user:
            return RedirectResponse("/reset?token=expired", status_code=303)
        if pw := password_problem(f.get("password", ""), user["email"]):
            return page(request, path="/reset", title="Choose a new password", body=reset_body(token, pw), status=422)
        store.reset_user(token, consume=True)
        store.set_password(user["id"], f["password"])
        log("password_reset", user=user["id"])
        return RedirectResponse("/login?m=reset", status_code=303)

    # -------------------------------------------------------------- account
    def account_body(u: dict[str, Any], error: str = "", ok: str = "") -> str:
        methods = " · ".join(x for x in ("Password" if u["has_password"] else "", "Google" if u["google"] else "") if x)
        admin_link = '<a class="btn" href="/admin">Open admin console</a>' if u["role"] == "admin" else ""
        current = (field("current", "Current password", pw_input("current", "current-password"), {})
                   if u["has_password"] else "")
        return f"""
<section class="account">
  <header class="account-head"><div class="avatar" aria-hidden="true">{e(u["name"][:1].upper())}</div>
    <div><h1>{e(u["name"])}</h1><p class="sub">{e(u["email"])} · <span class="role-pill {u["role"]}">{u["role"]}</span></p></div></header>
  {notice("error", e(error)) if error else notice("success", e(ok))}
  <div class="account-grid">
    <div class="panel panel-pad"><h2>Your workspace</h2>
      <dl class="kv"><dt>Sign-in methods</dt><dd>{methods}</dd><dt>Member since</dt><dd>{e(u["created_at"][:10])}</dd>
      <dt>Last sign-in</dt><dd>{e((u["last_login_at"] or "")[:16].replace("T", " "))} UTC</dd><dt>Project</dt><dd><code>{e(u["project_id"])}</code></dd></dl>
      <div class="btn-row"><a class="btn primary" href="/app/">Open dashboard</a>{admin_link}</div></div>
    <div class="panel panel-pad"><h2>{"Change password" if u["has_password"] else "Add a password"}</h2>
      <form class="form" method="post" action="/account/password" novalidate>
        {current}
        {field("password", "New password", pw_input("password", "new-password"), {}, f"At least {MIN_PASSWORD} characters. Signs out other devices.")}
        <button class="btn" type="submit">Save password</button></form></div>
  </div>
  <form method="post" action="/logout" class="signout"><button class="btn danger-ghost" type="submit">Sign out</button></form>
</section>"""

    @app.get("/account", include_in_schema=False)
    def account(request: Request) -> Response:
        u = current_user(request)
        if not u:
            return RedirectResponse("/login?next=/account&m=expired", status_code=303)
        ok = "Password saved." if request.query_params.get("m") == "saved" else ""
        return page(request, path="/account", title="Your account", body=account_body(u, ok=ok))

    @app.post("/account/password", include_in_schema=False)
    async def change_password(request: Request) -> Response:
        u = current_user(request)
        if not u:
            return RedirectResponse("/login?m=expired", status_code=303)
        f = await form(request)
        if problem := guard(request):
            return page(request, path="/account", title="Your account", body=account_body(u, error=problem), status=403)
        if u["has_password"] and not store.verify_login(u["email"], f.get("current", "")):
            return page(request, path="/account", title="Your account", status=401,
                        body=account_body(u, error="Your current password is wrong."))
        if pw := password_problem(f.get("password", ""), u["email"]):
            return page(request, path="/account", title="Your account", body=account_body(u, error=pw), status=422)
        store.set_password(u["id"], f["password"])
        return sign_in(request, store.get_user(u["id"]), "/account?m=saved")  # fresh session on this device

    # ---------------------------------------------------------------- admin
    def admin_body(me: dict[str, Any], flash: str = "", link: str = "") -> str:
        users, counts = store.list_users(), store.counts()
        msgs = store.list_contact_messages(limit=20)
        stats = [("Users", len(users)), ("Admins", sum(u["role"] == "admin" for u in users)),
                 ("Experiments", counts.get("experiments", 0)), ("Episodes", counts.get("runs", 0)),
                 ("Failures found", counts.get("failures", 0)), ("Contact messages", counts.get("contact_messages", 0))]
        tiles = "".join(f'<div><dt>{k}</dt><dd>{v:,}</dd></div>' for k, v in stats)

        def actions(u: dict[str, Any]) -> str:
            if u["id"] == me["id"]:
                return '<span class="sub">You</span>'
            role_to = "user" if u["role"] == "admin" else "admin"
            btn = lambda act, label, cls="": (f'<button class="btn small {cls}" name="action" value="{act}">{label}</button>')  # noqa: E731
            return (f'<form method="post" action="/admin/users/{e(u["id"])}" class="row-actions">'
                    + btn("role-" + role_to, "Make " + role_to)
                    + (btn("enable", "Enable") if u["disabled_at"] else btn("disable", "Disable", "danger-ghost"))
                    + btn("reset-link", "Reset link") + "</form>")

        rows = "".join(
            f'<tr><td><b>{e(u["name"])}</b><div class="sub">{e(u["email"])}</div></td>'
            f'<td><span class="role-pill {u["role"]}">{u["role"]}</span></td>'
            f'<td>{"Google" if u["google"] else ""}{" + " if u["google"] and u["has_password"] else ""}{"Password" if u["has_password"] else ""}</td>'
            f'<td>{e((u["last_login_at"] or "never")[:10])}</td>'
            f'<td>{"<span class=status-off>Disabled</span>" if u["disabled_at"] else "Active"}</td><td>{actions(u)}</td></tr>'
            for u in users)
        inbox = "".join(f'<li><b>{e(m["name"])}</b> <a href="mailto:{e(m["email"])}">{e(m["email"])}</a>'
                        f'<span class="sub"> · {e(m["created_at"][:10])}</span><p>{e(m["message"][:400])}</p></li>' for m in msgs)
        link_html = (f'<div class="auth-notice success" role="status">Reset link (valid 60 minutes, works once). Send it to the user:'
                     f'<pre class="code-copy"><code>{e(link)}</code></pre></div>') if link else ""
        return f"""
<section class="admin">
  <div class="docket-strip"><span>Admin console</span><span>Signed in as <b class="id">{e(me["email"])}</b></span></div>
  <header class="admin-head"><div><h1>Platform overview</h1>
    <p class="sub">Every account, its access, and what visitors sent you.</p></div>
    <div class="btn-row"><a class="btn" href="/app/">Dashboard</a><a class="btn" href="/docs">API reference</a></div></header>
  {notice("success", e(flash))}{link_html}
  <dl class="register" aria-label="Platform totals">{tiles}</dl>
  <div class="panel"><div class="panel-pad"><h2>Users</h2><p class="sub">Each user has a private project. Disabling signs them out everywhere.</p></div>
    <div class="table-wrap"><table class="table"><thead><tr><th>User</th><th>Role</th><th>Sign-in</th><th>Last sign-in</th><th>Status</th><th><span class="sr-only">Actions</span></th></tr></thead>
    <tbody>{rows or '<tr><td colspan="6">No users yet.</td></tr>'}</tbody></table></div></div>
  <div class="panel panel-pad"><h2>Latest contact messages</h2>{f'<ul class="inbox">{inbox}</ul>' if inbox else '<p class="sub">No messages yet.</p>'}</div>
</section>"""

    def admin_gate(request: Request) -> tuple[dict[str, Any] | None, Response | None]:
        u = current_user(request)
        if not u:
            return None, RedirectResponse("/admin/login?m=expired", status_code=303)
        if u["role"] != "admin":
            return None, page(request, path="/admin", title="Admins only", status=403, body=auth_card(
                title="Admins only", lede="Your account doesn't have admin access. Ask an admin to upgrade it.",
                body='<a class="btn primary block" href="/app/">Go to your dashboard</a>'))
        return u, None

    @app.get("/admin", include_in_schema=False)
    def admin(request: Request) -> Response:
        me, denied = admin_gate(request)
        return denied or page(request, path="/admin", title="Admin console", body=admin_body(me))

    @app.post("/admin/users/{uid}", include_in_schema=False)
    async def admin_user_action(uid: str, request: Request) -> Response:
        me, denied = admin_gate(request)
        if denied:
            return denied
        f = await form(request)
        target, act = store.get_user(uid), f.get("action", "")
        if not same_origin(request) or not target or uid == me["id"]:
            return page(request, path="/admin", title="Admin console", status=400, body=admin_body(me, "Nothing changed."))
        flash, link = "", ""
        if act in ("role-admin", "role-user"):
            store.update_user(uid, role=act[5:])
            flash = f"{target['email']} is now {'an admin' if act == 'role-admin' else 'a regular user'}."
        elif act in ("disable", "enable"):
            store.update_user(uid, disabled=act == "disable")
            flash = f"{target['email']} was {act}d."
        elif act == "reset-link":
            link = (public_url() or str(request.base_url).rstrip("/")) + "/reset?token=" + store.create_reset(uid)
        log("admin_action", by=me["id"], target=uid, action=act)
        return page(request, path="/admin", title="Admin console", body=admin_body(me, flash, link))

    # --------------------------------------------------------------- Google
    def sign(data: dict[str, Any]) -> str:
        raw = base64.urlsafe_b64encode(json.dumps(data).encode()).decode()
        return raw + "." + hmac.new(signer, raw.encode(), hashlib.sha256).hexdigest()

    def unsign(value: str) -> dict[str, Any] | None:
        raw, _, sig = (value or "").partition(".")
        if not hmac.compare_digest(sig, hmac.new(signer, raw.encode(), hashlib.sha256).hexdigest()):
            return None
        data = json.loads(base64.urlsafe_b64decode(raw))
        return data if data.get("exp", 0) > time.time() else None

    def redirect_uri(request: Request) -> str:
        return (public_url() or str(request.base_url).rstrip("/")) + "/auth/google/callback"

    @app.get("/auth/google", include_in_schema=False)
    def google_start(request: Request) -> Response:
        if not google_enabled():
            return RedirectResponse("/login", status_code=303)
        state, verifier = secrets.token_urlsafe(24), secrets.token_urlsafe(48)
        challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
        url = GOOGLE_AUTH + "?" + urlencode({
            "client_id": _env("GOOGLE_CLIENT_ID"), "redirect_uri": redirect_uri(request), "response_type": "code",
            "scope": "openid email profile", "state": state, "code_challenge": challenge,
            "code_challenge_method": "S256", "prompt": "select_account"})
        resp = RedirectResponse(url, status_code=303)
        resp.set_cookie(OAUTH_COOKIE, sign({"s": state, "v": verifier, "n": safe_next(request.query_params.get("next")),
                                            "exp": time.time() + 600}),
                        max_age=600, httponly=True, samesite="lax", secure=secure(request), path="/auth/google")
        return resp

    @app.get("/auth/google/callback", include_in_schema=False)
    def google_callback(request: Request) -> Response:
        fail = RedirectResponse("/login?e=google", status_code=303)
        fail.delete_cookie(OAUTH_COOKIE, path="/auth/google")
        flow = unsign(request.cookies.get(OAUTH_COOKIE, ""))
        q = request.query_params
        if not google_enabled() or not flow or not q.get("code") or not hmac.compare_digest(q.get("state", ""), flow["s"]):
            return fail
        try:
            tok = call("POST", GOOGLE_TOKEN, data={
                "code": q["code"], "client_id": _env("GOOGLE_CLIENT_ID"), "client_secret": _env("GOOGLE_CLIENT_SECRET"),
                "redirect_uri": redirect_uri(request), "grant_type": "authorization_code", "code_verifier": flow["v"]})
            info = call("GET", GOOGLE_USERINFO, headers={"Authorization": f"Bearer {tok['access_token']}"})
        except Exception:  # noqa: BLE001 - any failure talking to Google is a failed sign-in, never a crash
            log("google_sign_in_failed")
            return fail
        sub, email = str(info.get("sub", "")), str(info.get("email", ""))
        if not sub or not EMAIL_RE.match(email) or info.get("email_verified") is not True:
            return fail
        user = store.user_by_google(sub)
        if not user and (user := store.user_by_email(email)):
            store.link_google(user["id"], sub)  # Google verified the address, so it's the same person
        if not user:
            user = store.create_user(email, str(info.get("name") or email.split("@")[0])[:100], google_sub=sub)
            log("sign_up", user=user["id"], method="google")
        if email.lower() in admin_emails() and user["role"] != "admin":
            store.update_user(user["id"], role="admin")
            log("admin_created", user=user["id"], via="CRASHLAB_ADMIN_EMAILS")
        user = store.get_user(user["id"])
        if user["disabled_at"]:
            return RedirectResponse("/login?e=disabled", status_code=303)
        resp = sign_in(request, user, flow["n"])
        resp.delete_cookie(OAUTH_COOKIE, path="/auth/google")
        return resp


def print_setup_link(app: FastAPI) -> None:
    """On a fresh install, print the one-time link that creates the first admin."""
    if app.state.store.count_admins() == 0:
        base = public_url() or "http://127.0.0.1:" + os.environ.get("PORT", "8000")
        print(f"\n  FIRST RUN: create your admin account here (one-time link):\n  {base}/setup?token={app.state.setup_token}\n",
              flush=True)
