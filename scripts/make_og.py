"""Render web/og.png, the 1200x630 share preview, from HTML in the site's own type and colours.

    python scripts/make_og.py

The numbers come from server.site.RESULTS (the reproducible benchmark), so the image can't drift from the page.
"""
from __future__ import annotations

import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from server.site import LOGO, RESULTS  # noqa: E402

r = RESULTS
HTML = f"""<!doctype html><html><head><meta charset="utf-8">
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Archivo:wdth,wght@62..125,400..800&family=Public+Sans:wght@400..700&family=JetBrains+Mono:wght@500;600&display=block">
<style>
  * {{ box-sizing: border-box; margin: 0; }}
  body {{ width: 1200px; height: 630px; background: #f1f4f6; color: #0d1b2a; font-family: "Public Sans", sans-serif; padding: 56px 72px; position: relative; overflow: hidden; }}
  .strip {{ display: flex; border-top: 3px solid #0d1b2a; border-bottom: 1px solid #c3ccd4; font: 600 15px/1 "JetBrains Mono", monospace; letter-spacing: .06em; text-transform: uppercase; color: #56677a; }}
  .strip span {{ padding: 13px 20px 13px 0; margin-right: 20px; border-right: 1px solid #c3ccd4; }}
  .strip span:last-child {{ border: 0; }}
  .strip b {{ color: #0d1b2a; }}
  h1 {{ font-family: Archivo, sans-serif; font-stretch: 72%; font-weight: 800; font-size: 88px; line-height: .95; letter-spacing: -.02em; margin-top: 40px; max-width: 760px; }}
  .stamp {{ position: absolute; right: 84px; top: 372px; transform: rotate(-7deg); border: 4px solid #c8102e; color: #c8102e; padding: 10px 18px 12px;
           font-family: Archivo, sans-serif; font-stretch: 75%; font-weight: 800; font-size: 40px; line-height: 1; text-transform: uppercase; letter-spacing: .03em; }}
  .stamp small {{ display: block; font: 700 14px/1 "JetBrains Mono", monospace; letter-spacing: .18em; margin-bottom: 6px; }}
  .foot {{ position: absolute; left: 72px; right: 72px; bottom: 52px; display: flex; justify-content: space-between; align-items: flex-end; border-top: 1px solid #c3ccd4; padding-top: 18px; }}
  .brand {{ display: flex; align-items: center; gap: 12px; font-family: Archivo, sans-serif; font-weight: 800; font-size: 22px; letter-spacing: .05em; }}
  .brand svg {{ width: 34px; height: 34px; }}
  .nums {{ display: flex; gap: 36px; font: 600 20px "JetBrains Mono", monospace; }}
  .nums span {{ color: #56677a; font-weight: 500; font-size: 15px; display: block; margin-bottom: 4px; font-family: "Public Sans", sans-serif; }}
  .bad {{ color: #c8102e; }}
</style></head><body>
  <div class="strip"><span>Docket <b>ACL-ECOM-0002</b></span><span>Investigation report</span><span>Closed · regression saved</span></div>
  <h1>Your AI agent passed the demo. Will it survive production?</h1>
  <div class="stamp"><small>Finding</small>Duplicate refund</div>
  <div class="foot"><div class="brand">{LOGO}<span>AGENT CRASH LAB</span></div>
    <div class="nums"><div><span>Original agent</span><b class="bad">{r['v1_pass']}/{r['per_agent']}</b> passed</div><div><span>Fixed agent</span><b>{r['v2_pass']}/{r['per_agent']}</b> passed</div></div></div>
</body></html>"""


def main() -> None:
    out = ROOT / "web" / "og.png"
    with sync_playwright() as pw:
        b = pw.chromium.launch()
        p = b.new_page(viewport={"width": 1200, "height": 630})
        p.set_content(HTML, wait_until="networkidle")
        p.evaluate("document.fonts.ready")
        p.screenshot(path=str(out))
        b.close()
    print(f"wrote {out} ({out.stat().st_size // 1024} KB)")


if __name__ == "__main__":
    main()
