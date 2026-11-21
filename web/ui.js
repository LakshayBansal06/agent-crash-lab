/* Shared interface pieces for the public site and the dashboard:
   theme toggle, search dialog, confirmation dialog, back-to-top, scroll progress,
   copy buttons on code, skip link and password visibility toggles.
   Everything degrades gracefully: without JavaScript the pages still read and work. */
(() => {
  "use strict";
  const $ = (s, r = document) => r.querySelector(s);
  const reduced = () => window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  const esc = (v) => String(v ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const ICONS = {
    sun: '<svg class="sun" viewBox="0 0 20 20" aria-hidden="true"><circle cx="10" cy="10" r="3.6" fill="none" stroke="currentColor" stroke-width="1.8"/><path d="M10 1.8v2.4M10 15.8v2.4M1.8 10h2.4M15.8 10h2.4M4.2 4.2l1.7 1.7M14.1 14.1l1.7 1.7M4.2 15.8l1.7-1.7M14.1 5.9l1.7-1.7" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"/></svg>',
    moon: '<svg class="moon" viewBox="0 0 20 20" aria-hidden="true"><path d="M16.5 12.6A7 7 0 0 1 7.4 3.5a7 7 0 1 0 9.1 9.1z" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linejoin="round"/></svg>',
    search: '<svg viewBox="0 0 20 20" aria-hidden="true"><circle cx="8.6" cy="8.6" r="5.6" fill="none" stroke="currentColor" stroke-width="1.8"/><path d="M13 13l4.5 4.5" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"/></svg>',
    up: '<svg viewBox="0 0 20 20" aria-hidden="true"><path d="M10 16V4M4.5 9.5L10 4l5.5 5.5" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/></svg>',
    eye: '<svg viewBox="0 0 20 20" aria-hidden="true"><path d="M1.8 10S5 4.5 10 4.5 18.2 10 18.2 10 15 15.5 10 15.5 1.8 10 1.8 10z" fill="none" stroke="currentColor" stroke-width="1.7"/><circle cx="10" cy="10" r="2.6" fill="none" stroke="currentColor" stroke-width="1.7"/></svg>',
  };

  // ------------------------------------------------------------- theme
  const theme = {
    get: () => document.documentElement.getAttribute("data-theme") || "dark",
    set(t) {
      document.documentElement.setAttribute("data-theme", t);
      try { localStorage.setItem("crashlab.theme", t); } catch (_) {}
      document.querySelectorAll(".theme-toggle").forEach(sync);
      document.dispatchEvent(new CustomEvent("themechange", { detail: t }));
    },
  };
  function sync(btn) {
    const dark = theme.get() === "dark";
    btn.setAttribute("aria-pressed", String(!dark));
    btn.setAttribute("aria-label", dark ? "Switch to light theme" : "Switch to dark theme");
    btn.title = btn.getAttribute("aria-label");
  }
  function themeButton() {
    const b = document.createElement("button");
    b.type = "button"; b.className = "icon-btn theme-toggle";
    b.innerHTML = ICONS.moon + ICONS.sun;
    b.addEventListener("click", () => theme.set(theme.get() === "dark" ? "light" : "dark"));
    sync(b);
    return b;
  }
  // follow the OS setting live until the visitor makes their own choice
  try {
    window.matchMedia("(prefers-color-scheme: light)").addEventListener("change", (e) => {
      let saved = null; try { saved = localStorage.getItem("crashlab.theme"); } catch (_) {}
      if (!saved) { document.documentElement.setAttribute("data-theme", e.matches ? "light" : "dark"); document.querySelectorAll(".theme-toggle").forEach(sync); }
    });
  } catch (_) {}

  // ------------------------------------------------------------- dialogs
  function makeDialog(cls) {
    const d = document.createElement("dialog");
    d.className = "ui-dialog " + (cls || "");
    document.body.appendChild(d);
    return d;
  }
  /** Ask before doing something costly or irreversible. Resolves true only on an explicit confirm. */
  function confirm({ title, body, confirmLabel = "Confirm", cancelLabel = "Cancel", danger = false }) {
    return new Promise((resolve) => {
      const opener = document.activeElement;
      const d = makeDialog("confirm-dialog");
      d.setAttribute("aria-labelledby", "dlg-title");
      d.innerHTML = `<div class="dlg-body"><h2 id="dlg-title">${esc(title)}</h2>${body ? `<p>${esc(body)}</p>` : ""}</div>
        <div class="dlg-actions"><button type="button" class="btn" data-a="no">${esc(cancelLabel)}</button>
        <button type="button" class="btn ${danger ? "danger" : "primary"}" data-a="yes">${esc(confirmLabel)}</button></div>`;
      let answer = false;
      d.addEventListener("click", (e) => {
        const b = e.target.closest("button[data-a]");
        if (b) { answer = b.dataset.a === "yes"; d.close(); }
        else if (e.target === d) d.close(); // click on the backdrop = cancel
      });
      d.addEventListener("close", () => { d.remove(); if (opener && opener.focus) opener.focus(); resolve(answer); });
      d.showModal();
      d.querySelector('[data-a="no"]').focus(); // safe default: Enter right away cancels
    });
  }

  // ------------------------------------------------------------- search
  function highlight(text, terms) {
    let out = esc(text);
    terms.forEach((t) => { if (t.length > 1) out = out.replace(new RegExp(`(${t.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")})`, "ig"), "<mark>$1</mark>"); });
    return out;
  }
  function rank(items, q) {
    const terms = q.toLowerCase().split(/\s+/).filter(Boolean);
    if (!terms.length) return [];
    return items.map((it) => {
      const title = (it.title || "").toLowerCase(), text = ((it.subtitle || "") + " " + (it.text || "")).toLowerCase();
      const keys = (it.keys || []).map((k) => String(k).toLowerCase());
      let score = it.boost || 0;
      for (const t of terms) {
        if (keys.includes(t)) score += 8; // exact identifier, e.g. a scenario number
        else if (title.includes(t)) score += title.startsWith(t) ? 6 : 4;
        else if (text.includes(t)) score += 1;
        else return null; // every word must match somewhere
      }
      return { it, score };
    }).filter(Boolean).sort((a, b) => b.score - a.score).slice(0, 12).map((r) => r.it);
  }
  /** Opens the search dialog. getItems() returns [{title, subtitle, text, url}] (may be async). */
  async function openSearch(getItems, { placeholder = "Search", onGo } = {}) {
    if ($("dialog.search-dialog[open]")) return;
    const d = makeDialog("search-dialog");
    d.setAttribute("aria-label", "Search");
    d.innerHTML = `<div class="search-field">${ICONS.search}<input type="search" role="combobox" aria-expanded="true" aria-controls="sr" aria-autocomplete="list" placeholder="${esc(placeholder)}" autocomplete="off" spellcheck="false"></div>
      <ul class="results" id="sr" role="listbox"></ul><div class="hint">↑ ↓ to move · Enter to open · Esc to close</div>`;
    const input = $("input", d), list = $(".results", d);
    let items = [], shown = [], sel = 0;
    const draw = () => {
      const q = input.value.trim();
      shown = rank(items, q);
      sel = 0;
      list.innerHTML = !q ? '<li class="none">Type to search pages, sections and questions.</li>'
        : shown.length ? shown.map((it, i) => `<li role="presentation"><a role="option" id="sr-${i}" href="${esc(it.url)}" aria-selected="${i === 0}"><span class="t">${highlight(it.title, q.split(/\s+/))}</span>${it.subtitle ? `<span class="s">${highlight(it.subtitle, q.split(/\s+/))}</span>` : ""}</a></li>`).join("")
        : `<li class="none">No results for “${esc(q)}”. Try a shorter word.</li>`;
      input.setAttribute("aria-activedescendant", shown.length ? "sr-0" : "");
    };
    const move = (k) => {
      if (!shown.length) return;
      sel = (sel + k + shown.length) % shown.length;
      list.querySelectorAll("a").forEach((a, i) => a.setAttribute("aria-selected", String(i === sel)));
      list.querySelector(`#sr-${sel}`)?.scrollIntoView({ block: "nearest" });
      input.setAttribute("aria-activedescendant", `sr-${sel}`);
    };
    const go = (url) => { d.close(); if (onGo) onGo(url); else location.href = url; };
    input.addEventListener("input", draw);
    input.addEventListener("keydown", (e) => {
      if (e.key === "ArrowDown") { e.preventDefault(); move(1); }
      else if (e.key === "ArrowUp") { e.preventDefault(); move(-1); }
      else if (e.key === "Enter" && shown[sel]) { e.preventDefault(); go(shown[sel].url); }
      else if (e.key === "Escape") { e.preventDefault(); d.close(); } // one press closes (a search box would only clear)
    });
    list.addEventListener("click", (e) => { const a = e.target.closest("a"); if (a) { e.preventDefault(); go(a.getAttribute("href")); } });
    d.addEventListener("click", (e) => { if (e.target === d) d.close(); });
    d.addEventListener("close", () => d.remove());
    d.showModal();
    input.focus();
    draw();
    try { items = await getItems(); } catch (_) { items = []; list.innerHTML = '<li class="none">Search is unavailable right now.</li>'; return; }
    draw();
  }
  function searchButton(onClick) {
    const b = document.createElement("button");
    b.type = "button"; b.className = "icon-btn search-btn";
    b.setAttribute("aria-label", "Search (press /)");
    b.innerHTML = `${ICONS.search}<span class="label">Search</span><kbd>/</kbd>`;
    b.addEventListener("click", onClick);
    document.addEventListener("keydown", (e) => {
      const typing = /^(INPUT|TEXTAREA|SELECT)$/.test(document.activeElement?.tagName) || document.activeElement?.isContentEditable;
      if ((e.key === "/" && !typing) || ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "k")) {
        if ($("dialog[open]")) return;
        e.preventDefault(); onClick();
      }
    });
    return b;
  }

  // ------------------------------------------------------------- back to top + progress
  function floaters(extra) {
    const wrap = document.createElement("div");
    wrap.className = "floaters";
    const top = document.createElement("button");
    top.type = "button"; top.className = "fab to-top"; top.hidden = true;
    top.setAttribute("aria-label", "Back to top"); top.title = "Back to top";
    top.innerHTML = ICONS.up;
    top.addEventListener("click", () => {
      window.scrollTo({ top: 0, behavior: reduced() ? "auto" : "smooth" });
      const target = $("#main, #page");
      if (target) { if (!target.hasAttribute("tabindex")) target.setAttribute("tabindex", "-1"); target.focus({ preventScroll: true }); }
    });
    wrap.appendChild(top);
    if (extra) wrap.appendChild(extra);
    document.body.appendChild(wrap);
    const bar = document.createElement("div");
    bar.className = "progress-bar"; bar.setAttribute("aria-hidden", "true");
    document.body.appendChild(bar);
    let ticking = false;
    const update = () => {
      ticking = false;
      const max = document.documentElement.scrollHeight - window.innerHeight;
      const y = window.scrollY;
      top.hidden = y < Math.max(500, window.innerHeight * 0.8);
      // only long pages get a progress bar
      bar.style.transform = `scaleX(${max > window.innerHeight * 0.5 ? Math.min(1, y / max) : 0})`;
    };
    window.addEventListener("scroll", () => { if (!ticking) { ticking = true; requestAnimationFrame(update); } }, { passive: true });
    window.addEventListener("resize", update);
    update();
    return { update };
  }

  // ------------------------------------------------------------- copy buttons
  async function copy(text) {
    try { await navigator.clipboard.writeText(text); return true; }
    catch (_) {
      const ta = document.createElement("textarea");
      ta.value = text; ta.setAttribute("readonly", ""); ta.style.position = "fixed"; ta.style.opacity = "0";
      document.body.appendChild(ta); ta.select();
      let ok = false; try { ok = document.execCommand("copy"); } catch (_) {}
      ta.remove();
      return ok;
    }
  }
  function enhanceCode(root = document) {
    root.querySelectorAll("pre:not([data-copy])").forEach((pre) => {
      pre.setAttribute("data-copy", "");
      if (!pre.textContent.trim()) return;
      const wrap = document.createElement("div");
      wrap.className = "copy-wrap";
      pre.parentNode.insertBefore(wrap, pre);
      wrap.appendChild(pre);
      const b = document.createElement("button");
      b.type = "button"; b.className = "copy-btn"; b.textContent = "Copy";
      b.setAttribute("aria-label", "Copy code to clipboard");
      b.addEventListener("click", async () => {
        const ok = await copy(pre.innerText.replace(/^\$ /gm, ""));
        b.textContent = ok ? "Copied" : "Press Ctrl+C";
        b.classList.toggle("done", ok);
        live(ok ? "Copied to clipboard" : "Couldn't copy automatically. Select the text and press Ctrl+C.");
        if (!ok) { const r = document.createRange(); r.selectNodeContents(pre); const s = getSelection(); s.removeAllRanges(); s.addRange(r); }
        setTimeout(() => { b.textContent = "Copy"; b.classList.remove("done"); }, 1800);
      });
      wrap.appendChild(b);
    });
  }
  let liveEl;
  function live(msg) {
    if (!liveEl) { liveEl = document.createElement("div"); liveEl.setAttribute("aria-live", "polite"); liveEl.className = "sr-only"; liveEl.style.cssText = "position:absolute;width:1px;height:1px;overflow:hidden;clip:rect(0 0 0 0)"; document.body.appendChild(liveEl); }
    liveEl.textContent = ""; setTimeout(() => (liveEl.textContent = msg), 30);
  }

  // ------------------------------------------------------------- password fields
  function enhancePasswords(root = document) {
    root.querySelectorAll('input[type="password"]:not([data-pw])').forEach((inp) => {
      inp.setAttribute("data-pw", "");
      const wrap = document.createElement("div");
      wrap.className = "pw-wrap";
      inp.parentNode.insertBefore(wrap, inp);
      wrap.appendChild(inp);
      const b = document.createElement("button");
      b.type = "button"; b.className = "icon-btn";
      b.setAttribute("aria-controls", inp.id || "");
      const setState = (shown) => {
        inp.type = shown ? "text" : "password";
        b.setAttribute("aria-pressed", String(shown));
        const what = ((inp.labels && inp.labels[0] && inp.labels[0].textContent) || "password").trim().toLowerCase();
        b.setAttribute("aria-label", `${shown ? "Hide" : "Show"} ${what}`);
        b.innerHTML = `${ICONS.eye}<span>${shown ? "Hide" : "Show"}</span>`;
      };
      b.addEventListener("click", () => { setState(inp.type === "password"); inp.focus(); });
      setState(false);
      wrap.appendChild(b);
      inp.form?.addEventListener("submit", () => setState(false)); // never leave a key visible after use
    });
  }

  // ------------------------------------------------------------- skip links (work with hash routers)
  document.addEventListener("click", (e) => {
    const a = e.target.closest("a.skip");
    if (!a) return;
    const target = document.getElementById(a.getAttribute("href").slice(1));
    if (!target) return;
    e.preventDefault();
    if (!target.hasAttribute("tabindex")) target.setAttribute("tabindex", "-1");
    target.focus();
    target.scrollIntoView({ block: "start" });
  });

  window.crashlabUI = { theme, themeButton, confirm, openSearch, searchButton, floaters, enhanceCode, enhancePasswords, copy, live, esc };
})();
