"""Static export: one self-contained HTML file with the dashboard and a full
experiment embedded - for sharing a reliability report without a server."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from urllib.parse import quote

from .experiment import run_experiment, run_regressions
from .scenarios import load_scenarios, validate_scenario

WEB = Path(__file__).resolve().parent.parent / "web"


def build_payload(agents: list[str], repeats: int) -> dict[str, Any]:
    scenarios = load_scenarios()
    exp = run_experiment(agents, scenarios, repeats=repeats, experiment_id="exp_demo2026",
                         name=f"Refund reliability suite · {' vs '.join(agents)}")
    rr = run_regressions(exp["regressions"], agents, repeats=3)
    rr["created_at"] = exp["created_at"]
    return {"experiment": exp, "scenarios": scenarios, "regression_run": rr,
            "validation": {s["scenario_id"]: dict(validate_scenario(s)) for s in scenarios}}


def render(payload: dict[str, Any]) -> str:
    html = (WEB / "index.html").read_text(encoding="utf-8")
    css = (WEB / "styles.css").read_text(encoding="utf-8")
    js = (WEB / "app.js").read_text(encoding="utf-8")
    ui = (WEB / "ui.js").read_text(encoding="utf-8")
    theme = (WEB / "theme.js").read_text(encoding="utf-8")
    data = json.dumps(payload, default=str, separators=(",", ":")).replace("</", "<\\/")
    html = html.replace('<link rel="stylesheet" href="/static/styles.css">', f"<style>\n{css}\n</style>")
    icon = "data:image/svg+xml," + quote((WEB / "favicon.svg").read_text(encoding="utf-8").strip())  # no server to serve /favicon.ico
    html = html.replace('href="/favicon.ico"', f'href="{icon}"')
    html = html.replace('<script src="/static/theme.js"></script>', f"<script>\n{theme}\n</script>")
    html = html.replace('<script src="/static/ui.js"></script>', f"<script>\n{ui}\n</script>")
    html = html.replace('<script src="/static/app.js"></script>',
                        f"<script>window.CRASHLAB_STATIC={data};</script>\n<script>\n{js}\n</script>")
    return html


def export_static(out: Path, agents: list[str] | None = None, repeats: int = 5) -> Path:
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(render(build_payload(agents or ["v1", "v2"], repeats)), encoding="utf-8")
    return out
