"""Record the product demo video from the real app.

    python scripts/record_demo.py            # writes web/media/demo.webm and web/media/demo-poster.png

Starts its own server on a throwaway database, then drives a real browser through the whole
product with on-screen captions: landing page, sign-up, a 200-episode experiment, a failure,
minimization, a saved regression test, the trace explorer, the regression suite, integrations,
and the admin console. Needs Playwright: pip install playwright && python -m playwright install chromium
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

from playwright.sync_api import Page, sync_playwright

ROOT = Path(__file__).resolve().parent.parent
MEDIA = ROOT / "web" / "media"
PORT = int(os.environ.get("DEMO_PORT", "8765"))
BASE = f"http://127.0.0.1:{PORT}"
SIZE = {"width": 1280, "height": 720}
# Throwaway accounts that exist only in the temporary database this script creates and deletes.
ADMIN = ("Priya Admin", "admin@demo.crashlab.test", "demo-admin-password-1")
USER = ("Arjun Mehta", "arjun@demo.crashlab.test", "demo-user-password-1")

CAPTION_JS = """
(() => {
  const mk = () => {
    if (document.getElementById('__cap')) return;
    const d = document.createElement('div'); d.id = '__cap';
    d.style.cssText = 'position:fixed;left:50%;bottom:76px;transform:translateX(-50%);z-index:99999;max-width:min(860px,90vw);' +
      'background:#0d1b2a;color:#f1f4f6;font:600 19px/1.35 "Public Sans",system-ui,sans-serif;padding:12px 20px 13px;' +
      'border:1px solid #2a3e52;box-shadow:0 16px 40px rgba(0,0,0,.35);opacity:0;transition:opacity .35s;pointer-events:none;text-align:center';
    document.documentElement.appendChild(d);
  };
  window.__cap = (t) => { mk(); const d = document.getElementById('__cap'); d.textContent = t; d.style.opacity = t ? '1' : '0'; };
  document.addEventListener('DOMContentLoaded', mk);
})();
"""


def cap(page: Page, text: str, hold: float = 2.6) -> None:
    page.evaluate("t => window.__cap && window.__cap(t)", text)
    page.wait_for_timeout(int(hold * 1000))


def glide(page: Page, selector: str, pause: float = 1.2) -> None:
    page.evaluate("s => document.querySelector(s).scrollIntoView({behavior: 'smooth', block: 'start'})", selector)
    page.wait_for_timeout(int(pause * 1000))


def wait_path(page: Page, fragment: str, timeout: float = 30) -> None:
    """Wait until the address contains `fragment` (simpler and sturdier than URL globs)."""
    end = time.time() + timeout
    while fragment not in page.url:
        if time.time() > end:
            raise TimeoutError(f"never reached {fragment!r}; still at {page.url}: " + page.inner_text("main")[:500])
        page.wait_for_timeout(100)


def slow_type(page: Page, selector: str, text: str) -> None:
    page.click(selector)
    page.type(selector, text, delay=45)


def compress(src: Path, dest: Path) -> None:
    """The raw browser recording is ~18 MB. A two-pass VP9 encode of mostly still UI keeps text sharp at ~3 MB, so
    the repository and every page view stay light. Needs ffmpeg on PATH, or `pip install imageio-ffmpeg`."""
    ff = shutil.which("ffmpeg")
    if not ff:
        try:
            import imageio_ffmpeg

            ff = imageio_ffmpeg.get_ffmpeg_exe()
        except ImportError:
            print("ffmpeg not found: keeping the uncompressed recording (install ffmpeg or imageio-ffmpeg)")
            shutil.copyfile(src, dest)
            return
    common = [ff, "-hide_banner", "-loglevel", "error", "-y", "-i", str(src), "-c:v", "libvpx-vp9", "-b:v", "230k",
              "-crf", "38", "-row-mt", "1", "-an"]
    with tempfile.TemporaryDirectory() as tmp:
        log = str(Path(tmp) / "pass")
        subprocess.run([*common, "-cpu-used", "4", "-pass", "1", "-passlogfile", log, "-f", "webm", os.devnull], check=True)  # noqa: S603
        subprocess.run([*common, "-cpu-used", "2", "-pass", "2", "-passlogfile", log, str(dest)], check=True)  # noqa: S603


def start_server(db: Path) -> tuple[subprocess.Popen, str]:
    env = {**os.environ, "CRASHLAB_FORM_SECRET": "demo", "CRASHLAB_RATE_AUTH_PER_MIN": "1000", "PYTHONUNBUFFERED": "1"}
    exe = Path(sys.executable).with_name("crashlab.exe" if os.name == "nt" else "crashlab")
    proc = subprocess.Popen([str(exe), "--db", str(db), "serve", "--port", str(PORT)], cwd=ROOT, env=env,  # noqa: S603
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding="utf-8")
    deadline = time.time() + 30
    while time.time() < deadline:
        line = proc.stdout.readline()
        if m := re.search(r"/setup\?token=([\w-]+)", line):
            # keep reading the server's log, or the pipe fills up and the server blocks mid-request
            threading.Thread(target=lambda: [None for _ in proc.stdout], daemon=True).start()
            return proc, m.group(1)
    proc.kill()
    raise RuntimeError("server did not print its setup link")


def main() -> None:
    MEDIA.mkdir(parents=True, exist_ok=True)
    tmp = Path(tempfile.mkdtemp(prefix="crashlab-demo-"))
    proc, token = start_server(tmp / "demo.db")
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch()
            # Off camera: the owner creates the admin account from the one-time setup link.
            setup = browser.new_context()
            sp = setup.new_page()
            sp.goto(f"{BASE}/setup?token={token}", wait_until="domcontentloaded")
            sp.fill("#name", ADMIN[0]); sp.fill("#email", ADMIN[1]); sp.fill("#password", ADMIN[2])
            sp.click("button[type=submit]", no_wait_after=True)
            wait_path(sp, "/admin")
            setup.close()

            ctx = browser.new_context(viewport=SIZE, record_video_dir=str(tmp / "video"), record_video_size=SIZE,
                                      color_scheme="light", reduced_motion="no-preference")
            ctx.add_init_script(CAPTION_JS)
            page = ctx.new_page()

            # 1. The landing page
            page.goto(wait_until="domcontentloaded", url=BASE + "/")
            page.wait_for_timeout(600)
            cap(page, "AGENT CRASH LAB: crash-test your AI agent before your customers do.", 3.2)
            cap(page, "Every crash gets an investigation. This one is real: a refund agent paid a customer twice.", 4.4)
            cap(page, "The gateway committed the refund, then timed out. The agent retried without checking.", 3.6)
            glide(page, "#how")
            cap(page, "One loop: stress, detect, explain, minimize, prevent, compare.", 3.4)
            glide(page, "#results")
            cap(page, "Measured, not claimed: the fixed agent passes 99 of 100 runs; the original passed 19.", 3.8)
            glide(page, "#pricing")
            cap(page, "Start free. Let's create an account.", 2.4)

            # 2. Sign up
            page.goto(wait_until="domcontentloaded", url=BASE + "/signup")
            cap(page, "Sign up with email, or with Google when it's configured.", 2.0)
            slow_type(page, "#name", USER[0])
            slow_type(page, "#email", USER[1])
            slow_type(page, "#password", USER[2])
            page.wait_for_timeout(400)
            page.click("form.auth-form button[type=submit]", no_wait_after=True)
            wait_path(page, "/app")
            page.wait_for_timeout(1200)
            cap(page, "Arjun's private workspace. Nothing tested yet.", 2.6)

            # 3. Run an experiment
            page.goto(wait_until="domcontentloaded", url=BASE + "/app/#/experiments/new")
            page.wait_for_timeout(1200)
            cap(page, "Pick agents and scenarios: v1 is the original agent, v2 the fixed one. 30 scenarios × 5 repeats.", 4.0)
            page.click("button[type=submit]")
            cap(page, "300 episodes run live while faults fire: timeouts, rate limits, stale data, prompt injection.", 4.5)
            page.wait_for_selector("text=Finished", timeout=120_000)
            page.wait_for_timeout(800)
            cap(page, "Green passed, red failed. Every square is one recorded episode.", 3.0)
            page.goto(wait_until="domcontentloaded", url=BASE + "/app/#/overview")
            page.wait_for_timeout(1500)
            cap(page, "v2 survived 99 of 100 crash tests. v1 survived 19.", 3.6)

            # 4. A failure, minimized and saved as a regression test
            page.goto(wait_until="domcontentloaded", url=BASE + "/app/#/failures")
            page.wait_for_timeout(1200)
            cap(page, "Failures are grouped into modes, with severity and category.", 3.0)
            page.click(".table tbody tr.link >> nth=0")
            page.wait_for_timeout(1500)
            # the poster opens the exhibit on evidence: the real duplicate-refund finding, no caption over it
            cap(page, "", 0.5)
            page.screenshot(path=str(MEDIA / "demo-poster.png"))
            cap(page, "Each failure has evidence, the violated rule, and a probable cause.", 3.4)
            page.mouse.wheel(0, 500)
            page.wait_for_timeout(800)
            if page.query_selector("#re-min"):
                page.click("#re-min")
                cap(page, "Delta debugging shrinks the failing run to the few steps that still reproduce it.", 4.0)
            if page.query_selector("#save-reg"):
                page.click("#save-reg")
                cap(page, "One click turns the failure into a permanent regression test.", 3.2)

            # 5. Trace explorer
            page.goto(wait_until="domcontentloaded", url=BASE + "/app/#/traces")
            page.wait_for_timeout(1500)
            cap(page, "The trace explorer replays any episode, step by step, on a virtual clock.", 3.6)
            page.mouse.wheel(0, 300)
            page.wait_for_timeout(1200)

            # 6. Regression suite
            page.goto(wait_until="domcontentloaded", url=BASE + "/app/#/regressions")
            page.wait_for_timeout(1400)
            cap(page, "Saved regression tests become a suite that guards every release.", 3.2)
            if page.query_selector("#run-suite"):
                page.click("#run-suite")
                page.wait_for_timeout(700)
                if page.query_selector("dialog[open] .btn.primary"):
                    page.click("dialog[open] .btn.primary")
                cap(page, "Run it in one click, or in CI with a release gate.", 4.5)

            # 7. Integrations: SDK, CLI, Claude and Gemini
            page.goto(wait_until="domcontentloaded", url=BASE + "/app/#/integrations")
            page.wait_for_timeout(1400)
            cap(page, "Test your own agent with the Python SDK, the CLI, or the REST API. Claude and Gemini agents are built in.", 4.5)

            # 8. Admin console
            page.goto(wait_until="domcontentloaded", url=BASE + "/account")
            page.wait_for_timeout(1000)
            cap(page, "Your account: sign-in methods, password, and workspace.", 2.6)
            page.click("form.signout button", no_wait_after=True)
            wait_path(page, "/login")
            page.goto(wait_until="domcontentloaded", url=BASE + "/admin/login")
            cap(page, "Admins sign in separately to manage the platform.", 2.2)
            slow_type(page, "#email", ADMIN[1])
            slow_type(page, "#password", ADMIN[2])
            page.click("form.auth-form button[type=submit]", no_wait_after=True)
            wait_path(page, "/admin")
            page.wait_for_timeout(1000)
            cap(page, "Every user, their role and access. Disable an account or send a reset link.", 4.0)

            # 9. Close
            page.goto(wait_until="domcontentloaded", url=BASE + "/")
            glide(page, ".closing", 1.5)
            cap(page, "AGENT CRASH LAB. Find failures before your customers do.", 3.6)
            cap(page, "", 0.6)

            video = page.video.path()
            ctx.close()
            browser.close()
            compress(Path(video), MEDIA / "demo.webm")
            print(f"wrote {MEDIA / 'demo.webm'} ({(MEDIA / 'demo.webm').stat().st_size / 1e6:.1f} MB)")
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    main()
