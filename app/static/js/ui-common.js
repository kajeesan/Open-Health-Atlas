/* Shared Hi-Fi UI behaviors: range dropdowns (.dd) and tab rows (.tabrow →
   .subview panes). Markup contracts come from the design file:
     <div class="dd" data-dd="NAME"><button class="ddbtn">Label <span class="cv">▾</span></button>
       <div class="ddmenu"><button data-val="…">…</button>…</div></div>
     <div class="tabrow" data-group="G"><button data-tab="t1" class="on">…</button>…</div>
     <div class="subview on" data-group="G" data-pane="t1">…</div>
   Pages read state via HermesUI.range(name) and subscribe with onRange/onTab. */
(function () {
  const rangeState = {};   // dd name -> current value
  const rangeSubs = {};    // dd name -> [fn]
  const tabSubs = {};      // group -> [fn]

  function setOpen(dropdown, open) {
    dropdown.classList.toggle("open", open);
    dropdown.querySelector(".ddbtn").setAttribute("aria-expanded", String(open));
  }

  function closeAll() {
    document.querySelectorAll(".dd.open").forEach((d) => setOpen(d, false));
  }

  document.addEventListener("keydown", (event) => {
    if (event.key !== "Escape") return;
    const open = document.querySelector(".dd.open");
    if (!open) return;
    closeAll();
    open.querySelector(".ddbtn").focus();
    event.preventDefault();
  });

  document.addEventListener("click", (e) => {
    const btn = e.target.closest(".dd .ddbtn");
    const opt = e.target.closest(".ddmenu button");
    const tab = e.target.closest(".tabrow button");

    if (btn) {
      const dd = btn.closest(".dd");
      const wasOpen = dd.classList.contains("open");
      closeAll();
      if (!wasOpen) setOpen(dd, true);
      return;
    }
    if (opt) {
      const dd = opt.closest(".dd");
      const name = dd.dataset.dd;
      const val = opt.dataset.val;
      rangeState[name] = val;
      dd.querySelector(".ddbtn").innerHTML = val + ' <span class="cv">▾</span>';
      dd.querySelectorAll(".ddmenu button").forEach((b) => b.classList.toggle("on", b === opt));
      setOpen(dd, false);
      dd.querySelector(".ddbtn").focus();
      (rangeSubs[name] || []).forEach((fn) => fn(val));
      return;
    }
    if (tab) {
      const row = tab.closest(".tabrow");
      const group = row.dataset.group;
      row.querySelectorAll("button").forEach((b) => b.classList.toggle("on", b === tab));
      document.querySelectorAll(`.subview[data-group="${group}"]`).forEach((p) => {
        p.classList.toggle("on", p.dataset.pane === tab.dataset.tab);
      });
      (tabSubs[group] || []).forEach((fn) => fn(tab.dataset.tab));
      return;
    }
    closeAll();
  });

  /* seed initial dd state from the .on option (or button label) */
  function seed() {
    document.querySelectorAll(".dd[data-dd]").forEach((dd) => {
      setOpen(dd, dd.classList.contains("open"));
      const on = dd.querySelector(".ddmenu button.on");
      if (on && !(dd.dataset.dd in rangeState)) rangeState[dd.dataset.dd] = on.dataset.val;
    });
  }
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", seed);
  else seed();

  window.HermesUI = {
    range: (name) => rangeState[name],
    onRange: (name, fn) => { (rangeSubs[name] = rangeSubs[name] || []).push(fn); },
    onTab: (group, fn) => { (tabSubs[group] = tabSubs[group] || []).push(fn); },
  };
})();
