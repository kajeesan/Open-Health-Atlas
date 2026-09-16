/* Theme picker: 12 Hermes Hi-Fi themes via [data-theme] on <html>, choice
   persisted in localStorage. Fires a "themechange" event so charts re-render
   with the new palette. Also owns the settings drawer + mobile "More" sheet. */
(function () {
  const root = document.documentElement;
  const THEMES = ["paper", "ember", "alpine", "verdant", "sunbeam", "ridge",
                  "grove", "timber", "glacier", "canyon", "cyber", "cosmos"];

  /* migrate pre-redesign stored values ("dark"/"light") to the new default */
  const stored = localStorage.getItem("panel-theme");
  if (THEMES.includes(stored)) root.dataset.theme = stored;
  else root.dataset.theme = "paper";

  function markSwatches() {
    document.querySelectorAll("[data-theme-btn]").forEach((b) => {
      b.classList.toggle("on", b.dataset.themeBtn === root.dataset.theme);
    });
  }

  function setTheme(t) {
    if (!THEMES.includes(t)) return;
    root.dataset.theme = t;
    localStorage.setItem("panel-theme", t);
    markSwatches();
    window.dispatchEvent(new CustomEvent("themechange", { detail: t }));
  }

  document.querySelectorAll("[data-theme-btn]").forEach((b) => {
    b.addEventListener("click", () => setTheme(b.dataset.themeBtn));
  });
  markSwatches();

  /* Settings drawer */
  const overlay = document.getElementById("settings-overlay");
  function openSettings(open) {
    if (!overlay) return;
    overlay.classList.toggle("open", open);
    overlay.setAttribute("aria-hidden", String(!open));
  }
  for (const id of ["settings-btn", "settings-btn-mobile"]) {
    const el = document.getElementById(id);
    if (el) el.addEventListener("click", () => openSettings(true));
  }
  if (overlay) {
    overlay.querySelectorAll("[data-close-settings]").forEach((el) => {
      el.addEventListener("click", () => openSettings(false));
    });
    document.addEventListener("keydown", (e) => {
      if (e.key === "Escape") openSettings(false);
    });
  }

  /* Mobile "More" bottom sheet */
  const moreBtn = document.getElementById("more-btn");
  const sheet = document.getElementById("more-sheet");
  if (moreBtn && sheet) {
    moreBtn.addEventListener("click", () => {
      const open = sheet.hidden;
      sheet.hidden = !open;
      moreBtn.setAttribute("aria-expanded", String(open));
    });
    sheet.addEventListener("click", (e) => {
      if (e.target === sheet) { sheet.hidden = true; moreBtn.setAttribute("aria-expanded", "false"); }
    });
  }
})();
