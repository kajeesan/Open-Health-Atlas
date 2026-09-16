/* Shared READ-ONLY renderers for the pain physio-loop (§3g) and mobility (§3h)
   lens state. Extracted from training.js (Task C) so the Body-page cockpit AND
   the Pain/Mobility workspace pages paint the SAME engine payload through ONE
   source — no divergence. Determinism law: every candidate cause, support tag,
   citation, boundary line, mobility value/norm/caveat comes from the engine's
   `muscle-map --lens <name>` payload VERBATIM; this file only lays it out with
   textContent (never innerHTML), so a citation string can't break into markup.
   Both renderers target the fixed card ids (mf-loop* / mf-mobility*) — whichever
   page includes that card markup gets it populated. */
(function () {
  const SUPPORT_LABEL = { supported: "consistent with", against: "argued against", untested: "untested" };
  function el(tag, cls, text) {
    const n = document.createElement(tag);
    if (cls) n.className = cls;
    if (text != null) n.textContent = text;
    return n;
  }

  // §3g pain lens: render the READ-ONLY physio-loop card from the engine payload
  // VERBATIM. Built with the DOM (textContent, not innerHTML) so a citation or
  // note string can never break out into markup.
  function renderPhysioLoop(data) {
    const card = document.getElementById("mf-loop");
    if (!card) return;
    card.hidden = false;
    document.getElementById("mf-cv").textContent = data.cv_note || "";
    const boundary = document.getElementById("mf-boundary");
    boundary.textContent = data.boundary_note || "";
    boundary.hidden = !data.boundary_note;
    const loop = data.loop || [];
    document.getElementById("mf-loop-count").textContent =
      loop.length ? loop.length + " painful area" + (loop.length > 1 ? "s" : "") : "no pain logged";
    const body = document.getElementById("mf-loop-body");
    if (!loop.length) {
      body.replaceChildren(el("p", "muted note-txt m0",
        "No pain logged in this window. Tell the coach if something hurts — the figure and this card stay read-only."));
      return;
    }
    body.replaceChildren(...loop.map((area) => {
      const box = el("div", "note-card");   // reuse the existing inset-box style
      const head = el("div", "row between wrap");
      head.append(el("span", "label ink",
        area.region.replace(/-/g, " ") + (area.side !== "central" ? " · " + area.side : "")));
      head.append(el("span", "chip", "NRS " + area.nrs + " · " + area.status));
      box.append(head);
      if (area.boundary_flags && area.boundary_flags.length)
        box.append(el("p", "note-txt strong m0 mt-1",
          "model boundary: " + area.boundary_flags.join(", ")));
      box.append(el("div", "muted micro mt-1", area.note));   // "not a diagnosis"
      (area.causes || []).forEach((c) => {
        const row = el("div", "mt-2");
        const t = el("div", "row gap-2");
        t.append(el("span", "strong", c.cause));
        t.append(el("span", "chip", SUPPORT_LABEL[c.support] || c.support));
        row.append(t);
        (c.evidence || []).forEach((e) => row.append(el("div", "muted micro", "· " + e)));
        const drills = (c.drills || []).map((d) => d.name + (d.dose ? " (" + d.dose + ")" : "")).join("; ");
        if (drills) row.append(el("div", "micro mt-1", "first-line load: " + drills));
        row.append(el("div", "muted micro", "cite: " + c.cite));
        box.append(row);
      });
      if (area.trials && area.trials.length) {
        const tr = el("div", "mt-2");
        tr.append(el("span", "label ink", "trials"));
        area.trials.forEach((t) => tr.append(
          el("div", "muted micro", "· " + t.date + " " + t.drill + " → " + t.response)));
        box.append(tr);
      }
      return box;
    }));
  }

  // §3h mobility lens: render the READ-ONLY mobility detail card from the engine
  // payload VERBATIM (textContent, never innerHTML — a citation string can't
  // break into markup). Every value/status/norm/caveat is the engine's; this
  // only lays it out.
  const MOB_STATUS_LABEL = { restricted: "restricted", normal: "within norm", untested: "untested" };
  function fmtMobValue(unit, value) {
    if (value == null) return "—";
    if (unit === "pass-fail") return value ? "pass" : "fail";
    return value + " " + unit;
  }
  function renderMobility(data) {
    const card = document.getElementById("mf-mobility");
    if (!card) return;
    card.hidden = false;
    document.getElementById("mf-mobility-flex").textContent = data.flexibility_note || "";
    const tests = data.tests || [];
    const tested = tests.filter((t) => (t.sides || []).length);
    document.getElementById("mf-mobility-count").textContent =
      tested.length + " / " + tests.length + " tested";
    const body = document.getElementById("mf-mobility-body");
    body.replaceChildren(...tests.map((t) => {
      const box = el("div", "note-card");
      const head = el("div", "row between wrap");
      head.append(el("span", "label ink", t.name));
      // untested tests show which sides are missing; tested ones show status chips
      if (!(t.sides || []).length) {
        head.append(el("span", "chip", "not tested"));
      } else {
        const chips = el("div", "row gap-1");
        t.sides.forEach((s) => chips.append(el("span", "chip",
          (s.side === "bilateral" ? "" : s.side + " ") +
          fmtMobValue(t.unit, s.value) + " · " + (MOB_STATUS_LABEL[s.status] || s.status))));
        head.append(chips);
      }
      box.append(head);
      box.append(el("div", "muted micro mt-1", "cite: " + t.cite));
      if (t.caveat) box.append(el("div", "muted micro", "caveat: " + t.caveat));
      if (t.flexibility_axis)
        box.append(el("div", "muted micro", "also the radar's Flexibility axis"));
      return box;
    }));
  }

  window.HermesPhysio = { renderPhysioLoop, renderMobility };
})();
