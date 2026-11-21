/* Public pages: header tools, mobile menu, floating buttons, FAQ, form validation,
   UTM capture, cookie consent and Google Analytics.
   Analytics never loads until the visitor clicks Accept (GA4 consent mode, default denied). */
(() => {
  "use strict";
  const body = document.body;
  const UI = window.crashlabUI;
  const path = location.pathname;

  // ---- header tools: search + theme
  const tools = document.getElementById("head-tools");
  if (UI && tools) {
    tools.append(
      UI.searchButton(() => UI.openSearch(() => fetch("/search-index.json").then((r) => r.json()),
        { placeholder: "Search pages and questions", onGo: (url) => { location.href = url; openFaqFromHash(); } })),
      UI.themeButton());
  }

  // ---- mobile menu
  const btn = document.getElementById("site-menu-btn");
  const nav = document.getElementById("site-nav");
  if (btn && nav) {
    const set = (open) => {
      nav.classList.toggle("open", open);
      btn.setAttribute("aria-expanded", String(open));
      btn.setAttribute("aria-label", open ? "Close menu" : "Open menu");
    };
    btn.addEventListener("click", () => set(!nav.classList.contains("open")));
    nav.addEventListener("click", (e) => { if (e.target.closest("a")) set(false); });
    document.addEventListener("keydown", (e) => { if (e.key === "Escape" && nav.classList.contains("open")) { set(false); btn.focus(); } });
  }

  // ---- floating buttons: back to top + contact (not on the contact pages themselves)
  if (UI) {
    let contact = null;
    if (!path.startsWith("/contact")) {
      contact = document.createElement("a");
      contact.className = "fab primary"; contact.href = "/contact";
      contact.setAttribute("aria-label", "Contact us");
      contact.innerHTML = '<svg viewBox="0 0 20 20" aria-hidden="true"><path d="M3 4.5h14v9H8l-4 3v-3H3z" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linejoin="round"/></svg><span class="label">Contact us</span>';
    }
    UI.floaters(contact);
    UI.enhanceCode();
  }

  // ---- FAQ: open the question a link or search result points to; expand / collapse all
  function openFaqFromHash() {
    const id = decodeURIComponent(location.hash.slice(1));
    const el = id && document.getElementById(id);
    if (el && el.tagName === "DETAILS") { el.open = true; el.querySelector("summary").focus({ preventScroll: true }); el.scrollIntoView({ block: "center" }); }
  }
  window.addEventListener("hashchange", openFaqFromHash);
  openFaqFromHash();
  const faq = document.querySelector(".faq");
  if (faq) {
    const tools = document.createElement("div");
    tools.className = "faq-tools";
    const b = document.createElement("button");
    b.type = "button"; b.className = "btn";
    const items = () => [...faq.querySelectorAll("details")];
    const label = () => { const all = items().every((d) => d.open); b.textContent = all ? "Collapse all" : "Expand all"; b.setAttribute("aria-expanded", String(all)); };
    b.addEventListener("click", () => { const open = !items().every((d) => d.open); items().forEach((d) => (d.open = open)); label(); });
    faq.addEventListener("toggle", label, true);
    label();
    tools.appendChild(b);
    faq.parentNode.insertBefore(tools, faq);
  }

  // ---- one orchestrated entrance: the hero terminal plays its lines once
  const term = document.querySelector(".terminal");
  if (term && !matchMedia("(prefers-reduced-motion: reduce)").matches) {
    term.querySelectorAll(".ln").forEach((l, i) => (l.style.animationDelay = `${120 + i * 110}ms`));
    term.classList.add("play");
  }

  // ---- UTM tags: keep the campaign that brought the visitor (this tab only), then tidy the URL
  const UTM = ["utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content"];
  const UTM_OK = /^[\w .+\-%/]{1,100}$/;
  const params = new URLSearchParams(location.search);
  const found = {};
  UTM.forEach((k) => { const v = params.get(k); if (v && UTM_OK.test(v)) found[k] = v; });
  let utm = {};
  try { utm = JSON.parse(sessionStorage.getItem("crashlab.utm") || "{}"); } catch (_) {}
  if (Object.keys(found).length) {
    utm = found; // the latest campaign link wins
    try { sessionStorage.setItem("crashlab.utm", JSON.stringify(utm)); } catch (_) {}
    UTM.forEach((k) => params.delete(k));
    const clean = location.pathname + (params.toString() ? "?" + params : "") + location.hash;
    history.replaceState(null, "", clean); // shared links stay clean; the tags are already saved
  }
  const form = document.getElementById("contact-form");
  if (form) UTM.forEach((k) => { const f = form.elements[k]; if (f && utm[k] && !f.value) f.value = utm[k]; });

  // ---- contact form: inline validation before sending (the server checks again)
  if (form) {
    const rules = {
      name: (v) => (v.trim() ? "" : "Enter your name."),
      email: (v) => (/^[^@\s<>"']{1,64}@[^@\s<>"']{1,190}\.[A-Za-z]{2,24}$/.test(v.trim()) ? "" : "Enter a valid email address, like name@example.com."),
      message: (v) => { const n = v.trim().length; return n < 10 ? `Your message is ${n} characters; write at least 10.` : n > 5000 ? "Keep your message under 5,000 characters." : ""; },
    };
    let tried = false;
    const show = (name, msg) => {
      const el = form.elements[name];
      let err = document.getElementById(`${name}-error`);
      const hint = document.getElementById(`${name}-hint`);
      if (msg) {
        if (!err) { err = document.createElement("div"); err.className = "field-error"; err.id = `${name}-error`; el.closest(".field").appendChild(err); }
        err.textContent = msg;
        el.setAttribute("aria-invalid", "true");
      } else {
        err?.remove();
        el.removeAttribute("aria-invalid");
      }
      el.setAttribute("aria-describedby", [hint && hint.id, msg && `${name}-error`].filter(Boolean).join(" "));
      return !msg;
    };
    const check = (name) => show(name, rules[name](form.elements[name].value));
    Object.keys(rules).forEach((n) => {
      form.elements[n].addEventListener("blur", (e) => {
        // moving to the Send button: let the submit handler validate, otherwise a new error line
        // pushes the button down mid-click and the click misses it
        if (e.relatedTarget && e.relatedTarget.type === "submit") return;
        if (tried || form.elements[n].value) check(n);
      });
      form.elements[n].addEventListener("input", () => { if (form.elements[n].getAttribute("aria-invalid")) check(n); });
    });
    form.addEventListener("submit", (e) => {
      tried = true;
      const bad = Object.keys(rules).filter((n) => !check(n));
      if (bad.length) {
        e.preventDefault();
        let sum = document.getElementById("form-error");
        if (!sum) { sum = document.createElement("div"); sum.id = "form-error"; sum.className = "error-summary"; sum.setAttribute("role", "alert"); sum.tabIndex = -1; form.parentNode.insertBefore(sum, form); }
        sum.innerHTML = "<b>Message not sent.</b> Please fix the highlighted fields.<ul>" +
          bad.map((n) => `<li><a href="#${n}">${UI ? UI.esc(rules[n](form.elements[n].value)) : ""}</a></li>`).join("") + "</ul>";
        form.elements[bad[0]].focus();
        return;
      }
      const sendBtn = form.querySelector("[type=submit]");
      sendBtn.disabled = true; sendBtn.setAttribute("aria-busy", "true"); sendBtn.textContent = "Sending…"; // no double submits
    });
    document.getElementById("form-error")?.focus(); // server-side errors: move focus to the summary
  }

  // ---- analytics with consent
  const GA = body.dataset.ga;
  if (!GA) return;
  const KEY = "crashlab.consent";
  const get = () => { try { return localStorage.getItem(KEY); } catch (_) { return null; } };
  const put = (v) => { try { localStorage.setItem(KEY, v); } catch (_) {} };

  window.dataLayer = window.dataLayer || [];
  function gtag() { window.dataLayer.push(arguments); }
  window.gtag = gtag;
  gtag("consent", "default", { analytics_storage: "denied", ad_storage: "denied", ad_user_data: "denied", ad_personalization: "denied" });

  let loaded = false;
  function load() {
    if (loaded) return;
    loaded = true;
    gtag("consent", "update", { analytics_storage: "granted" });
    const s = document.createElement("script");
    s.async = true;
    s.src = "https://www.googletagmanager.com/gtag/js?id=" + encodeURIComponent(GA);
    document.head.appendChild(s);
    gtag("js", new Date());
    // hand GA the campaign tags saved from the landing URL, so attribution survives the tidy URL
    const loc = new URL(location.href);
    Object.entries(utm).forEach(([k, v]) => loc.searchParams.set(k, v));
    gtag("config", GA, { anonymize_ip: true, page_location: loc.toString() });
    if (body.dataset.event) gtag("event", body.dataset.event, { form: "contact", ...utm }); // e.g. generate_lead
  }

  function banner() {
    if (document.getElementById("consent")) return;
    const el = document.createElement("div");
    el.className = "consent"; el.id = "consent";
    el.setAttribute("role", "dialog"); el.setAttribute("aria-label", "Cookie consent");
    el.innerHTML = '<p>We use Google Analytics cookies to count visits and improve this site, only if you agree. ' +
      'See our <a href="/privacy">privacy policy</a>.</p><div class="btn-row">' +
      '<button class="btn primary" type="button" data-c="granted">Accept</button>' +
      '<button class="btn" type="button" data-c="denied">Decline</button></div>';
    el.addEventListener("click", (e) => {
      const b = e.target.closest("button[data-c]");
      if (!b) return;
      put(b.dataset.c);
      el.remove();
      body.classList.remove("consent-open");
      UI && UI.live(b.dataset.c === "granted" ? "Analytics cookies accepted" : "Analytics cookies declined");
      if (b.dataset.c === "granted") load();
      else if (loaded) location.reload(); // withdrawing consent: reload without analytics
    });
    document.body.appendChild(el);
    body.classList.add("consent-open");
  }

  const choice = get();
  if (choice === "granted") load();
  else if (choice !== "denied") banner();
  document.getElementById("cookie-settings")?.addEventListener("click", banner);
})();
