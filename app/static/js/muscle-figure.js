/* Muscle figure (§3f) — the ONE reusable front/back body-map renderer for
   every placement (Body cockpit, dashboard glance, muscle-group sub-pages).
   Draws the vendored body-muscles SVG path data (window.BodyMuscles —
   audited, sha-pinned in vendor/VENDOR.md, byte-unmodified; we consume its
   exported FRONT_MUSCLES/BACK_MUSCLES data, NOT its BodyChart class, so
   region colors stay CSS-class/theme driven and re-theme for free).
   Determinism law: the engine's `muscle-map` payload decides every region's
   level/basis/approx — this file only paints classes and titles. */
(function () {
  const BM = window.BodyMuscles;
  if (!BM) return;
  const SVG_NS = "http://www.w3.org/2000/svg";
  // per-view viewBox halves of the shared sheet (values from the vendored
  // BodyChart source, which renders the same data)
  const VIEWBOX = { front: "0 0 35 93", back: "37 0 35 93" };

  /* create(container, opts) -> {update(payload, {scopeGroup})}
     opts: view "front"|"back"; interactive: click/keyboard enabled;
     onRegionClick(id, name, regionPayload). */
  function create(container, opts) {
    const defs = opts.view === "back" ? BM.BACK_MUSCLES : BM.FRONT_MUSCLES;
    const svg = document.createElementNS(SVG_NS, "svg");
    svg.setAttribute("viewBox", VIEWBOX[opts.view] || VIEWBOX.front);
    svg.setAttribute("class", "mf-svg");
    svg.setAttribute("role", "img");
    svg.setAttribute("aria-label",
      (opts.view === "back" ? "Posterior" : "Anterior") + " muscle map");
    let last = null;                 // latest payload, for click callbacks
    const nodes = new Map();         // region id -> {path, title, name}
    defs.forEach((m) => {
      const p = document.createElementNS(SVG_NS, "path");
      p.setAttribute("d", m.path);
      p.setAttribute("class", "mf-region mf-neutral");
      const t = document.createElementNS(SVG_NS, "title");
      t.textContent = m.name;
      p.appendChild(t);
      if (opts.interactive) {
        // listeners attach here, but role/tabindex are granted in update():
        // only regions the payload tracks become buttons — non-muscle
        // silhouette parts must not collect dead keyboard stops (review)
        const fire = () => opts.onRegionClick &&
          opts.onRegionClick(m.id, m.name, last && last.regions ? last.regions[m.id] : null);
        p.addEventListener("click", fire);
        p.addEventListener("keydown", (e) => {
          if (e.key === "Enter" || e.key === " ") { e.preventDefault(); fire(); }
        });
      }
      svg.appendChild(p);
      nodes.set(m.id, { path: p, title: t, name: m.name });
    });
    container.replaceChildren(svg);

    // lens:status → CSS class + title text, all from engine payload fields.
    // status-lens payloads (strength-balance, pain) carry {status,…} regions +
    // a `legend` [{status,label}]; the status label comes from the payload's
    // own legend so wording can't drift from the engine (determinism law: no
    // judgement made here). The CSS prefix is per-lens; each lens's status set
    // is distinct so red means exactly one thing within its own legend.
    const STATUS_PREFIX = { "strength-balance": "mf-sb-", "pain": "mf-pain-", "mobility": "mf-mob-" };
    function statusLabel(payload, status) {
      const hit = (payload.legend || []).find((l) => l.status === status);
      return hit ? hit.label : status;
    }
    function paint(n, reg, payload) {
      const prefix = STATUS_PREFIX[payload.lens];
      if (prefix) {
        const status = reg.status || "untested";
        n.path.classList.add(prefix + status.replace(/_/g, "-"));
        if (reg.boundary) n.path.classList.add("mf-boundary");   // pain red-flag hatch
        const lines = [n.name + " — " + statusLabel(payload, status)];
        (reg.tips || []).forEach((t) => lines.push(t));
        if (reg.boundary) lines.push("carries a signal outside this model — see the loop card");
        if (reg.approx) lines.push("shown on nearest drawn region");
        n.title.textContent = lines.join("\n");
        return;
      }
      n.path.classList.add("mf-l" + (reg.level || 0));
      n.title.textContent = n.name + " — " + reg.eff_sets + " eff sets/" +
        payload.window_days + "d" +
        (reg.basis === "coarse" ? " (coarse tag)" :
          reg.basis === "mixed" ? " (partly coarse tag)" : "") +
        (reg.approx ? " · shown on nearest drawn region" : "");
    }

    function update(payload, view) {
      last = payload;
      const scope = view && view.scopeGroup;
      const nonMuscle = new Set(payload.non_muscle || []);
      nodes.forEach((n, id) => {
        const reg = (payload.regions || {})[id];
        n.path.setAttribute("class", "mf-region");
        if (!reg || nonMuscle.has(id)) {
          n.path.classList.add("mf-neutral");
          n.path.removeAttribute("role");
          n.path.removeAttribute("tabindex");
          n.title.textContent = n.name;
          return;
        }
        // Actionable regions become keyboard buttons. Default: only regions
        // with a group to drill into (pain-lens joints carry group:null and
        // must not be a 'dead keyboard stop'). A caller may pass
        // opts.actionable(reg, payload) to widen this — REPORT lenses make every
        // painted region actionable so any spot (incl. joints) opens its
        // workspace (Task C). The click listener fires regardless; this governs
        // role/tabindex + cursor only.
        const isActionable = opts.actionable ? opts.actionable(reg, payload) : !!reg.group;
        if (opts.interactive && isActionable) {
          n.path.setAttribute("role", "button");
          n.path.setAttribute("tabindex", "0");
        } else {
          n.path.removeAttribute("role");
          n.path.removeAttribute("tabindex");
        }
        if (reg.approx) n.path.classList.add("mf-approx");
        if (scope && reg.group !== scope) n.path.classList.add("mf-dim");
        paint(n, reg, payload);
      });
    }
    // mark(idsSet) — toggle a 'selected' outline on the regions the caller
    // picked (Task C workspace multi-select). Purely visual, independent of the
    // lens paint; a null/empty set clears all selection outlines.
    function mark(selected) {
      nodes.forEach((n, id) => {
        const on = !!(selected && selected.has(id));
        n.path.classList.toggle("mf-selected", on);
        // The workspace is a multi-select control: expose the same state its
        // accent outline shows. Neutral silhouette paths are not buttons and
        // therefore must not gain aria-pressed.
        if (n.path.getAttribute("role") === "button") {
          n.path.setAttribute("aria-pressed", on ? "true" : "false");
        } else {
          n.path.removeAttribute("aria-pressed");
        }
      });
    }
    return { update, mark };
  }

  /* legend(el, payload) — swatch chips for the payload's own legend
     (status lenses carry `legend` [{status,label}]; activation carries `levels`
     [{level,label}]), so a color is always defined next to the figure it colors
     and labels always come from the engine. The swatch prefix is per-lens. */
  const LEGEND_PREFIX = { "strength-balance": "mf-sb-", "pain": "mf-pain-", "mobility": "mf-mob-" };
  function legend(el, payload) {
    if (!el) return;
    const prefix = LEGEND_PREFIX[payload.lens];
    const items = (payload.legend && prefix)
      ? payload.legend.map((l) => ({
          cls: prefix + l.status.replace(/_/g, "-"), label: l.label }))
      : (payload.levels || []).map((l) => ({
          cls: "mf-l" + l.level, label: l.label }));
    el.replaceChildren(...items.map((l) => {
      const item = document.createElement("span");
      item.className = "mf-leg";
      const sw = document.createElement("i");
      sw.className = "mf-swatch " + l.cls;
      const label = document.createElement("span");
      label.textContent = l.label;
      item.append(sw, label);
      return item;
    }));
  }

  window.MuscleFigure = { create, legend };
})();
