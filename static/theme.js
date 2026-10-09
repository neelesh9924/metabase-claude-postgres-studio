// Runs in <head> before the first paint so the right theme shows at once.
// The choice ("auto" | "light" | "dark") lives in localStorage; "auto" follows the system.
(function () {
  var root = document.documentElement;
  function choice() {
    try { return localStorage.getItem("studio-theme") || "auto"; } catch (e) { return "auto"; }
  }
  function apply() {
    var c = choice();
    var dark = c === "dark" || (c === "auto" && window.matchMedia("(prefers-color-scheme: dark)").matches);
    root.dataset.theme = dark ? "dark" : "light";
  }
  window.studioTheme = {
    get: choice,
    set: function (c) {
      try { localStorage.setItem("studio-theme", c); } catch (e) { /* private mode: keep it for this page */ }
      apply();
    },
  };
  window.matchMedia("(prefers-color-scheme: dark)").addEventListener("change", apply);
  apply();
})();
