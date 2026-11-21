/* Runs before the page paints, so there is no flash of the wrong theme.
   Choice order: what the visitor picked last time, then their OS setting. */
(function () {
  var KEY = "crashlab.theme", t = null;
  try { t = localStorage.getItem(KEY); } catch (e) {}
  if (t !== "light" && t !== "dark") {
    // Report paper by default; the evening docket only when the OS asks for dark.
    t = window.matchMedia && window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
  }
  document.documentElement.setAttribute("data-theme", t);
})();
