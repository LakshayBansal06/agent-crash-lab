/* AGENT CRASH LAB dashboard — dependency-free, works against the live API
   (crashlab serve) or a static export with embedded results. */
(() => {
  "use strict";

  const STATIC = window.CRASHLAB_STATIC || null;
  const UI = window.crashlabUI;
  const API = "/api/v1";
  const VIEWS = {};
  const S = { mode: STATIC ? "static" : "api", exp: null, scenarios: [], regRun: null, regressions: [], validation: {},
              runCache: {}, failCache: {}, temp: {} };

  // ------------------------------------------------------------ utilities
  const $ = (sel, root = document) => root.querySelector(sel);
  const esc = (v) => String(v ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const pct = (x, d = 1) => (x == null ? "—" : `${(x * 100).toFixed(d)}%`);
  const fmt1 = (x) => (x == null ? "—" : Number(x).toFixed(1));
  const secs = (ms) => `${(ms / 1000).toFixed(ms < 10000 ? 2 : 1)}s`;
  const inr = (x) => "₹" + Number(x).toLocaleString("en-IN", { maximumFractionDigits: 2 });
  const titleCase = (s) => String(s || "").toLowerCase().replace(/_/g, " ").replace(/^./, (c) => c.toUpperCase());
  const argStr = (a) => Object.entries(a || {}).map(([k, v]) => `${k}=${JSON.stringify(v)}`).join(", ");
  const json = (o) => esc(JSON.stringify(o, null, 2));
  const ICON = {
    pass: '<svg viewBox="0 0 16 16" aria-hidden="true"><path d="M3 8.5l3 3 7-7" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/></svg>',
    fail: '<svg viewBox="0 0 16 16" aria-hidden="true"><path d="M4 4l8 8M12 4l-8 8" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"/></svg>',
    flaky: '<svg viewBox="0 0 16 16" aria-hidden="true"><path d="M8 2.5l6 11H2z" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linejoin="round"/><path d="M8 7v3" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"/></svg>',
  };
  // A run whose model provider failed (outage, quota) has no verdict: grey, never red.
  const inconclusive = (r) => r.status === "INCONCLUSIVE";
  const verdictCls = (r) => (inconclusive(r) ? "skip" : r.passed ? "" : "fail");
  const verdictText = (r) => (inconclusive(r) ? "Inconclusive (model provider error)" : r.passed ? "Passed" : titleCase(r.primary_category));
  const statusHtml = (st, text) => `<span class="status ${st}">${ICON[st] || ""}${esc(text ?? st)}</span>`;

  // ---- auth: the key lives in sessionStorage (this tab only) and is sent as a Bearer header
  const KEY = "crashlab.apiKey";
  const getKey = () => { try { return sessionStorage.getItem(KEY) || ""; } catch (_) { return ""; } };
  const setKey = (k) => { try { k ? sessionStorage.setItem(KEY, k) : sessionStorage.removeItem(KEY); } catch (_) {} };
  class AuthError extends Error {}
  const authHeaders = () => (getKey() ? { authorization: `Bearer ${getKey()}` } : {});

  async function api(path, opts = {}) {
    const r = await fetch(API + path, { ...opts, headers: { "content-type": "application/json", ...authHeaders(), ...(opts.headers || {}) } });
    if (r.status === 401) throw new AuthError("API key required");
    if (!r.ok) {
      let detail = r.statusText;
      try { const j = await r.json(); detail = j.detail || detail; if (j.request_id) detail += ` (request ${j.request_id})`; } catch (_) {}
      throw new Error(`${r.status} ${detail}`);
    }
    if (r.status === 204) return null;
    return r.json();
  }

  // Server-sent events over fetch, so the Authorization header can be sent (EventSource can't).
  function streamEvents(path, handlers) {
    const ctrl = new AbortController();
    (async () => {
      try {
        const r = await fetch(API + path, { headers: authHeaders(), signal: ctrl.signal });
        if (!r.ok) throw new Error(`${r.status}`);
        const reader = r.body.getReader();
        const dec = new TextDecoder();
        let buf = "";
        for (;;) {
          const { value, done } = await reader.read();
          if (done) break;
          buf += dec.decode(value, { stream: true });
          let i;
          while ((i = buf.indexOf("\n\n")) >= 0) {
            const chunk = buf.slice(0, i); buf = buf.slice(i + 2);
            let type = "message", data = "";
            chunk.split("\n").forEach((ln) => { if (ln.startsWith("event: ")) type = ln.slice(7); else if (ln.startsWith("data: ")) data += ln.slice(6); });
            if (data && handlers[type]) handlers[type](JSON.parse(data));
          }
        }
      } catch (e) { if (e.name !== "AbortError" && handlers.error) handlers.error(e); }
    })();
    return () => ctrl.abort();
  }

  // Signed out: people go to the sign-in page; scripts that connected with an API key get the key form again.
  function signedOut(message) {
    if (getKey()) { keyForm(message); return; }
    location.href = "/login?m=expired&next=" + encodeURIComponent("/app/" + location.hash);
  }

  function keyForm(message) {
    crumbs(["Sign in"]);
    $("#nav").innerHTML = "";
    $("#page").innerHTML = `<div class="panel panel-pad" style="max-width:520px">
      <h1>Connect with an API key</h1>
      <p class="lede">${esc(message)}</p>
      <form class="form" id="key-form" style="margin-top:18px">
        <div class="field"><label for="key">API key</label><input id="key" type="password" autocomplete="off" spellcheck="false" style="width:100%" placeholder="acl_…" required>
        <div class="hint">Create one on the server with <code>crashlab keys create --scopes read,write</code>. It's kept for this browser tab only.</div></div>
        <div class="btn-row"><button class="btn primary" type="submit">Connect</button></div>
      </form></div>`;
    $("#key-form").onsubmit = async (e) => {
      e.preventDefault();
      setKey($("#key").value.trim());
      try { const me = await api("/whoami"); success(`Connected to project ${me.project_id}`); boot(); } catch (err) { setKey(""); keyForm(err instanceof AuthError ? "That key was rejected. Check it and try again." : err.message); }
    };
    UI && UI.enhancePasswords($("#page"));
    $("#key").focus();
  }

  // Feedback: every user action ends in a success or an error message. Errors stay longer and are announced.
  function toast(msg, kind = "ok") {
    document.querySelectorAll(".toast").forEach((t) => t.remove());
    const t = document.createElement("div");
    t.className = `toast ${kind}`;
    t.setAttribute("role", kind === "err" ? "alert" : "status");
    t.innerHTML = `<span aria-hidden="true" style="color:${kind === "err" ? "var(--fail)" : "var(--pass)"}">${kind === "err" ? "✕" : "✓"}</span><span>${esc(msg)}</span>`;
    document.body.appendChild(t);
    setTimeout(() => t.remove(), kind === "err" ? 7000 : 3200);
  }
  const success = (m) => toast(m, "ok");
  const failure = (m) => toast(m, "err");
  // fetch() rejects with a TypeError only when the network fails; any other TypeError is a bug and should say so
  const friendly = (e) => (e instanceof TypeError && /fetch|network|load failed/i.test(e.message)
    ? "Can't reach the server. Check your connection and that the server is running." : e.message);

  // Run a button's action: disable it while busy, then report success or the error, and always re-enable.
  async function action(btn, busyLabel, fn, okMsg) {
    const label = btn.textContent;
    btn.disabled = true; btn.textContent = busyLabel; btn.setAttribute("aria-busy", "true");
    try {
      const r = await fn();
      const m = typeof okMsg === "function" ? okMsg(r) : okMsg;
      if (m) success(m);
      return r;
    } catch (e) {
      if (e instanceof AuthError) { signedOut("Your API key expired or was revoked."); return undefined; }
      failure(friendly(e));
      return undefined;
    } finally {
      if (btn.isConnected) { btn.disabled = false; btn.textContent = label; btn.removeAttribute("aria-busy"); }
    }
  }

  async function copyText(text, what) {
    try {
      await navigator.clipboard.writeText(text);
      success(`${what} copied to the clipboard`);
    } catch (_) {
      failure(`Couldn't copy automatically. Select the ${what.toLowerCase()} and press Ctrl+C (⌘C on Mac).`);
    }
  }

  // tooltip
  const tipEl = $("#tip");
  function tipShow(html, e) {
    tipEl.innerHTML = html; tipEl.classList.add("on");
    const x = Math.min(window.innerWidth - tipEl.offsetWidth - 12, e.clientX + 14);
    const y = Math.min(window.innerHeight - tipEl.offsetHeight - 12, e.clientY + 14);
    tipEl.style.left = x + "px"; tipEl.style.top = y + "px";
  }
  const tipHide = () => tipEl.classList.remove("on");
  document.addEventListener("scroll", tipHide, true);

  // ------------------------------------------------------------ data layer
  const agents = () => (S.exp ? S.exp.config.agents : []);
  const summary = (ref) => S.exp.summaries[ref];
  const agentName = (ref) => { const a = summary(ref)?.agent; return a ? `${a.name} ${a.version}` : ref; };
  const agentShort = (ref) => { const a = summary(ref)?.agent; return a ? `v${a.version}` : ref; };
  const seriesVar = (ref) => (agents().indexOf(ref) === 0 ? "var(--s-v1)" : "var(--s-v2)");
  const runsIndex = () => (S.exp ? S.exp.runs_index || S.exp.runs || [] : []);
  const scenario = (id) => S.scenarios.find((s) => s.scenario_id === id) || S.exp?.regressions?.find((r) => r.scenario_id === id);
  const refForVersion = (ver) => agents().find((r) => summary(r)?.agent?.version === ver) || ver;

  async function getRun(id) {
    if (S.temp[id]) return S.temp[id];
    if (S.runCache[id]) return S.runCache[id];
    let r;
    if (S.mode === "static" || id.startsWith("min:")) r = (S.exp.runs || []).find((x) => x.run_id === id);
    else {
      try { r = await api(`/runs/${encodeURIComponent(id)}`); } catch (e) { if (String(e.message).startsWith("404")) r = null; else throw e; }
    }
    if (r) S.runCache[id] = r;
    return r;
  }
  async function getFailure(id) {
    if (S.failCache[id]) return S.failCache[id];
    let f;
    if (S.mode === "static") {
      f = (S.exp.failures || []).find((x) => x.failure_id === id);
      if (f) f = { ...f, reproduction: S.exp.reproductions?.[id] || null };
    } else {
      try { f = await api(`/failures/${encodeURIComponent(id)}`); } catch (e) { if (String(e.message).startsWith("404")) f = null; else throw e; }
    }
    if (f) S.failCache[id] = f;
    return f;
  }

  async function loadExperiment(id) {
    S.exp = S.mode === "static" ? STATIC.experiment : await api(`/experiments/${encodeURIComponent(id)}`);
    S.runCache = {}; S.failCache = {};
  }

  async function boot() {
    if (S.mode === "static") {
      S.exp = STATIC.experiment; S.scenarios = STATIC.scenarios; S.regRun = STATIC.regression_run;
      S.regressions = S.exp.regressions || []; S.validation = STATIC.validation || {};
    } else {
      try {
        S.me = await api("/whoami");
        S.scenarios = await api("/scenarios");
        const list = await api("/experiments");
        const done = list.find((e) => e.status === "COMPLETED");
        if (done) await loadExperiment(done.experiment_id);
        S.regressions = await api("/regressions");
        try { S.regRun = await api("/regression-suites/default/latest"); } catch (_) { S.regRun = null; }
      } catch (e) {
        if (e instanceof AuthError) { signedOut("This server requires an API key."); return; }
        $("#page").innerHTML = `<div class="empty"><h3>Can't reach the Crash Lab API</h3><p>${esc(e.message)}. Start it with <code>crashlab serve</code>.</p></div>`;
        return;
      }
    }
    const m = $("#mode");
    m.className = "mode-pill" + (S.mode === "static" ? " static" : "");
    m.innerHTML = S.mode === "static" ? "<i></i>Static report" : "<i></i>Connected to API";
    renderFooter();
    if (!S.booted) {
      window.addEventListener("hashchange", route);
      S.booted = true;
      if (UI) {
        const tools = $("#top-tools");
        tools.innerHTML = "";
        tools.append(UI.searchButton(() => UI.openSearch(searchItems, { placeholder: "Search pages, scenarios, failures, runs", onGo: (u) => (location.hash = u) })), UI.themeButton());
        if (S.mode === "api" && S.me && S.me.user) {
          const u = S.me.user;
          const acct = document.createElement("a");
          acct.className = "account-chip"; acct.href = "/account";
          acct.title = `${u.name} (${u.email})`;
          acct.innerHTML = `<span class="avatar" aria-hidden="true">${esc(u.name.slice(0, 1).toUpperCase())}</span><span class="account-name">${esc(u.name)}</span>`;
          acct.setAttribute("aria-label", `Your account: ${u.name}`);
          tools.append(acct);
          if (u.role === "admin") {
            const adm = document.createElement("a");
            adm.className = "icon-btn"; adm.href = "/admin"; adm.textContent = "Admin";
            tools.append(adm);
          }
        } else if (S.mode === "api" && getKey()) {
          const out = document.createElement("button");
          out.type = "button"; out.className = "icon-btn"; out.textContent = "Sign out";
          out.addEventListener("click", async () => {
            if (await UI.confirm({ title: "Sign out of this dashboard?", body: "Your API key will be removed from this browser tab. You'll need to paste it again to reconnect.", confirmLabel: "Sign out" })) {
              setKey(""); success("Signed out"); location.reload();
            }
          });
          tools.append(out);
        }
        UI.floaters();
        new MutationObserver(() => { UI.enhanceCode($("#page")); UI.enhancePasswords($("#page")); }).observe($("#page"), { childList: true, subtree: true });
      }
    }
    route();
  }

  // ------------------------------------------------------------ navigation
  const NAV = [
    ["Workspace", [["overview", "Overview"], ["experiments", "Experiments"]]],
    ["Testing", [["datasets", "Datasets"], ["live", "Live runs", "api"]]],
    ["Analysis", [["failures", "Failures"], ["traces", "Trace explorer"], ["results", "Reliability report"]]],
    ["Engineering", [["regressions", "Regression tests"], ["integrations", "Integrations"]]],
  ];
  const TITLES = {
    overview: ["Overview", "Reliability verdict, episode wall, scorecard and top failure modes for the current experiment."],
    experiments: ["Experiments", "Every experiment: agents, scenarios, repeats and success rates."],
    live: ["Live runs", "Watch episodes, faults and failures stream in while an experiment runs."],
    datasets: ["Scenario dataset", "Versioned test scenarios with tasks, faults, expected state and invariants."],
    failures: ["Failures", "Failure modes grouped by root cause, with evidence and minimal reproductions."],
    traces: ["Trace explorer", "Step through any episode: tool calls, injected faults, state changes and invariant breaches."],
    results: ["Reliability report", "Paired comparison of agent versions with confidence intervals and per-scenario results."],
    regressions: ["Regression tests", "Tests generated from discovered failures, and the release gate they enforce."],
    integrations: ["Integrations", "Connect an agent with the Python SDK, CLI, CI pipeline or REST API."],
  };
  function setMeta(title, desc) {
    document.title = `${title} · AGENT CRASH LAB`;
    let m = document.querySelector('meta[name="description"]');
    if (!m) { m = document.createElement("meta"); m.name = "description"; document.head.appendChild(m); }
    if (desc) m.content = desc;
  }
  function renderNav(active) {
    const counts = {
      failures: S.exp ? (S.exp.failure_modes || []).length : "",
      datasets: S.scenarios.length || "",
      regressions: (S.regressions || []).length || "",
    };
    $("#nav").innerHTML = NAV.map(([g, items]) => `<div class="nav-group">${g}</div>` + items.filter(([, , only]) => !only || only === S.mode).map(([k, label]) =>
      `<a href="#/${k}" ${active === k ? 'aria-current="page"' : ""}><span>${label}</span><span class="count">${counts[k] ?? ""}</span></a>`).join("")).join("");
    const e = S.exp;
    $("#side-foot").innerHTML = e ? `Viewing <b>${esc(e.name)}</b><br>${esc(e.created_at?.replace("T", " ").replace("Z", " UTC") || "")}` :
      "No experiment yet";
  }
  function crumbs(parts) {
    $("#crumbs").innerHTML = parts.map((p, i) => (i === parts.length - 1 ? `<b>${esc(p)}</b>` : esc(p))).join(" / ");
  }

  async function route() {
    tipHide();
    closeMenu();
    const [path, query] = location.hash.replace(/^#\/?/, "").split("?");
    const [view, ...rest] = path.split("/");
    const id = rest.map(decodeURIComponent).join("/");
    const v = view || "overview";
    renderNav(v === "live" && id ? "live" : v);
    const page = $("#page");
    const known = Object.prototype.hasOwnProperty.call(VIEWS, v) && v !== "notfound";
    const [t, d] = TITLES[v] || ["Page not found", "This page doesn't exist."];
    setMeta(t, d);
    renderFooter();
    if (!known) { VIEWS.notfound(page); page.focus({ preventScroll: true }); window.scrollTo(0, 0); return; }
    const needsExp = ["overview", "results", "failures", "traces"];
    if (needsExp.includes(v) && !S.exp) { crumbs([titleCase(v)]); page.innerHTML = emptyExperiment(); return; }
    try {
      const fn = VIEWS[v];
      page.innerHTML = `<div class="skeleton" role="status" aria-live="polite"><span style="position:absolute;width:1px;height:1px;overflow:hidden;clip:rect(0 0 0 0)">Loading ${esc(t)}…</span>
        <i class="h"></i><i style="width:80%"></i><i style="width:62%"></i><i class="box"></i><i style="width:70%"></i></div>`;
      await fn(page, id, new URLSearchParams(query || ""));
    } catch (e) {
      if (e instanceof AuthError) { signedOut("Your API key expired or was revoked."); return; }
      if (e instanceof NotFound) { VIEWS.notfound(page, e.message); return; }
      console.error(e);
      setMeta("Error", null);
      page.innerHTML = `<div class="empty" role="alert"><h3>This page couldn't load</h3><p>${esc(friendly(e))}</p>
        <div class="btn-row" style="justify-content:center"><button class="btn primary" id="retry">Try again</button><a class="btn" href="#/overview">Go to overview</a></div></div>`;
      $("#retry").onclick = route;
    }
    page.focus({ preventScroll: true });
    window.scrollTo(0, 0);
  }

  function emptyExperiment() {
    return `<div class="empty"><h3>No experiments yet</h3><p>Run your first crash test to see reliability results here.</p>
      ${S.mode === "api" ? '<p><a class="btn primary" href="#/experiments/new">Start an experiment</a></p>' : ""}
      <p class="sub">Or from a terminal: <code>crashlab run --agents v1 v2 --repeats 5</code></p></div>`;
  }

  class NotFound extends Error {}

  // ------------------------------------------------------------ mobile menu
  const side = $("#side"), scrim = $("#scrim"), menuBtn = $("#menu-btn");
  function openMenu() {
    side.classList.add("open"); scrim.hidden = false; menuBtn.setAttribute("aria-expanded", "true");
    menuBtn.setAttribute("aria-label", "Close menu");
    side.querySelector("a")?.focus();
  }
  function closeMenu() {
    if (!side.classList.contains("open")) return;
    side.classList.remove("open"); scrim.hidden = true; menuBtn.setAttribute("aria-expanded", "false");
    menuBtn.setAttribute("aria-label", "Open menu");
  }
  menuBtn.onclick = () => (side.classList.contains("open") ? (closeMenu(), menuBtn.focus()) : openMenu());
  scrim.onclick = closeMenu;
  document.addEventListener("keydown", (e) => { if (e.key === "Escape" && side.classList.contains("open")) { closeMenu(); menuBtn.focus(); } });
  side.addEventListener("click", (e) => { if (e.target.closest("a")) closeMenu(); });

  function searchItems() {
    const items = Object.entries(TITLES).filter(([k]) => k !== "live" || S.mode === "api")
      .map(([k, [t, d]]) => ({ title: t, subtitle: "Page", text: d, url: `#/${k}`, boost: 3 }));
    S.scenarios.forEach((s) => items.push({ title: s.title, subtitle: `Scenario ${s.scenario_id} · ${titleCase(s.category)}`,
      text: `${s.scenario_id} ${s.task.user_message} ${(s.tags || []).join(" ")} ${s.faults.map((f) => f.type).join(" ")}`, url: `#/datasets/${s.scenario_id}`,
      keys: [s.scenario_id, s.scenario_id.slice(-4)], boost: 2 }));
    if (S.exp) {
      (S.exp.failure_modes || []).forEach((m) => items.push({ title: m.title, subtitle: `Failure · ${titleCase(m.category)} · v${m.agent_version} · ${m.occurrences} episodes`,
        text: `${m.category} ${m.scenarios.join(" ")}`, url: modeHref(m).slice(1), boost: 2 }));
      runsIndex().filter((r) => !r.passed).forEach((r) => items.push({ title: `${r.scenario_id.slice(-4)} · ${agentShort(r.agent_ref)} · repeat ${r.repeat + 1}`,
        subtitle: `Failed run · ${titleCase(r.primary_category)}`, text: `${r.run_id} ${r.scenario_title || ""} trace`, url: `#/traces/${encodeURIComponent(r.run_id)}`, keys: [r.run_id] }));
    }
    return items;
  }

  function renderFooter() {
    const year = new Date().getFullYear();
    $("#foot").innerHTML = `<span>© ${year} AGENT CRASH LAB · v${esc(S.exp?.__version || "0.1.0")}</span>
      <nav aria-label="Footer">${S.mode === "api" ? '<a href="/">Public site</a>' : ""}<a href="#/overview">Overview</a><a href="#/datasets">Scenarios</a><a href="#/regressions">Regression tests</a><a href="#/integrations">Integrations</a>${S.mode === "api" ? '<a href="/docs" target="_blank" rel="noopener">API reference</a>' : ""}</nav>`;
  }

  VIEWS.notfound = (page, what) => {
    crumbs(["Not found"]);
    setMeta("Page not found", "This page doesn't exist.");
    page.innerHTML = `<div class="notfound"><div class="code" aria-hidden="true">404</div>
      <h1 style="margin-top:12px">${what ? esc(what) : "This page doesn't exist"}</h1>
      <p class="lede">The link may be out of date, or the experiment it pointed to was run on another server. Pick a destination below.</p>
      <div class="btn-row" style="margin-top:18px"><a class="btn primary" href="#/overview">Go to overview</a><a class="btn" href="#/failures">Browse failures</a><a class="btn" href="#/traces">Open trace explorer</a></div></div>`;
  };

  // ------------------------------------------------------------ charts
  function lineChart({ series, xLabels, yMax = 1, yFmt = (v) => pct(v, 0), height = 250, title, width = 420 }) {
    const W = width, H = height, m = { l: 44, r: 92, t: 14, b: 30 };
    const iw = W - m.l - m.r, ih = H - m.t - m.b;
    const n = xLabels.length;
    const x = (i) => m.l + (n === 1 ? iw / 2 : (i * iw) / (n - 1));
    const y = (v) => m.t + ih - (v / yMax) * ih;
    const ticks = [0, 0.25, 0.5, 0.75, 1].map((t) => t * yMax);
    let s = `<svg class="chart" viewBox="0 0 ${W} ${H}" role="img" aria-label="${esc(title)}">`;
    s += `<g class="grid">${ticks.map((t) => `<line x1="${m.l}" x2="${W - m.r}" y1="${y(t)}" y2="${y(t)}"/>`).join("")}</g>`;
    s += ticks.map((t) => `<text x="${m.l - 8}" y="${y(t) + 4}" text-anchor="end">${yFmt(t)}</text>`).join("");
    s += xLabels.map((l, i) => `<text x="${x(i)}" y="${H - 8}" text-anchor="middle">${esc(l)}</text>`).join("");
    const placed = [];
    series.forEach((se) => {
      const d = se.values.map((v, i) => `${i ? "L" : "M"}${x(i).toFixed(1)},${y(v).toFixed(1)}`).join("");
      s += `<path d="${d}" fill="none" stroke="${se.color}" stroke-width="2" stroke-linejoin="round" stroke-linecap="round"/>`;
      s += se.values.map((v, i) => `<circle cx="${x(i)}" cy="${y(v)}" r="4" fill="${se.color}" stroke="var(--surface)" stroke-width="2"/>`).join("");
      let ly = y(se.values[n - 1]) + 4;
      placed.forEach((p) => { if (Math.abs(p - ly) < 14) ly = p + (ly >= p ? 14 : -14); });
      placed.push(ly);
      s += `<text class="lbl" x="${x(n - 1) + 10}" y="${ly}">${esc(se.name)} ${yFmt(se.values[n - 1])}</text>`;
    });
    s += `<line class="xh" x1="0" x2="0" y1="${m.t}" y2="${m.t + ih}" stroke="var(--line)" stroke-width="1" visibility="hidden"/>`;
    s += `<rect class="hit" x="${m.l - 10}" y="${m.t}" width="${iw + 20}" height="${ih}" fill="transparent"/>`;
    s += "</svg>";
    const wrap = document.createElement("div");
    wrap.innerHTML = s;
    const svg = wrap.firstChild;
    const hit = svg.querySelector(".hit"), xh = svg.querySelector(".xh");
    hit.addEventListener("mousemove", (e) => {
      const pt = svg.createSVGPoint(); pt.x = e.clientX; pt.y = e.clientY;
      const p = pt.matrixTransform(svg.getScreenCTM().inverse());
      const i = Math.max(0, Math.min(n - 1, Math.round(((p.x - m.l) / iw) * (n - 1))));
      xh.setAttribute("x1", x(i)); xh.setAttribute("x2", x(i)); xh.setAttribute("visibility", "visible");
      tipShow(`<div class="t">${esc(xLabels[i])}</div>` + series.map((se) =>
        `<div class="r"><span><span class="swatch" style="display:inline-block;background:${se.color}"></span> ${esc(se.name)}</span><b>${yFmt(se.values[i])}</b></div>`).join(""), e);
    });
    hit.addEventListener("mouseleave", () => { xh.setAttribute("visibility", "hidden"); tipHide(); });
    return svg;
  }

  function groupedBars({ groups, series, max = 100 }) {
    // horizontal bars: one row per group, one bar per series
    const W = 460, rowH = 16 * series.length + 18, m = { l: 122, r: 44, t: 6 };
    const H = m.t + groups.length * rowH + 22;
    const iw = W - m.l - m.r;
    const x = (v) => (v / max) * iw;
    let s = `<svg class="chart" viewBox="0 0 ${W} ${H}" role="img" aria-label="Score by dimension">`;
    s += `<g class="grid">${[0, 25, 50, 75, 100].map((t) => `<line x1="${m.l + x(t)}" x2="${m.l + x(t)}" y1="${m.t}" y2="${H - 22}"/>`).join("")}</g>`;
    s += [0, 50, 100].map((t) => `<text x="${m.l + x(t)}" y="${H - 6}" text-anchor="middle">${t}</text>`).join("");
    groups.forEach((g, gi) => {
      const gy = m.t + gi * rowH;
      s += `<text class="lbl" x="${m.l - 12}" y="${gy + rowH / 2 + 2}" text-anchor="end">${esc(g.label)}</text>`;
      series.forEach((se, si) => {
        const v = g.values[si];
        const by = gy + 6 + si * 16;
        if (v == null) { s += `<text x="${m.l + 4}" y="${by + 10}">n/a</text>`; return; }
        const w = Math.max(2, x(v));
        s += `<path d="M${m.l},${by} h${w - 4} q4,0 4,4 v4 q0,4 -4,4 h-${w - 4} z" fill="${se.color}" data-g="${gi}" data-s="${si}" class="bar"/>`;
        s += `<text class="val" x="${m.l + w + 6}" y="${by + 10}">${fmt1(v)}</text>`;
      });
    });
    s += "</svg>";
    const wrap = document.createElement("div"); wrap.innerHTML = s;
    const svg = wrap.firstChild;
    svg.querySelectorAll(".bar").forEach((b) => {
      b.addEventListener("mousemove", (e) => {
        const g = groups[+b.dataset.g];
        tipShow(`<div class="t">${esc(g.label)}</div>` + series.map((se, i) =>
          `<div class="r"><span>${esc(se.name)}</span><b>${fmt1(g.values[i])}</b></div>`).join(""), e);
      });
      b.addEventListener("mouseleave", tipHide);
    });
    return svg;
  }

  function legend(series) {
    return `<div class="legend">${series.map((s) => `<span><i class="swatch" style="background:${s.color}"></i>${esc(s.name)}</span>`).join("")}</div>`;
  }

  // ------------------------------------------------------------ views

  // ------------------------------------------------------------- Overview
  VIEWS.overview = async (page) => {
    crumbs(["Workspace", "Overview"]);
    const ag = agents();
    const base = ag[0], cand = ag[ag.length - 1];
    const sb = summary(base), sc = summary(cand);
    const runs = runsIndex();
    const total = runs.length;
    const failures = runs.filter((r) => !r.passed).length;
    const modes = S.exp.failure_modes || [];
    const repro = Object.values(S.exp.reproductions || {});
    const regs = S.exp.regressions || [];
    let avgRed = null;
    const rs = S.exp.reproduction_summary;
    if (!repro.length && rs && rs.count) avgRed = `${rs.original_avg.toFixed(1)} → ${rs.minimized_avg.toFixed(1)}`;
    if (repro.length) {
      const o = repro.reduce((a, r) => a + r.original_steps, 0), mm = repro.reduce((a, r) => a + r.minimized_steps, 0);
      avgRed = `${(o / repro.length).toFixed(1)} → ${(mm / repro.length).toFixed(1)}`;
    }
    const verdict = ag.length > 1
      ? `<span class="agent-v2">${esc(agentName(cand))}</span> survived ${sc.successes} of ${sc.episodes} crash tests. <span class="agent-v1">${esc(agentShort(base))}</span> survived ${sb.successes}.`
      : `<span class="agent-v2">${esc(agentName(cand))}</span> survived ${sc.successes} of ${sc.episodes} crash tests.`;

    page.innerHTML = `
      <h1 class="verdict">${verdict}</h1>
      <p class="lede">${S.exp.config.scenario_ids.length} scenarios, each run ${S.exp.config.repeats} times with seeded fault injection${S.exp.created_at ? `, on <time datetime="${esc(S.exp.created_at)}">${esc(new Date(S.exp.created_at).toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" }))}</time>` : ""}.
        Every cell below is one episode; a dark foot marks an episode where a fault fired. Select any cell to open its trace.</p>
      <div class="wall" id="wall"></div>
      <section class="section">
        <div class="section-head"><div><h2>The reliability loop for this experiment</h2>
        <p>Every failure is explained, shrunk to a minimal reproduction and turned into a test that guards the next release.</p></div></div>
        <div class="loop">
          ${[["Stress", total, "episodes under faults, injections and races", "#/results"],
             ["Detect", failures, "episodes violated a rule or the goal", "#/failures"],
             ["Explain", modes.length, "distinct failure modes with evidence", "#/failures"],
             ["Minimize", avgRed || "—", "average steps, original → minimal", "#/failures"],
             ["Prevent", regs.length, "regression tests generated", "#/regressions"],
             ["Compare", ag.length > 1 ? `${(S.exp.comparison.success_delta.delta * 100).toFixed(0)} pts` : "—", ag.length > 1 ? `success gain, ${agentShort(cand)} over ${agentShort(base)}` : "add a second agent", "#/results"],
          ].map(([v, f, w, href], i) => `<a href="${href}"><div class="n">Step ${i + 1}</div><div class="verb">${v}</div><div class="fig">${esc(f)}</div><div class="what">${w}</div></a>`).join("")}
        </div>
      </section>
      <section class="section grid-3-2">
        <div class="panel panel-pad">
          <div class="section-head"><div><h2>Scorecard</h2><p>Transparent per-dimension scores. An episode passes only when no critical or high-severity check fails.</p></div></div>
          <div class="table-wrap"><table class="table" id="scorecard"></table></div>
        </div>
        <div class="panel panel-pad">
          <h2>Consistency across repeated attempts</h2>
          <p class="sub" style="margin:4px 0 10px">pass^k: the chance that all k attempts at a scenario succeed. A flaky agent decays as k grows.</p>
          ${legend(ag.map((r) => ({ name: agentShort(r), color: seriesVar(r) })))}
          <div id="passk"></div>
          <div class="table-wrap" style="margin-top:14px"><table class="table">
            <thead><tr><th>Agent</th><th class="num">pass@${sc.pass_at_k.length}</th><th class="num">pass^${sc.pass_hat_k.length}</th><th class="num">All-pass scenarios</th></tr></thead>
            <tbody>${ag.map((r) => { const s = summary(r); return `<tr><td>${esc(agentShort(r))}</td><td class="num">${pct(s.pass_at_k.at(-1), 0)}</td><td class="num">${pct(s.pass_hat_k.at(-1), 0)}</td><td class="num">${pct(s.strict_consistency, 0)}</td></tr>`; }).join("")}</tbody></table></div>
          <p class="sub" style="margin-top:10px">pass@k asks whether an agent can do the task at all; pass^k asks whether you can rely on it. The gap between them is flakiness.</p>
        </div>
      </section>
      <section class="section grid-2">
        <div class="panel panel-pad">
          <h2>Score by dimension</h2>
          <p class="sub" style="margin:4px 0 10px">Mean across all episodes, 0–100. Fault recovery is scored only where a fault fired.</p>
          ${legend(ag.map((r) => ({ name: agentShort(r), color: seriesVar(r) })))}
          <div id="dims"></div>
        </div>
        <div class="panel panel-pad">
          <div class="section-head"><div><h2>Top failure modes</h2></div><a class="btn" href="#/failures">All failures</a></div>
          <table class="table" id="topmodes"></table>
        </div>
      </section>`;

    renderWall($("#wall"));
    $("#scorecard").innerHTML = scorecardTable();
    $("#passk").appendChild(lineChart({
      title: "pass^k by agent", xLabels: sc.pass_hat_k.map((_, i) => `k=${i + 1}`),
      series: ag.map((r) => ({ name: agentShort(r), color: seriesVar(r), values: summary(r).pass_hat_k })),
    }));
    const labels = S.exp.dimension_labels;
    $("#dims").appendChild(groupedBars({
      groups: Object.keys(labels).map((d) => ({ label: labels[d], values: ag.map((r) => summary(r).dimensions[d]) })),
      series: ag.map((r) => ({ name: agentShort(r), color: seriesVar(r) })),
    }));
    $("#topmodes").innerHTML = `<thead><tr><th>Severity</th><th>Failure</th><th class="num">Episodes</th></tr></thead><tbody>` +
      modes.slice(0, 7).map((m) => `<tr class="link" data-href="${modeHref(m)}"><td>${sev(m.severity)}</td><td>${esc(m.title)}<div class="sub">${esc(agentShort(refForVersion(m.agent_version)))} · ${m.scenarios.length} scenario${m.scenarios.length > 1 ? "s" : ""}</div></td><td class="num">${esc(m.occurrences)}</td></tr>`).join("") +
      "</tbody>";
    bindRowLinks(page);
  };

  const sev = (s) => `<span class="sev ${esc(s)}">${esc(titleCase(s))}</span>`;
  function modeHref(m) {
    const cl = (S.exp.clusters || []).find((c) => c.signature === m.clusters[0]);
    return cl ? `#/failures/${encodeURIComponent(cl.representative_failure_id)}` : "#/failures";
  }
  function bindRowLinks(root) {
    root.querySelectorAll("tr.link[data-href]").forEach((tr) => {
      tr.tabIndex = 0;
      tr.addEventListener("click", () => (location.hash = tr.dataset.href));
      tr.addEventListener("keydown", (e) => { if (e.key === "Enter") location.hash = tr.dataset.href; });
    });
  }

  function renderWall(root) {
    const ag = agents();
    const runs = runsIndex();
    const sids = S.exp.config.scenario_ids;
    root.innerHTML = ag.map((ref) => {
      const s = summary(ref);
      const groups = sids.map((sid) => {
        const rs = runs.filter((r) => r.agent_ref === ref && r.scenario_id === sid).sort((a, b) => a.repeat - b.repeat);
        return `<div class="wall-group">${rs.map((r) => `<button class="cell ${verdictCls(r)} ${r.faults_activated?.length ? "faulted" : ""}" data-run="${esc(r.run_id)}" aria-label="${esc(`${esc(sid)} repeat ${esc(r.repeat + 1)}: ${r.passed ? "passed" : "failed"}`)}"></button>`).join("")}</div>`;
      }).join("");
      return `<div class="wall-row"><div class="wall-label"><i class="swatch" style="background:${seriesVar(ref)}"></i>${esc(agentShort(ref))}</div>
        <div class="wall-cells">${groups}</div><div class="wall-score">${pct(s.success_rate, 0)}<small>${esc(s.successes)}/${esc(s.episodes)} passed</small></div></div>`;
    }).join("") + `<div class="wall-legend"><span><i class="k" style="background:var(--pass)"></i>Passed</span><span><i class="k" style="background:var(--fail)"></i>Failed</span><span><i class="k" style="background:var(--raised);box-shadow:inset 0 -4px 0 var(--signal)"></i>Fault fired</span><span>Grouped by scenario, ${S.exp.config.repeats} repeats each</span></div>`;
    const byId = Object.fromEntries(runs.map((r) => [r.run_id, r]));
    if (!S.wallPlayed && !matchMedia("(prefers-reduced-motion: reduce)").matches) {
      S.wallPlayed = true; // once per visit: the episodes fill in, then stay put
      root.querySelectorAll(".cell").forEach((c, i) => (c.style.animationDelay = `${Math.min(i * 3, 700)}ms`));
      root.classList.add("animate");
    }
    root.querySelectorAll(".cell").forEach((c) => {
      const r = byId[c.dataset.run];
      c.addEventListener("click", () => (location.hash = `#/traces/${encodeURIComponent(r.run_id)}`));
      c.addEventListener("mousemove", (e) => tipShow(`<div class="t">${esc(r.scenario_title || r.scenario_id)}</div>
        <div class="r"><span>${esc(r.scenario_id)} · repeat ${esc(r.repeat + 1)}</span></div>
        <div class="r"><span>Result</span><b>${esc(verdictText(r))}</b></div>
        <div class="r"><span>Score</span><b>${fmt1(r.score)}</b></div>
        <div class="r"><span>Steps · time</span><b>${esc(r.steps)} · ${secs(r.duration_ms)}</b></div>`, e));
      c.addEventListener("mouseleave", tipHide);
    });
  }

  function scorecardTable() {
    const ag = agents();
    const labels = S.exp.dimension_labels;
    const two = ag.length > 1;
    const head = `<thead><tr><th>Metric</th>${ag.map((r) => `<th class="num"><span class="swatch" style="display:inline-block;background:${seriesVar(r)};margin-right:6px"></span>${esc(agentShort(r))}</th>`).join("")}${two ? '<th class="num">Change</th>' : ""}</tr></thead>`;
    const row = (label, vals, f, higherBetter = true, sub = null) => {
      let d = "";
      if (two && vals[0] != null && vals[1] != null) {
        const diff = vals[1] - vals[0];
        const good = higherBetter ? diff > 0 : diff < 0;
        const shown = f === pct || String(f(0.5)).endsWith("%") ? `${(Math.abs(diff) * 100).toFixed(1)} pts` : f(Math.abs(diff));
        d = `<td class="num ${Math.abs(diff) < 1e-9 ? "" : good ? "delta-good" : "delta-bad"}">${diff >= 0 ? "+" : "−"}${shown}</td>`;
      }
      return `<tr><td>${esc(label)}${sub ? `<div class="ci">${sub}</div>` : ""}</td>${vals.map((v) => `<td class="num">${f(v)}</td>`).join("")}${d}</tr>`;
    };
    const S_ = ag.map(summary);
    return head + "<tbody>" +
      row("Reliability score", S_.map((s) => s.reliability_score), fmt1) +
      `<tr><td>Task success rate<div class="ci">95% Wilson interval</div></td>${S_.map((s) => `<td class="num">${pct(s.success_rate)}<div class="ci">${pct(s.success_ci[0], 0)}–${pct(s.success_ci[1], 0)}</div></td>`).join("")}${two ? `<td class="num delta-good">+${(S.exp.comparison.success_delta.delta * 100).toFixed(1)} pts</td>` : ""}</tr>` +
      row(`pass^${S_[0].pass_hat_k.length} (all attempts succeed)`, S_.map((s) => s.pass_hat_k.at(-1)), (v) => pct(v)) +
      Object.keys(labels).map((d) => row(labels[d], S_.map((s) => s.dimensions[d]), fmt1)).join("") +
      row("Duplicate side-effect rate", S_.map((s) => s.rates.duplicate_side_effect), (v) => pct(v), false) +
      row("Attack success rate", S_.map((s) => s.rates.security_attack_success), (v) => pct(v), false) +
      row("False completion rate", S_.map((s) => s.rates.false_completion), (v) => pct(v), false) +
      row("P95 latency (virtual)", S_.map((s) => s.latency_ms.p95), (v) => secs(v), false) +
      row("Cost per successful task", S_.map((s) => s.cost_inr.per_success), (v) => (v == null ? "—" : inr(v)), false, "simulated token pricing") +
      "</tbody>";
  }

  // ------------------------------------------------------------- Results
  VIEWS.results = async (page) => {
    crumbs(["Analysis", "Reliability report"]);
    const ag = agents(), cmp = S.exp.comparison;
    const runs = runsIndex();
    const sids = S.exp.config.scenario_ids;
    page.innerHTML = `
      <h1>Reliability report</h1>
      <p class="lede">${esc(S.exp.name)}. Versions are compared on identical scenarios and seeds, so every difference is paired.</p>
      ${cmp ? `<section class="section panel panel-pad">
        <h2>${esc(agentShort(cmp.candidate))} compared with ${esc(agentShort(cmp.baseline))}</h2>
        <dl class="kv" style="margin-top:12px">
          <dt>Success rate change</dt><dd><b>${cmp.success_delta.delta >= 0 ? "+" : ""}${(cmp.success_delta.delta * 100).toFixed(1)} points</b> <span class="sub">95% paired bootstrap CI ${(cmp.success_delta.ci_low * 100).toFixed(1)} to ${(cmp.success_delta.ci_high * 100).toFixed(1)}</span></dd>
          <dt>Score change</dt><dd>${cmp.score_delta.delta >= 0 ? "+" : ""}${cmp.score_delta.delta.toFixed(1)} <span class="sub">CI ${cmp.score_delta.ci_low.toFixed(1)} to ${cmp.score_delta.ci_high.toFixed(1)}</span></dd>
          <dt>Paired episodes</dt><dd>${cmp.paired_episodes}: ${cmp.discordant.fixed} fixed, ${cmp.discordant.broken} newly broken <span class="sub">exact McNemar p = ${cmp.mcnemar_p.toExponential(1)}</span></dd>
          <dt>Scenarios improved</dt><dd>${cmp.scenarios_improved.length ? cmp.scenarios_improved.map((s) => `<a href="#/datasets/${esc(s)}">${esc(s.slice(-4))}</a>`).join(", ") : "none"}</dd>
          <dt>Scenarios regressed</dt><dd>${cmp.scenarios_regressed.length ? cmp.scenarios_regressed.map((s) => `<a href="#/datasets/${esc(s)}">${esc(s.slice(-4))}</a>`).join(", ") : "none"}</dd>
        </dl>
        <p class="sub" style="margin-top:12px">Read the trade-offs as well as the headline: compare latency and cost below before declaring a winner.</p>
      </section>` : ""}
      <section class="section panel panel-pad"><h2>Core metrics</h2><div class="table-wrap" style="margin-top:8px"><table class="table">${scorecardTable()}</table></div></section>
      <section class="section panel panel-pad">
        <div class="section-head"><div><h2>Results by scenario</h2><p>Each dot is one repeat. Select a dot to open the trace.</p></div></div>
        <div class="table-wrap"><table class="table matrix"><thead><tr><th>Scenario</th><th>Category</th>
          ${ag.map((r) => `<th>${esc(agentShort(r))}</th><th class="num">pass^${S.exp.config.repeats}</th>`).join("")}</tr></thead><tbody>
          ${sids.map((sid) => {
            const sc = scenario(sid);
            return `<tr><td><a href="#/datasets/${esc(sid)}">${esc(sc?.title || sid)}</a><div class="sub">${esc(sid)}</div></td><td><span class="cat">${esc(titleCase(sc?.category))}</span></td>` +
              ag.map((ref) => {
                const rs = runs.filter((r) => r.agent_ref === ref && r.scenario_id === sid).sort((a, b) => a.repeat - b.repeat);
                const ps = summary(ref).per_scenario[sid];
                return `<td class="dots">${rs.map((r) => `<button class="dot ${verdictCls(r)}" data-run="${esc(r.run_id)}" aria-label="repeat ${esc(r.repeat + 1)} ${r.passed ? "passed" : "failed"}"></button>`).join("")}</td><td class="num">${pct(ps.pass_hat_k.at(-1), 0)}</td>`;
              }).join("") + "</tr>";
          }).join("")}</tbody></table></div>
      </section>`;
    page.querySelectorAll(".dot").forEach((d) => {
      const r = runs.find((x) => x.run_id === d.dataset.run);
      d.addEventListener("click", () => (location.hash = `#/traces/${encodeURIComponent(r.run_id)}`));
      d.addEventListener("mousemove", (e) => tipShow(`<div class="t">Repeat ${esc(r.repeat + 1)} · seed ${esc(r.seed)}</div><div class="r"><span>${r.passed ? "Passed" : esc(titleCase(r.primary_category))}</span><b>${fmt1(r.score)}</b></div>`, e));
      d.addEventListener("mouseleave", tipHide);
    });
  };

  // ------------------------------------------------------------- Failures
  VIEWS.failures = async (page, id) => {
    if (id) return failureDetail(page, id);
    crumbs(["Analysis", "Failures"]);
    const modes = S.exp.failure_modes || [];
    const byVer = {};
    modes.forEach((m) => (byVer[m.agent_version] ||= []).push(m));
    page.innerHTML = `<h1>Failures</h1>
      <p class="lede">Failed checks are grouped into failure modes. Downstream effects, such as an unmet goal caused by a duplicate refund, are attached to the root failure that explains them.</p>
      ${Object.entries(byVer).map(([ver, ms]) => `<section class="section panel">
        <div class="panel-pad" style="padding-bottom:6px"><h2><span class="swatch" style="display:inline-block;background:${seriesVar(refForVersion(ver))};margin-right:8px"></span>${esc(agentName(refForVersion(ver)))}</h2>
        <p class="sub">${ms.length} failure mode${ms.length > 1 ? "s" : ""} across ${ms.reduce((a, m) => a + m.occurrences, 0)} failed checks groups</p></div>
        <div class="table-wrap"><table class="table"><thead><tr><th>Severity</th><th>Failure mode</th><th>Category</th><th>Scenarios</th><th class="num">Episodes</th></tr></thead><tbody>
        ${ms.map((m) => `<tr class="link" data-href="${modeHref(m)}"><td>${sev(m.severity)}</td><td>${esc(m.title)}</td><td><span class="cat">${esc(titleCase(m.category))}</span></td><td class="sub">${m.scenarios.map((s) => s.slice(-4)).join(", ")}</td><td class="num">${esc(m.occurrences)}</td></tr>`).join("")}
        </tbody></table></div></section>`).join("") || '<div class="empty"><h3>No failures</h3><p>Every episode passed every gating check.</p></div>'}`;
    bindRowLinks(page);
  };

  async function failureDetail(page, id) {
    const f = await getFailure(id);
    if (!f) throw new NotFound("That failure doesn't exist");
    crumbs(["Analysis", "Failures", f.title]);
    const sc = scenario(f.scenario_id);
    const rc = f.root_cause;
    const rp = f.reproduction;
    const cluster = (S.exp.clusters || []).find((c) => c.failure_ids?.includes(f.failure_id));
    const reg = (S.exp.regressions || []).find((r) => r.scenario_id === cluster?.regression_id) ||
      (S.regressions || []).find((r) => r.regression?.source_failure_id === f.failure_id);
    page.innerHTML = `
      <div class="f-head">${sev(f.severity)}<span class="cat">${esc(titleCase(f.category))}</span><span class="sub">${esc(f.agent.name)} ${esc(f.agent.version)} · ${esc(f.scenario_id)} · seed ${esc(f.seed)}</span></div>
      <h1>${esc(f.title)}</h1>
      <p class="lede">${esc(sc?.title || "")}${cluster ? `. Seen in ${cluster.occurrences} of ${cluster.runs_in_scenario} repeats of this scenario.` : ""}</p>
      <div class="btn-row" style="margin-top:16px">
        <a class="btn primary" href="#/traces/${encodeURIComponent(f.run_id)}?focus=${encodeURIComponent(rc.failure_point_event_id || "")}">Open failing trace</a>
        ${rp ? `<button class="btn" id="open-min">Open minimal reproduction</button>` : ""}
        ${S.mode === "api" ? `<button class="btn" id="re-min">Re-run minimization</button><button class="btn" id="save-reg">Save as regression test</button>` : ""}
      </div>
      <section class="section grid-3-2">
        <div class="panel panel-pad">
          <h2>Root-cause hypothesis</h2>
          <div class="callout" style="margin-top:12px"><b>${esc(rc.hypothesis)}</b><div class="sub" style="margin-top:4px">Confidence: ${esc(rc.confidence)}. ${esc(rc.caveat)}</div></div>
          ${rc.divergence ? `<p style="margin:14px 0 0"><b>Earliest divergence.</b> ${esc(rc.divergence.description)}</p>` : ""}
          <div class="columns" style="margin-top:16px">
            <div><div class="sub" style="margin-bottom:6px">An acceptable execution</div><ol class="steps">${rc.expected.map((e) => `<li><span>${esc(e)}</span></li>`).join("")}</ol></div>
            <div><div class="sub" style="margin-bottom:6px">What the agent did</div><ol class="steps bad">${rc.actual.map((a) => `<li><code>${esc(a)}</code></li>`).join("")}</ol></div>
          </div>
          ${rc.triggering_fault ? `<p style="margin-top:14px"><span class="fault-tag">${esc(rc.triggering_fault.type)}</span> <span class="sub">on ${esc(rc.triggering_fault.target)}, call #${rc.triggering_fault.on_call ?? "any"}, +${(rc.triggering_fault.delay_ms / 1000).toFixed(0)}s, seed ${rc.triggering_fault.seed ?? f.seed}</span></p>` : ""}
          <h3 style="margin-top:20px">Recommended fix</h3><p style="margin:6px 0 0;color:var(--ink-2)">${esc(f.recommended_fix)}</p>
        </div>
        <div class="panel panel-pad">
          <h2>Minimal reproduction</h2>
          ${rp ? `<p class="sub" style="margin:4px 0 0">Delta debugging replayed ${rp.replays} candidate traces with faults pinned to their original decisions.</p>
            <div class="reduce" aria-label="Steps remaining after each reduction">${rp.history.map((n, i) => `<div class="bar"><b>${n}</b><i style="height:${Math.max(4, (n / rp.history[0]) * 80)}px"></i><small>${i === 0 ? "original" : i === rp.history.length - 1 ? "minimal" : "pass " + i}</small></div>`).join("")}</div>
            <ol class="steps" style="margin-top:14px">${rp.steps.map((s) => `<li><span><code>${s.kind === "final" ? `final answer: “${esc((s.text || "").slice(0, 90))}${(s.text || "").length > 90 ? "…" : ""}”` : `${esc(s.tool)}(${esc(argStr(s.args))})`}</code> ${s.faults.length ? `<span class="fault-tag">${esc(s.faults.join(", "))}</span>` : ""}</span></li>`).join("")}</ol>
            <p class="sub" style="margin-top:10px">${statusHtml(rp.deterministic ? "pass" : "fail", rp.deterministic ? "Deterministic" : "Non-deterministic")} — identical result on every replay.</p>`
            : '<p class="sub">Not minimized yet.</p>'}
        </div>
      </section>
      <section class="section grid-2">
        <div class="panel panel-pad">
          <h2>Evidence</h2>
          <ul class="checks" style="margin-top:8px">${[...f.checks].sort((a, b) => (a.role === "root" ? 0 : 1) - (b.role === "root" ? 0 : 1)).map((c) => `<li><span class="x">✕</span><div><b>${esc(c.name)}</b> <span class="role">${c.role === "consequence" ? "consequence" : "root"} · ${esc(c.severity)}</span><div class="sub">${esc(c.detail)}</div></div></li>`).join("")}</ul>
          <h3 style="margin-top:18px">Events</h3>
          <ol class="steps" style="margin-top:6px">${f.evidence.map((e) => `<li><span><a href="#/traces/${encodeURIComponent(f.run_id)}?focus=${encodeURIComponent(e.event_id)}"><code>${esc(e.text)}</code></a> <span class="sub">${esc(e.event_type.replace(/_/g, " "))}</span></span></li>`).join("")}</ol>
        </div>
        <div class="panel panel-pad">
          <div class="section-head"><div><h2>Regression test</h2><p>${reg ? `<b>${esc(reg.regression.name)}</b> re-runs the real agent under the same faults.` : "Not generated yet."}</p></div>${reg ? '<button class="btn" id="copy-reg">Copy JSON</button>' : ""}</div>
          ${reg ? `<pre class="code" id="reg-json">${json(regressionView(reg))}</pre>` : ""}
        </div>
      </section>`;
    if (rp) $("#open-min").onclick = async () => {
      const run = await getRun(f.run_id);
      const tid = `min:${f.failure_id}`;
      S.temp[tid] = { ...run, run_id: tid, events: rp.minimized_events, final_message: null, steps: rp.minimized_steps,
                      passed: false, minimal_of: f.failure_id, scenario_title: `Minimal reproduction · ${f.title}` };
      location.hash = `#/traces/${encodeURIComponent(tid)}`;
    };
    if (reg) $("#copy-reg").onclick = () => copyText(JSON.stringify(reg, null, 2), "Regression JSON");
    if (S.mode === "api") {
      $("#re-min").onclick = async (e) => {
        const r = await action(e.currentTarget, "Minimizing…", () => api(`/failures/${encodeURIComponent(id)}/minimize`, { method: "POST" }),
          (x) => `Minimized ${x.original_steps} → ${x.minimized_steps} steps in ${x.replays} replays`);
        if (r) { delete S.failCache[id]; route(); }
      };
      $("#save-reg").onclick = async (e) => {
        await action(e.currentTarget, "Saving…", async () => {
          const r = await api(`/failures/${encodeURIComponent(id)}/regression`, { method: "POST" });
          S.regressions = await api("/regressions");
          return r;
        }, (r) => `Saved regression test ${r.regression.name}`);
      };
    }
  }

  function regressionView(reg) {
    return { name: reg.regression.name, scenario_id: reg.scenario_id, environment: reg.environment,
             task: reg.task.user_message, faults: reg.faults.map(({ fault_id, type, target, on_call, delay_ms }) => ({ fault_id, type, target, on_call, delay_ms })),
             assertions: reg.expected_state, invariants: reg.invariants,
             source: { failure: reg.regression.source_failure_id, scenario: reg.regression.source_scenario_id,
                       minimized: `${reg.regression.original_steps} → ${reg.regression.minimized_steps} steps` } };
  }

  // ------------------------------------------------------------- Traces
  function stateAt(initial, events) {
    // replay structural diffs to reconstruct the state after every event
    const st = JSON.parse(JSON.stringify(initial || {}));
    const after = {};
    for (const e of events) {
      for (const d of e.state_diff || []) applyDiff(st, d);
      if (e.state_diff) after[e.event_id] = JSON.parse(JSON.stringify(st));
    }
    return after;
  }
  function pathTokens(p) {
    const out = [];
    p.split(".").forEach((part) => {
      const m = part.match(/^([^[]*)((?:\[\d+\])*)$/);
      if (m[1]) out.push(m[1]);
      (m[2].match(/\d+/g) || []).forEach((n) => out.push(+n));
    });
    return out;
  }
  function applyDiff(st, d) {
    const t = pathTokens(d.path);
    let node = st;
    for (let i = 0; i < t.length - 1; i++) { if (node[t[i]] == null) node[t[i]] = typeof t[i + 1] === "number" ? [] : {}; node = node[t[i]]; }
    const k = t.at(-1);
    if (d.op === "remove") { Array.isArray(node) ? node.splice(k, 1) : delete node[k]; } else node[k] = JSON.parse(JSON.stringify(d.after));
  }

  VIEWS.traces = async (page, id, q) => {
    crumbs(["Analysis", "Trace explorer"]);
    const runs = runsIndex();
    const pick = id || (runs.find((r) => !r.passed) || runs[0])?.run_id;
    const filt = S.traceFilter ||= { agent: "", result: "", scenario: "" };
    page.innerHTML = `<h1>Trace explorer</h1>
      <p class="lede">Every tool call, injected fault, state change and invariant breach on one virtual clock. Select an event to inspect it.</p>
      <div class="trace-layout section" style="margin-top:22px">
        <div class="panel">
          <div class="filters">
            <select id="f-agent" aria-label="Agent"><option value="">All agents</option>${agents().map((r) => `<option value="${esc(r)}">${esc(agentShort(r))}</option>`).join("")}</select>
            <select id="f-res" aria-label="Result"><option value="">All results</option><option value="fail">Failed</option><option value="pass">Passed</option></select>
            <select id="f-sc" aria-label="Scenario" style="flex-basis:100%"><option value="">All scenarios</option>${S.exp.config.scenario_ids.map((s) => `<option value="${esc(s)}">${esc(s)} · ${esc(scenario(s)?.title || "")}</option>`).join("")}</select>
          </div>
          <div class="run-list" id="run-list"></div>
        </div>
        <div class="panel" id="trace-main"></div>
        <aside class="panel inspector" id="inspector" aria-label="Event inspector"></aside>
      </div>`;
    $("#f-agent").value = filt.agent; $("#f-res").value = filt.result; $("#f-sc").value = filt.scenario;
    const drawList = () => {
      const list = runs.filter((r) => (!filt.agent || r.agent_ref === filt.agent) && (!filt.result || (filt.result === "pass") === r.passed)
        && (!filt.scenario || r.scenario_id === filt.scenario));
      $("#run-list").innerHTML = list.slice(0, 400).map((r) => `<button data-run="${esc(r.run_id)}" aria-pressed="${r.run_id === pick}">
        <i class="swatch" style="background:${inconclusive(r) ? "var(--line-strong)" : r.passed ? "var(--pass)" : "var(--fail)"}"></i>
        <span><span class="t">${esc(r.scenario_id.slice(-4))} · ${esc(agentShort(r.agent_ref))} · r${esc(r.repeat + 1)}</span><br><span class="m">${esc(r.passed ? "passed" : titleCase(r.primary_category))}</span></span>
        <span class="m">${esc(r.steps)}</span></button>`).join("") || '<div class="empty">No runs match</div>';
      $("#run-list").querySelectorAll("button").forEach((b) => (b.onclick = () => (location.hash = `#/traces/${encodeURIComponent(b.dataset.run)}`)));
    };
    ["f-agent", "f-res", "f-sc"].forEach((k, i) => ($("#" + k).onchange = (e) => { filt[["agent", "result", "scenario"][i]] = e.target.value; drawList(); }));
    drawList();
    $("#run-list").querySelector('[aria-pressed="true"]')?.scrollIntoView({ block: "nearest" });
    const run = pick && (await getRun(pick));
    if (!run) {
      $("#trace-main").innerHTML = `<div class="empty"><h3>Trace not found</h3><p>${id && id.startsWith("min:") ? "Minimal reproductions are generated on demand. Open it again from the failure page." : "This run doesn't exist on this server."}</p><a class="btn" href="#/failures">Browse failures</a></div>`;
      return;
    }
    renderTrace(run, q.get("focus"));
  };

  function renderTrace(run, focusId) {
    const events = run.events;
    const states = stateAt(run.initial_state, events);
    const end = Math.max(1, events.at(-1)?.t_ms || 1);
    const byId = Object.fromEntries(events.map((e) => [e.event_id, e]));
    const showSteps = S.showSteps || false;
    const visible = events.filter((e) => showSteps || e.event_type !== "agent_step");
    const pos = (t) => `${(t / end) * 100}%`;

    const faults = events.filter((e) => e.event_type === "fault_activated");
    const breaches = events.filter((e) => e.event_type === "invariant_breach");
    const ticks = events.filter((e) => e.event_type === "tool_call");
    const resultOf = {};
    events.forEach((e) => { if (e.event_type === "tool_result") resultOf[e.parent_event_id] = e; });
    const sc = scenario(run.scenario_id);

    let strip = `<div class="strip" aria-hidden="true"><div class="strip-axis"></div>`;
    faults.forEach((f) => {
      const res = resultOf[f.parent_event_id];
      const w = res ? Math.max(0.8, ((res.t_ms - f.t_ms) / end) * 100) : 0.8;
      strip += `<div class="strip-fault" style="left:${pos(f.t_ms)};width:${w}%"><span>${esc(f.metadata.type.replace(/_/g, " "))}</span></div>`;
    });
    ticks.forEach((t) => {
      const r = resultOf[t.event_id];
      const cls = r && r.result?.status !== "ok" ? "err" : r?.state_diff ? "write" : "call";
      strip += `<button class="strip-tick ${cls}" data-ev="${esc(t.event_id)}" style="left:${pos(t.t_ms)}" tabindex="-1"></button>`;
    });
    breaches.slice(0, 1).forEach((b) => (strip += `<div class="strip-breach ${b.t_ms / end > 0.7 ? "end" : ""}" style="left:${pos(b.t_ms)}"><span>invariant broken</span></div>`));
    strip += `</div><div class="strip-scale"><span>0s</span><span>${secs(end / 2)}</span><span>${secs(end)}</span></div>`;

    $("#trace-main").innerHTML = `<div class="panel-pad" style="border-bottom:1px solid var(--line-soft)">
        <div class="f-head">${run.minimal_of ? '<span class="fault-tag">Minimal reproduction</span>' : statusHtml(run.passed ? "pass" : "fail", run.passed ? "Passed" : "Failed")}
          <span class="sub">${esc(run.agent.name)} ${esc(run.agent.version)} · seed ${esc(run.seed)} · ${events.length} events · ${secs(end)} virtual</span></div>
        <h2>${esc(run.scenario_title || sc?.title || run.scenario_id)}</h2>
        <div class="sub">${esc(run.run_id)}</div>
        ${strip}
        <div class="btn-row" style="margin-top:10px">
          <label class="sub" style="display:flex;gap:6px;align-items:center"><input type="checkbox" id="steps" ${showSteps ? "checked" : ""}> Show model steps</label>
          ${(run.failure_ids || []).map((fid) => `<a class="btn" href="#/failures/${encodeURIComponent(fid)}">View diagnosis</a>`).slice(0, 1).join("")}
        </div>
      </div>
      <ol class="events" id="events">${visible.map((e) => eventRow(e, byId)).join("")}</ol>`;

    const select = (eid) => {
      const e = byId[eid];
      if (!e) return;
      $("#events").querySelectorAll("button").forEach((b) => b.setAttribute("aria-pressed", b.dataset.ev === eid));
      $("#trace-main").querySelectorAll(".strip-tick").forEach((b) => b.classList.toggle("sel", b.dataset.ev === eid || b.dataset.ev === e.parent_event_id));
      $("#inspector").innerHTML = inspector(e, byId, states, run);
    };
    $("#events").querySelectorAll("button").forEach((b) => (b.onclick = () => select(b.dataset.ev)));
    $("#trace-main").querySelectorAll(".strip-tick").forEach((b) => {
      const e = byId[b.dataset.ev];
      b.onclick = () => { select(b.dataset.ev); $(`#events button[data-ev="${CSS.escape(b.dataset.ev)}"]`)?.scrollIntoView({ block: "center", behavior: "smooth" }); };
      b.onmousemove = (ev) => tipShow(`<div class="t">${esc(e.tool.name)}</div><div class="r"><span>${esc(argStr(e.tool.arguments))}</span></div><div class="r"><span>at</span><b>${secs(e.t_ms)}</b></div>`, ev);
      b.onmouseleave = tipHide;
    });
    $("#steps").onchange = (e) => { S.showSteps = e.target.checked; renderTrace(run, focusId); };
    const first = focusId && byId[focusId] ? focusId : (breaches[0]?.event_id || faults[0]?.event_id || events.find((e) => e.state_diff)?.event_id || ticks[0]?.event_id || visible[0]?.event_id);
    select(first);
    if (focusId) $(`#events button[data-ev="${CSS.escape(first)}"]`)?.scrollIntoView({ block: "center" });
  }

  function eventRow(e, byId) {
    const t = e.event_type;
    let ic = "·", tx = "", cls = "";
    if (t === "user_message") { ic = "❝"; tx = `<b>Customer</b><div>${esc(e.message)}</div>`; }
    else if (t === "agent_step") { ic = "∴"; tx = `<span class="sm">model step ${e.metadata.step} · ${e.usage.input_tokens} in / ${e.usage.output_tokens} out tokens</span>`; }
    else if (t === "tool_call") { ic = "→"; tx = `<code>${esc(e.tool.name)}(${esc(argStr(e.tool.arguments))})</code>`; }
    else if (t === "tool_result") {
      cls = "indent";
      const ok = e.result.status === "ok";
      ic = ok ? `<span style="color:var(--pass)">✓</span>` : `<span style="color:var(--fail)">✕</span>`;
      tx = ok ? `<span class="sm">ok · ${e.duration_ms} ms</span>` : `<span style="color:var(--fail-ink)">${esc(e.result.error.code)}</span> <span class="sm">${esc(e.result.error.message)} · ${e.duration_ms} ms</span>`;
      if (e.state_diff) tx += ` <span class="sm" style="color:var(--ink-2)">· ${e.state_diff.length} state change${e.state_diff.length > 1 ? "s" : ""}</span>`;
    }
    else if (t === "fault_activated") { cls = "indent fault"; ic = '<span class="fault-tag" aria-label="fault"></span>'; tx = `<b style="color:var(--signal-ink)">Fault ${esc(e.metadata.fault_id)}: ${esc(e.metadata.type.replace(/_/g, " "))}</b> <span class="sm">on ${esc(e.metadata.target)}</span>`; }
    else if (t === "state_mutation") { cls = "fault"; ic = "⇄"; tx = `<b>External change</b> <span class="sm">${esc(e.message)}</span>`; }
    else if (t === "invariant_breach") { cls = "breach"; ic = `<span style="color:var(--fail)">!</span>`; tx = `<b style="color:var(--fail)">Invariant broken</b><div><code>${esc(e.metadata.expression)}</code></div>`; }
    else if (t === "final_answer") { ic = "◆"; tx = `<b>Agent reply</b><div>${esc(e.message)}</div>`; }
    else if (t === "budget_exceeded" || t === "guardrail") { cls = "breach"; ic = "■"; tx = `<b>${esc(titleCase(t))}</b> <span class="sm">${esc(e.message)}</span>`; }
    else { tx = `<span class="sm">${esc(t.replace(/_/g, " "))}</span>`; }
    return `<li class="${cls}"><button data-ev="${esc(e.event_id)}" aria-pressed="false"><span class="ts">${(e.t_ms / 1000).toFixed(3)}</span><span class="ic">${ic}</span><span class="tx">${tx}</span></button></li>`;
  }

  function inspector(e, byId, states, run) {
    const blk = (label, html) => `<div class="blk"><div class="lab">${label}</div>${html}</div>`;
    let out = `<div class="blk"><h3>${esc(titleCase(e.event_type))}</h3><dl class="kv">
      <dt>Time</dt><dd>${secs(e.t_ms)}</dd><dt>Actor</dt><dd>${esc(e.actor)}</dd><dt>Event</dt><dd class="mono">${esc(e.event_id.split(":").pop())}</dd>
      ${e.parent_event_id ? `<dt>Parent</dt><dd class="mono">${esc(e.parent_event_id.split(":").pop())}</dd>` : ""}
      ${e.duration_ms ? `<dt>Duration</dt><dd>${e.duration_ms} ms</dd>` : ""}</dl></div>`;
    if (e.tool?.arguments) out += blk("Arguments", `<pre class="code">${json(e.tool.arguments)}</pre>`);
    if (e.event_type === "tool_call") {
      const r = Object.values(byId).find((x) => x.event_type === "tool_result" && x.parent_event_id === e.event_id);
      if (r) e = { ...r, tool: e.tool };
    }
    if (e.result) out += blk(e.result.status === "ok" ? "Output" : "Error", `<pre class="code">${json(e.result.status === "ok" ? e.result.output : e.result.error)}</pre>`);
    if (e.metadata?.faults?.length) out += blk("Faults on this call", e.metadata.faults.map((f) => `<span class="fault-tag">${esc(f)}</span>`).join(" "));
    if (e.event_type === "fault_activated") out += blk("Fault definition", `<pre class="code">${json(e.metadata)}</pre>`);
    if (e.event_type === "invariant_breach") out += blk("Observed values", `<pre class="code">${json(e.metadata.observed)}</pre>`);
    if (e.message && ["final_answer", "user_message"].includes(e.event_type)) out += blk("Message", `<p style="margin:0">${esc(e.message)}</p>`);
    if (e.usage) out += blk("Usage", `${e.usage.input_tokens} input · ${e.usage.output_tokens} output tokens`);
    if (e.state_diff) {
      out += blk("State change", `<ul class="diff">${e.state_diff.map((d) => `<li><span class="p">${esc(d.path)}</span><br>${d.op !== "add" ? `<span class="b">${esc(JSON.stringify(d.before))}</span> ` : ""}${d.op !== "remove" ? `<span class="a">${esc(JSON.stringify(d.after))}</span>` : ""}</li>`).join("")}</ul>`);
      const st = states[e.event_id];
      if (st) out += blk("Environment state after this event", `<details><summary class="sub">orders, refunds, replacements, customers</summary><pre class="code" style="margin-top:8px">${json({ orders: st.orders, refunds: st.refunds, replacements: st.replacements, approvals: st.approvals, escalations: st.escalations, customers: st.customers })}</pre></details>`);
    }
    return out;
  }

  // ------------------------------------------------------------- Regressions
  VIEWS.regressions = async (page) => {
    crumbs(["Engineering", "Regression tests"]);
    const regs = S.mode === "static" ? S.exp.regressions : S.regressions;
    const rr = S.regRun;
    const ag = rr?.agents || agents();
    const rowOf = (sid) => rr?.rows.find((r) => r.scenario_id === sid);
    page.innerHTML = `<h1>Regression tests</h1>
      <p class="lede">Each test was generated from a discovered failure: the original task and state, the faults that triggered it, and assertions on the violated rules. Run the suite before every release.</p>
      ${rr ? `<section class="section panel panel-pad"><h2>Release gate</h2><div class="btn-row" style="margin-top:10px">${ag.map((a) => `<span style="margin-right:22px">${esc(agentShort(a))} ${statusHtml(rr.release_gate[a] ? "pass" : "fail", rr.release_gate[a] ? "Releasable" : "Blocked")}</span>`).join("")}
        ${S.mode === "api" ? '<button class="btn primary" id="run-suite">Run suite</button>' : ""}</div>
        <p class="sub" style="margin-top:8px">${rr.rows.length} tests × ${rr.repeats} repeats per agent${rr.created_at ? ` · last run ${esc(rr.created_at)}` : ""}. A test is flaky if some repeats fail.</p></section>`
        : S.mode === "api" ? '<p><button class="btn primary" id="run-suite">Run suite</button></p>' : ""}
      <section class="section panel"><div class="table-wrap"><table class="table"><thead><tr><th>Test</th><th>Guards against</th><th>Discovered on</th>${ag.map((a) => `<th>${esc(agentShort(a))}</th>`).join("")}</tr></thead><tbody>
        ${(regs || []).map((r) => {
          const row = rowOf(r.scenario_id);
          const covers = r.regression.covers || [{ title: r.title.replace("Regression: ", ""), category: r.regression.category }];
          return `<tr class="link" data-href="#/failures/${encodeURIComponent(r.regression.source_failure_id)}"><td><code>${esc(r.regression.name)}</code><div class="sub">${esc(r.scenario_id)} · from ${esc(r.regression.source_scenario_id)}</div></td>
            <td>${covers.slice(0, 3).map((c) => `<div>${esc(c.title)}</div>`).join("")}${covers.length > 3 ? `<div class="sub">+${covers.length - 3} more</div>` : ""}</td>
            <td class="sub">v${esc(r.regression.discovered_on.version)}</td>
            ${ag.map((a) => { const x = row?.results[a]; return `<td>${x ? statusHtml(x.status, `${x.passed}/${x.n} ${x.status}`) : '<span class="sub">not run</span>'}</td>`; }).join("")}</tr>`;
        }).join("") || `<tr><td colspan="5" class="empty">No regression tests yet. They are generated automatically when an experiment finds a failure.</td></tr>`}
      </tbody></table></div></section>
      <section class="section panel panel-pad"><h2>Run in CI</h2><p class="sub" style="margin:4px 0 10px">Block a merge when any regression fails.</p>
      <pre class="code">crashlab regress --agents v2 --repeats 3   # exit code 1 if the release gate blocks</pre></section>`;
    bindRowLinks(page);
    const btn = $("#run-suite");
    if (btn) btn.onclick = async () => {
      const n = (regs || []).length, ag2 = agents().length ? agents() : ["v1", "v2"];
      if (UI && !(await UI.confirm({ title: `Run ${n} regression tests?`, body: `Each test runs 3 times on ${ag2.length} agent version${ag2.length > 1 ? "s" : ""} (${n * 3 * ag2.length} episodes). This takes a few seconds and records a new release-gate result.`, confirmLabel: "Run suite" }))) return;
      // The suite runs in the background (real models can take minutes); poll until it finishes.
      const r = await action(btn, "Running…", async () => {
        await api("/regression-suites/default/run", { method: "POST", body: JSON.stringify({ agents: agents().length ? agents() : ["v1", "v2"], repeats: 3 }) });
        for (;;) {
          await new Promise((ok) => setTimeout(ok, 1500));
          const st = await api("/regression-suites/default/status");
          if (st.error) throw new Error(st.error);
          if (!st.running) return api("/regression-suites/default/latest");
        }
      }, (x) => `Suite finished: ${x.rows.length} tests. ${Object.entries(x.release_gate).map(([a, g]) => `${a} ${g ? "releasable" : "blocked"}`).join(", ")}`);
      if (r) { S.regRun = r; route(); }
    };
  };

  // ------------------------------------------------------------- Datasets
  VIEWS.datasets = async (page, id) => {
    if (id) return scenarioDetail(page, id);
    crumbs(["Testing", "Datasets"]);
    const sc = S.scenarios;
    const splits = sc.reduce((a, s) => ((a[s.split] = (a[s.split] || 0) + 1), a), {});
    page.innerHTML = `<h1>Scenario dataset</h1>
      <p class="lede">${sc.length} versioned scenarios for the e-commerce environment. Each defines a task, initial state, allowed tools, faults, expected final state and invariants. Splits: ${Object.entries(splits).map(([k, v]) => `${v} ${k}`).join(", ")}.</p>
      <section class="section panel"><div class="table-wrap"><table class="table"><thead><tr><th>Scenario</th><th>Category</th><th>Difficulty</th><th>Split</th><th>Faults</th><th class="num">Checks</th></tr></thead><tbody>
      ${sc.map((s) => `<tr class="link" data-href="#/datasets/${esc(s.scenario_id)}"><td>${esc(s.title)}<div class="sub">${esc(s.scenario_id)} · v${esc(s.version)}</div></td><td><span class="cat">${esc(titleCase(s.category))}</span></td><td>${esc(titleCase(s.difficulty))}</td><td class="sub">${esc(s.split)}</td>
        <td>${s.faults.map((f) => `<div class="fault-tag">${esc(f.type.replace(/_/g, " "))}</div>`).join("") || '<span class="sub">none</span>'}</td><td class="num">${s.expected_state.length + s.invariants.length}</td></tr>`).join("")}
      </tbody></table></div></section>`;
    bindRowLinks(page);
  };

  async function scenarioDetail(page, id) {
    const s = scenario(id);
    if (!s) throw new NotFound("That scenario doesn't exist");
    crumbs(["Testing", "Datasets", s.scenario_id]);
    page.innerHTML = `<div class="f-head"><span class="cat">${esc(titleCase(s.category))}</span><span class="sub">${esc(s.scenario_id)} · v${esc(s.version)} · ${esc(s.difficulty)} · ${esc(s.split)} split</span></div>
      <h1>${esc(s.title)}</h1><p class="lede">${esc(s.description)}</p>
      <section class="section grid-2">
        <div class="panel panel-pad">
          <h2>Task</h2><div class="callout" style="margin-top:10px">“${esc(s.task.user_message)}”</div>
          <p style="margin-top:10px"><b>Goal.</b> ${esc(s.task.goal)}</p>
          <h3 style="margin-top:18px">Faults</h3>
          ${s.faults.length ? `<ul class="checks">${s.faults.map((f) => `<li><span class="fault-tag"></span><div><b>${esc(f.type.replace(/_/g, " "))}</b> on <code>${esc(f.target)}</code><div class="sub">call #${f.on_call ?? "any"}${f.max_activations ? `, up to ${f.max_activations}×` : ""}${f.delay_ms ? `, +${f.delay_ms / 1000}s` : ""}${f.params?.payload ? ` · payload: “${esc(f.params.payload)}”` : ""}${f.params?.description ? ` · ${esc(f.params.description)}` : ""}</div></div></li>`).join("")}</ul>` : '<p class="sub">No faults. Baseline behaviour.</p>'}
          <h3 style="margin-top:18px">Expected final state</h3><ul class="checks">${s.expected_state.map((e) => `<li><span class="ok">=</span><code>${esc(e)}</code></li>`).join("")}</ul>
          <h3 style="margin-top:18px">Invariants (checked after every state change)</h3><ul class="checks">${s.invariants.map((e) => `<li><span class="ok">∀</span><code>${esc(e)}</code></li>`).join("")}</ul>
          <h3 style="margin-top:18px">Policies</h3><p class="sub">${(s.policies || []).map((p) => esc(p.replace(/_/g, " "))).join(", ") || "none"}</p>
        </div>
        <div class="panel panel-pad">
          <div class="section-head"><h2>Validation pipeline</h2>${S.mode === "api" ? '<button class="btn" id="validate">Validate now</button>' : ""}</div>
          <div id="val">${validationHtml(S.validation[s.scenario_id])}</div>
          <h3 style="margin-top:18px">Definition</h3><pre class="code" style="margin-top:8px;max-height:520px">${json(s)}</pre>
        </div>
      </section>`;
    const b = $("#validate");
    if (b) b.onclick = async () => {
      const r = await action(b, "Validating…", () => api("/scenarios/validate", { method: "POST", body: JSON.stringify(s) }), null);
      if (!r) return;
      $("#val").innerHTML = validationHtml(r);
      r.ok ? success("Scenario is valid: every stage passed") : failure(`Validation failed at ${Object.keys(r.stages).find((k) => r.stages[k] === "fail")}`);
    };
  }
  function validationHtml(v) {
    if (!v) return '<p class="sub">Not validated in this view yet.</p>';
    return `<ul class="checks">${Object.entries(v.stages).map(([k, st]) => `<li><span class="${st === "pass" ? "ok" : "x"}">${st === "pass" ? "✓" : "✕"}</span><span>${esc(titleCase(k))}</span></li>`).join("")}</ul>
      ${v.errors?.length ? `<div class="callout red" style="margin-top:8px">${v.errors.map(esc).join("<br>")}</div>` : `<p class="sub">Fingerprint <code>${esc(v.fingerprint || "")}</code></p>`}`;
  }

  // ------------------------------------------------------------- Experiments
  VIEWS.experiments = async (page, id) => {
    if (id === "new") {
      if (S.mode === "static") {
        crumbs(["Workspace", "Experiments", "New experiment"]); setMeta("New experiment", null);
        page.innerHTML = `<h1>New experiment</h1><div class="empty"><h3>Starting experiments needs the server</h3>
          <p>This is a shared, read-only report. Run <code>crashlab serve</code> and open it locally to start new experiments.</p>
          <a class="btn" href="#/experiments">Back to experiments</a></div>`;
        return;
      }
      setMeta("New experiment", "Choose agents, scenarios and repeats for a new crash-test experiment.");
      return newExperiment(page);
    }
    if (id) throw new NotFound("That experiment page doesn't exist");
    crumbs(["Workspace", "Experiments"]);
    let list;
    if (S.mode === "static") list = [{ experiment_id: S.exp.experiment_id, name: S.exp.name, status: S.exp.status, created_at: S.exp.created_at, config: S.exp.config,
      headline: Object.fromEntries(agents().map((r) => [r, summary(r)])) }];
    else list = await api("/experiments");
    page.innerHTML = `<div class="section-head" style="margin:0"><div><h1>Experiments</h1><p class="lede">An experiment runs one or more agent versions against a scenario set, repeatedly, with identical seeds.</p></div>
      ${S.mode === "api" ? '<a class="btn primary" href="#/experiments/new">New experiment</a>' : ""}</div>
      <section class="section panel"><div class="table-wrap"><table class="table"><thead><tr><th>Experiment</th><th>Status</th><th>Agents</th><th class="num">Episodes</th><th>Success by agent</th></tr></thead><tbody>
      ${list.map((e) => `<tr class="link" data-exp="${esc(e.experiment_id)}" data-status="${esc(e.status)}"><td>${esc(e.name)}<div class="sub">${esc(e.experiment_id)} · ${esc(e.created_at)}</div></td>
        <td>${statusHtml(e.status === "COMPLETED" ? "pass" : e.status === "FAILED" ? "fail" : "flaky", titleCase(e.status))}</td>
        <td>${esc((e.config.agents || []).join(", "))}</td><td class="num">${Object.values(e.headline || {}).reduce((a, h) => a + (h.episodes || 0), 0) || "—"}</td>
        <td>${Object.entries(e.headline || {}).map(([k, h]) => `${esc(k)} ${pct(h.success_rate, 0)}`).join(" · ") || "—"}</td></tr>`).join("") || '<tr><td colspan="5" class="empty">No experiments yet.</td></tr>'}
      </tbody></table></div></section>`;
    page.querySelectorAll("tr[data-exp]").forEach((tr) => {
      tr.tabIndex = 0;
      const open = async () => {
        if (["QUEUED", "RUNNING"].includes(tr.dataset.status)) { location.hash = `#/live/${tr.dataset.exp}`; return; }
        if (S.mode === "api") await loadExperiment(tr.dataset.exp);
        location.hash = "#/overview";
      };
      tr.onclick = open; tr.onkeydown = (e) => e.key === "Enter" && open();
    });
  };

  async function newExperiment(page) {
    crumbs(["Workspace", "Experiments", "New experiment"]);
    const ag = await api("/agents");
    page.innerHTML = `<h1>New experiment</h1><p class="lede">Choose the agents, the scenarios and how many times to repeat each one. Repeats with fixed seeds make results comparable across versions.</p>
      <form class="form section" id="exp-form">
        <div class="field"><span class="label">Agents</span><div class="agent-choices">${ag.map((a) => `<label><input type="checkbox" name="agent" value="${esc(a.ref)}" ${a.ref === "v1" || a.ref === "v2" ? "checked" : ""} ${a.available === false ? "disabled" : ""}><span><b>${esc(a.name)} ${esc(a.version)}</b><br><span class="sub">${esc(a.framework)}${a.available === false ? ` · set ${a.ref === "gemini" ? "GEMINI_API_KEY" : "ANTHROPIC_API_KEY"} to enable` : ""}</span></span></label>`).join("")}</div></div>
        <div class="field"><span class="label">Scenarios</span><div class="checks-grid">${S.scenarios.map((s) => `<label><input type="checkbox" name="sc" value="${esc(s.scenario_id)}" checked><span>${s.scenario_id.slice(-4)} · ${esc(s.title)}</span></label>`).join("")}</div></div>
        <div class="field"><label for="repeats">Repeats per scenario</label><input id="repeats" type="number" min="1" max="50" value="5" style="width:100px"><div class="hint">pass^k is reported for every k up to this number.</div></div>
        <div class="field"><label for="seed">Base seed</label><input id="seed" type="number" value="2026" style="width:140px"></div>
        <p class="estimate" id="estimate"></p>
        <div class="btn-row"><button class="btn primary" type="submit">Start experiment</button><a class="btn" href="#/experiments">Cancel</a></div>
      </form>`;
    const form = $("#exp-form");
    const est = () => {
      const a = form.querySelectorAll('[name=agent]:checked').length, s = form.querySelectorAll('[name=sc]:checked').length, r = +$("#repeats").value || 0;
      $("#estimate").textContent = `${a} agent${a === 1 ? "" : "s"} × ${s} scenarios × ${r} repeats = ${a * s * r} episodes.`;
    };
    form.oninput = est; est();
    form.onsubmit = async (e) => {
      e.preventDefault();
      const body = { agents: [...form.querySelectorAll("[name=agent]:checked")].map((x) => x.value),
        scenario_ids: [...form.querySelectorAll("[name=sc]:checked")].map((x) => x.value), repeats: +$("#repeats").value, base_seed: +$("#seed").value };
      if (!body.agents.length || !body.scenario_ids.length) { failure("Select at least one agent and one scenario."); return; }
      if (!(body.repeats >= 1 && body.repeats <= 50)) { failure("Repeats must be between 1 and 50."); $("#repeats").focus(); return; }
      const episodes = body.agents.length * body.scenario_ids.length * body.repeats;
      if (UI && episodes > 300 && !(await UI.confirm({ title: `Start ${episodes} episodes?`, body: "Large experiments take longer and use one of the server's experiment slots until they finish.", confirmLabel: "Start experiment" }))) return;
      const r = await action(form.querySelector("[type=submit]"), "Starting…", () => api("/experiments", { method: "POST", body: JSON.stringify(body) }),
        (x) => `Experiment started: ${x.episodes} episodes queued`);
      if (r) location.hash = `#/live/${r.experiment_id}`;
    };
  }

  // ------------------------------------------------------------- Live
  VIEWS.live = async (page, id) => {
    crumbs(["Testing", "Live runs"]);
    if (S.mode === "static" || !id) {
      page.innerHTML = `<h1>Live runs</h1><div class="empty"><h3>${S.mode === "static" ? "Live monitoring needs the API" : "Nothing running"}</h3>
        <p>${S.mode === "static" ? "This is a static report. Start the server with <code>crashlab serve</code> to watch experiments run in real time." : "Start an experiment to watch episodes, faults and failures as they happen."}</p>
        ${S.mode === "api" ? '<a class="btn primary" href="#/experiments/new">New experiment</a>' : ""}</div>`;
      return;
    }
    page.innerHTML = `<h1>Live run</h1><p class="lede" id="live-sub">Connecting to ${esc(id)}…</p>
      <section class="section panel panel-pad"><div class="progress"><i id="bar"></i></div>
        <div class="btn-row" style="margin-top:12px;justify-content:space-between"><span id="count" class="sub"></span><span class="btn-row"><button class="btn" id="cancel">Cancel experiment</button><a class="btn primary" id="view" href="#/overview" hidden>View results</a></span></div>
        <div class="live-cells" id="live-cells"></div></section>
      <section class="section grid-2"><div class="panel"><div class="panel-pad"><h2>Live events</h2></div><ol class="events ticker" id="ticker"></ol></div>
        <div class="panel"><div class="panel-pad"><h2>Failures detected</h2></div><ol class="events ticker" id="fails"></ol></div></section>`;
    let total = 0;
    const cells = $("#live-cells");
    const stop = streamEvents(`/experiments/${encodeURIComponent(id)}/stream`, {
      "experiment.started": (d) => { total = d.total; $("#live-sub").textContent = `Running ${total} episodes.`; },
      "run.completed": (d) => {
        total = d.total;
        $("#bar").style.width = `${(d.done / d.total) * 100}%`;
        $("#count").textContent = `${d.done} of ${d.total} episodes complete`;
        const b = document.createElement("span");
        b.className = "cell " + verdictCls(d); b.title = `${d.scenario_id} · ${d.agent_ref} · ${d.passed ? "passed" : d.primary_category}`;
        cells.appendChild(b);
        const tk = $("#ticker");
        tk.insertAdjacentHTML("afterbegin", (d.events || []).filter((e) => ["tool_call", "fault_activated", "invariant_breach", "final_answer"].includes(e.event_type)).slice(-4).map((e) => eventRow(e, {})).join(""));
        while (tk.children.length > 40) tk.lastChild.remove();
      },
      "run.failed": (d) => {
        $("#fails").insertAdjacentHTML("afterbegin", `<li><button><span class="ts"></span><span class="ic" style="color:var(--fail)">✕</span><span class="tx"><b>${esc(d.title)}</b><div class="sm">${esc(d.run_id)}</div></span></button></li>`);
        while ($("#fails").children.length > 40) $("#fails").lastChild.remove();
      },
      "experiment.failed": () => ($("#live-sub").textContent = "The experiment failed on the server. Check the server logs."),
      "diagnosis.minimizing": () => ($("#live-sub").textContent = "Episodes complete. Minimizing failures and generating regression tests…"),
      "stream.end": async () => {
        stop();
        await loadExperiment(id);
        S.regressions = await api("/regressions");
        $("#live-sub").textContent = `Finished. ${S.exp.status === "CANCELLED" ? "Cancelled before completion." : ""}`;
        $("#view").hidden = false; $("#cancel").hidden = true;
        renderNav("live");
      },
      error: () => { $("#live-sub").textContent = "Stream disconnected. Results will appear under Experiments when the run finishes."; },
    });
    $("#cancel").onclick = async (e) => {
      const btnEl = e.currentTarget;
      if (UI && !(await UI.confirm({ title: "Cancel this experiment?", body: "Episodes already finished are kept, but the remaining ones won't run and the results will be incomplete. This can't be undone.", confirmLabel: "Cancel experiment", cancelLabel: "Keep running", danger: true }))) return;
      return action(btnEl, "Cancelling…", () => api(`/experiments/${encodeURIComponent(id)}/cancel`, { method: "POST" }),
      "Cancellation requested; running episodes will stop shortly");
    };
    window.addEventListener("hashchange", stop, { once: true });
  };

  // ------------------------------------------------------------- Integrations
  VIEWS.integrations = async (page) => {
    crumbs(["Engineering", "Integrations"]);
    page.innerHTML = `<h1>Integrations</h1><p class="lede">Connect any agent. Your code drives the tools; Crash Lab injects the faults, records the trace and evaluates the outcome.</p>
      ${S.mode === "api" && S.me && S.me.user ? '<section class="section panel panel-pad" id="connect"></section>' : ""}
      <section class="section grid-2">
        <div class="panel panel-pad"><h2>Python SDK</h2><p class="sub" style="margin:4px 0 10px">Wrap an agent function and test it against any scenario.</p>
<pre class="code">from crashlab.sdk import CrashLab, ToolCallFailed

lab = CrashLab()  # or CrashLab(base_url="https://…", api_key="…")

def my_agent(task, tools, context):
    order = tools.get_order(order_id="ORD-1001")
    key = "refund:ORD-1001"
    try:
        tools.issue_refund(order_id="ORD-1001",
                           amount=order["amount"], idempotency_key=key)
    except ToolCallFailed as e:
        if e.code == "timeout":
            tools.get_refund_status(order_id="ORD-1001")
    return "Your refund has been initiated."

report = lab.test(my_agent, scenarios=["ACL-ECOM-0002"], repeats=5)
print(report.summary())</pre></div>
        <div class="panel panel-pad"><h2>GitHub Actions</h2><p class="sub" style="margin:4px 0 10px">Block a pull request when reliability drops or a regression returns.</p>
<pre class="code">name: Agent reliability
on: [pull_request]
jobs:
  crash-lab:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with: { python-version: "3.12" }
      - run: pip install -e .
      - run: |
          crashlab run --agents v2 --repeats 5 \\
            --gate "success>=0.95,security==0,duplicates==0" \\
            --fail-on-regression</pre></div>
        <div class="panel panel-pad"><h2>REST API</h2><p class="sub" style="margin:4px 0 10px">Every screen here is built on the public API. Interactive docs at <code>/docs</code>.</p>
<pre class="code">POST /api/v1/experiments          start an experiment (202)
GET  /api/v1/experiments/{id}/stream   live progress (SSE)
GET  /api/v1/runs/{id}/trace      full event trace
POST /api/v1/failures/{id}/minimize    delta-debug a failure
POST /api/v1/failures/{id}/regression  save a regression test
POST /api/v1/regression-suites/default/run
POST /api/v1/ingest/runs          upload SDK episodes</pre></div>
        <div class="panel panel-pad"><h2>LLM agents</h2><p class="sub" style="margin:4px 0 10px">Built-in tool-calling agents run Claude or Gemini models through the same harness. Set the key on the server, never in the browser.</p>
<pre class="code">export ANTHROPIC_API_KEY=…      # Claude
crashlab run --agents v2 llm --repeats 3

export GEMINI_API_KEY=…         # Gemini (GEMINI_MODEL to pick a model)
crashlab run --agents v2 gemini --repeats 3</pre>
<p class="sub" style="margin-top:10px">Planned adapters: OpenAI Agents SDK, LangGraph, CrewAI and a generic HTTP agent, all emitting the same event schema.</p></div>
      </section>`;
    if ($("#connect")) connectPanel($("#connect"));
  };

  // "Connect your agent": a key (shown once), the SDK installed from this site, a test of the user's own agent.
  async function connectPanel(box) {
    const origin = location.origin;
    const install = S.me.sdk_wheel ? `pip install "${origin}${S.me.sdk_wheel}"` : `pip install -e ".[server]"   # from the project folder`;
    const draw = (keys, fresh) => {
      const live = keys.filter((k) => !k.revoked_at);
      box.innerHTML = `<h2>Connect your own agent</h2>
        <p class="sub" style="margin:4px 0 16px">Three steps. Your agent runs on your machine; only the results are uploaded to your private workspace.</p>
        <ol class="connect-steps">
          <li><b>Create an API key</b>
            ${fresh ? `<div class="callout red" role="status" style="margin:10px 0"><b>Copy this key now: it won't be shown again.</b><pre class="code">${esc(fresh)}</pre></div>` : ""}
            <form class="btn-row" id="key-new" style="margin-top:10px"><label class="sr-only" for="key-name">Key name</label>
              <input id="key-name" maxlength="80" required placeholder="Name, e.g. my-laptop" style="height:34px;padding:0 10px;border:1px solid var(--line);background:var(--surface);min-width:0;flex:1;max-width:280px">
              <button class="btn primary" type="submit">Create key</button></form>
            ${live.length ? `<div class="table-wrap" style="margin-top:12px"><table class="table"><thead><tr><th>Name</th><th>Access</th><th>Created</th><th>Last used</th><th><span class="sr-only">Actions</span></th></tr></thead><tbody>
              ${live.map((k) => `<tr><td><b>${esc(k.name)}</b><div class="sub mono">${esc(k.id)}</div></td><td>${esc(k.scopes)}</td><td class="sub">${esc((k.created_at || "").slice(0, 10))}</td><td class="sub">${esc((k.last_used_at || "never").slice(0, 10))}</td>
                <td><button class="btn small danger-ghost" data-revoke="${esc(k.id)}" data-name="${esc(k.name)}">Revoke</button></td></tr>`).join("")}
              </tbody></table></div>` : '<p class="sub" style="margin-top:8px">No keys yet.</p>'}</li>
          <li><b>Install the SDK</b><pre class="code">${esc(install)}</pre></li>
          <li><b>Test your agent</b><p class="sub" style="margin:4px 0 0">Replace <code>my_agent</code> with yours: it receives the task, the tools and the session context.</p>
<pre class="code">from crashlab.sdk import CrashLab

lab = CrashLab(base_url="${esc(origin)}", api_key="PASTE_YOUR_KEY")

def my_agent(task, tools, context):
    order = tools.get_order(order_id="ORD-1001")
    tools.issue_refund(order_id="ORD-1001", amount=order["amount"])
    return "Your refund has been initiated."

print(lab.test(my_agent, scenarios=["ACL-ECOM-0001", "ACL-ECOM-0002"], repeats=3).summary())</pre>
            <p class="sub">Results appear under <a href="#/experiments">Experiments</a> as "SDK ingest". Every scenario and its tools are listed under <a href="#/datasets">Datasets</a>.</p></li>
        </ol>`;
      $("#key-new").onsubmit = async (e) => {
        e.preventDefault();
        const name = $("#key-name").value.trim();
        if (!name) return;
        const k = await action(e.submitter || $("#key-new button"), "Creating…",
          () => api("/api-keys", { method: "POST", body: JSON.stringify({ name, scopes: ["read", "write"] }) }), "Key created. Copy it now.");
        if (k) draw(await api("/api-keys"), k.key);
      };
      box.querySelectorAll("[data-revoke]").forEach((b) => (b.onclick = async () => {
        if (UI && !(await UI.confirm({ title: `Revoke "${b.dataset.name}"?`, body: "Anything using this key (scripts, CI) stops working immediately. This can't be undone.", confirmLabel: "Revoke key", danger: true }))) return;
        const r = await action(b, "Revoking…", () => api(`/api-keys/${encodeURIComponent(b.dataset.revoke)}/revoke`, { method: "POST" }), "Key revoked");
        if (r) draw(await api("/api-keys"), null);
      }));
    };
    box.innerHTML = '<div class="skeleton"><i class="h"></i><i></i><i></i></div>';
    try { draw(await api("/api-keys"), null); } catch (e) { box.innerHTML = `<p class="sub">Couldn't load your API keys: ${esc(friendly(e))}</p>`; }
  }

  boot();
})();
