(function () {
  "use strict";
  if (!document.getElementById("descriptive-root")) return;
  const $ = (id) => document.getElementById(id);
  const csrf = document.querySelector('meta[name="csrf-token"]').content;
  const reasons = {
    source_incompatible: "The records use different sources and cannot be pooled for this comparison.",
    no_exposure_contrast: "The available comparison values do not form two different groups.",
    no_paired_observations: "No outcome records have a usable comparison record at this timing.",
    temporal_boundary: "This timing would cross the outcome's permitted time boundary.",
    timing_unavailable: "The recorded timing cannot support this comparison.",
    unsupported_one_day_alignment: "This measurement does not support the selected day offset.",
    slow_episode_no_generic_alignment: "These measurements use separate episodes and cannot be compared as daily records.",
    mechanical_tautology: "These measurements share the same underlying calculation.",
    composite_root: "These measurements share underlying inputs and cannot provide an independent comparison.",
    forbidden_relation: "The measurement definitions do not permit this comparison.",
    source_specific_hrv: "These heart-rate-variability measurements require a compatible source.",
    protocol_incompatible: "The measurement protocols differ, so these values cannot be pooled for a comparison.",
    protocol_unavailable: "The measurement protocol is unknown, so comparability cannot be established.",
  };
  let range = null;
  let pending = false;
  let catalog = [];
  let catalogReady = false;
  let catalogLoading = false;
  let exposures = [];
  let controller = null;
  let generation = 0;

  function el(tag, className, text) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined) node.textContent = String(text);
    return node;
  }

  function unitLabel(unit) {
    const scale = /^ordinal_(-?\d+)_(-?\d+)$/.exec(unit || "");
    return scale ? `${scale[1]}–${scale[2]} scale` : unit;
  }

  function value(number, unit = "") {
    if (number === null || number === undefined) return "Not available";
    const display = typeof number === "number"
      ? number.toLocaleString(undefined, { maximumFractionDigits: 3 }) : String(number);
    const label = unitLabel(unit);
    const suffix = label !== unit ? ` (${label})` : (unit ? " " + unit : "");
    return display + suffix;
  }

  function name(key) {
    const feature = catalog.find((item) => item.key === key);
    return feature ? feature.display_name : key;
  }

  function row(label, content) {
    const node = el("div", "finding-detail");
    node.append(el("span", "label", label), el("span", "", content));
    return node;
  }

  function evidence(label, packet) {
    const details = el("details", "evidence-details");
    details.append(el("summary", "", label), el("pre", "brief mt-2", JSON.stringify(packet, null, 2)));
    return details;
  }

  async function request(url, options = {}) {
    const response = await fetch(url, { credentials: "same-origin", ...options });
    if (response.status === 401) {
      location.assign("/login");
      throw new Error("Please sign in again.");
    }
    const data = await response.json().catch(() => ({}));
    if (!response.ok || data.ok === false) {
      const error = data.error;
      throw new Error(typeof error === "string" ? error : (error || {}).message || "Records could not be loaded. Try again.");
    }
    if (!data.result) throw new Error("The response was incomplete. Try again.");
    return data.result;
  }

  function updateControls() {
    const bounded = range && range.kind === "bounded";
    $("descriptive-explore").disabled = pending || !catalogReady || !bounded || !$("descriptive-outcome").value;
    $("descriptive-outcome").disabled = !catalogReady;
    $("descriptive-exposure").disabled = !catalogReady || exposures.length >= 8;
    $("descriptive-add").disabled = !catalogReady || exposures.length >= 8 || !$("descriptive-exposure").value;
    $("descriptive-explore").textContent = controller ? "Stop waiting" : "Explore available records";
    $("descriptive-window").textContent = bounded
      ? `Investigation window: ${range.from} through ${range.to}`
      : "Choose Day, Week, Month or Year above. Descriptive exploration needs a window of up to 366 days.";
  }

  function reset(message) {
    generation += 1;
    if (controller) controller.abort();
    controller = null;
    $("descriptive-results").replaceChildren();
    $("descriptive-results").setAttribute("aria-busy", "false");
    $("descriptive-status").textContent = message;
    updateControls();
  }

  function paintChoices() {
    const picker = $("descriptive-exposure");
    picker.replaceChildren(el("option", "", "Choose a measurement"));
    picker.children[0].value = "";
    catalog.filter((item) => item.roles.includes("exposure") && item.candidate_enabled
      && item.key !== $("descriptive-outcome").value && !exposures.includes(item.key))
      .forEach((item) => {
        const option = el("option", "", `${item.display_name} (${unitLabel(item.unit)})`);
        option.value = item.key;
        picker.appendChild(option);
      });
    picker.value = "";
    const selected = $("descriptive-selected");
    selected.replaceChildren();
    exposures.forEach((key) => {
      const item = el("div", "row wrap gap-2");
      const button = el("button", "btn quiet", "Remove");
      button.type = "button";
      button.setAttribute("aria-label", `Remove ${name(key)}`);
      button.addEventListener("click", () => {
        exposures = exposures.filter((item) => item !== key);
        paintChoices();
        reset("Comparison removed. Explore again to update the records.");
        $("descriptive-exposure").focus();
      });
      item.append(el("span", "note-txt", name(key)), button);
      selected.appendChild(item);
    });
    updateControls();
  }

  async function loadCatalog() {
    if (catalogLoading) return;
    catalogLoading = true;
    $("descriptive-retry-catalog").hidden = true;
    $("descriptive-status").textContent = "Loading registered measurements…";
    try {
      const items = [];
      const seen = new Set();
      let cursor = "0";
      do {
        if (seen.has(cursor)) throw new Error("The measurement list was incomplete. Retry measurements.");
        seen.add(cursor);
        const result = await request("/api/insights/descriptive-catalog?cursor=" + encodeURIComponent(cursor));
        if (!Array.isArray(result.facts) || !result.coverage) throw new Error("The measurement list was incomplete. Retry measurements.");
        items.push(...result.facts);
        cursor = result.coverage.next_cursor;
      } while (cursor !== null && cursor !== undefined);
      catalog = items.filter((item) => item.implemented
        && !["categorical", "episode", "config"].includes(item.value_kind)
        && !["static_config", "unavailable"].includes(item.temporal_type));
      const outcome = $("descriptive-outcome");
      outcome.replaceChildren();
      catalog.filter((item) => item.roles.includes("outcome")).forEach((item) => {
        const option = el("option", "", `${item.display_name} (${unitLabel(item.unit)})`);
        option.value = item.key;
        outcome.appendChild(option);
      });
      if (!outcome.children.length) throw new Error("No supported outcome measurements are connected.");
      outcome.value = catalog.some((item) => item.key === "subjective.energy" && item.roles.includes("outcome"))
        ? "subjective.energy" : outcome.children[0].value;
      catalogReady = true;
      paintChoices();
      $("descriptive-status").textContent = "Choose an outcome and optional comparisons, then explore the available records.";
    } catch (error) {
      catalogReady = false;
      $("descriptive-status").textContent = "Measurements unavailable: " + error.message;
      $("descriptive-outcome").replaceChildren(el("option", "", "Measurements unavailable"));
      $("descriptive-retry-catalog").hidden = false;
    } finally {
      catalogLoading = false;
      updateControls();
    }
  }

  function summaryText(summary, unit) {
    if (!summary || !summary.n) return "No comparable recorded values.";
    if (summary.mean === null || summary.mean === undefined) return `${summary.n} recorded units; a comparable summary is not available.`;
    return `Mean ${value(summary.mean, unit)} · median ${value(summary.median, unit)} · range ${value(summary.min)} to ${value(summary.max, unit)} · ${summary.n} recorded units.`;
  }

  function renderFeature(feature) {
    const section = el("section");
    const partialFacts = feature.partial_recorded_facts || [];
    const coverage = partialFacts.length
      ? `${feature.observed_days} of ${feature.range_days} days have comparison-usable records; ${feature.days_without_usable_observation} days lack a comparable daily value. ${feature.observed_units} comparable day units.`
      : `${feature.observed_days} of ${feature.range_days} days have usable records; ${feature.days_without_usable_observation} days are unknown. ${feature.observed_units} recorded ${feature.unit_type === "episode" ? "episodes" : "day units"}.`;
    section.append(
      el("h3", "finding-title", feature.display_name),
      el("p", "note-txt mt-1", coverage)
    );
    if (partialFacts.length) {
      section.appendChild(el("h4", "finding-title", "Recorded subtotals"));
      const list = el("ul", "plain-list");
      partialFacts.forEach((fact) => list.appendChild(el("li", "note-txt",
        `${fact.value} ${unitLabel(fact.unit)} recorded on ${fact.observed_at}. Source: ${fact.source}.`)));
      section.append(list,
        el("p", "note-txt", "Recorded subtotal; full-day coverage unknown. These values are not daily intake totals or inputs to comparisons."),
        el("p", "muted micro", `Showing ${feature.partial_recorded_facts_shown} of ${feature.partial_recorded_observation_rows} recorded subtotals. Comparable summaries below exclude these records.`));
    }
    if (feature.summary) section.appendChild(el("p", "note-txt", summaryText(feature.summary, feature.unit)));
    else {
      const compatibility = (feature.comparability || {}).status;
      section.appendChild(el("p", "note-txt", reasons[compatibility] || "Sources differ, so no pooled summary is shown."));
      (feature.source_summaries || []).forEach((source) => section.appendChild(
        el("p", "note-txt", `${source.source}: ${summaryText(source, feature.unit)}`)
      ));
    }
    const period = feature.period_compare || {};
    const periods = el("details", "evidence-details");
    periods.appendChild(el("summary", "", "Earlier and later records"));
    periods.appendChild(el("p", "note-txt", `Calendar split: ${period.split_date || "Not available"}. This describes the selected periods; it does not establish a trend.`));
    if (period.baseline && period.current) {
      periods.append(row("Before split", summaryText(period.baseline, feature.unit)),
        row("From split", summaryText(period.current, feature.unit)));
    }
    const periodMessage = period.status === "observed_difference"
      ? `Later minus earlier mean: ${value(period.mean_difference, feature.unit)}.`
      : period.status === "no_clear_pattern"
        ? "No clear pattern: the observed period means are equal. This does not establish no relationship."
        : reasons[period.status] || "Not comparable: one or both periods have no comparison-usable records.";
    periods.appendChild(el("p", "note-txt", periodMessage));
    section.append(periods, evidence("Recorded sample, coverage and source identities", feature));
    return section;
  }

  function appendContrast(section, comparison, sourceGroup = false) {
    const groups = comparison.groups || [];
    if (groups.length) {
      const binary = comparison.grouping === "recorded_zero_vs_one";
      const groupRows = el("div", "finding-details mt-2");
      groups.forEach((group) => {
        const lower = group.label === "lower";
        const label = binary ? (lower ? "Recorded zero" : "Recorded one")
          : `${lower ? "At or below" : "Above"} ${value(comparison.exposure_threshold, comparison.exposure_unit)}`;
        groupRows.appendChild(row(label, `Outcome mean ${value(group.outcome.mean, comparison.outcome_unit)} · ${group.outcome.n} paired units`));
      });
      section.appendChild(groupRows);
    }
    if (comparison.pattern_status === "observed_difference") {
      const qualifier = sourceGroup ? "Observed difference within this source group" : "Observed difference";
      section.appendChild(el("p", "note-txt", `${qualifier}: higher-group minus lower-group outcome mean is ${value(comparison.mean_difference, comparison.outcome_unit)}. Statistical confidence is not assessed.`));
    } else if (comparison.pattern_status === "no_clear_pattern") {
      section.appendChild(el("p", "note-txt", "No clear pattern: the observed group means are equal. This does not establish no relationship."));
    } else {
      section.appendChild(el("p", "note-txt", "Not comparable: " + (reasons[comparison.reason]
        || "The available records do not support this contrast. See the comparison evidence for the recorded reason.")));
    }
    const counter = comparison.counterexamples;
    if (counter && counter.lower_group !== null && counter.higher_group !== null) {
      section.appendChild(el("p", "note-txt", `Counterexamples: ${counter.lower_group} in the lower group; ${counter.higher_group} in the higher group. ${counter.definition}`));
    } else if (comparison.pattern_status === "no_clear_pattern") {
      section.appendChild(el("p", "note-txt", "Directional counterexamples are not assessed when the group means are equal."));
    }
  }

  function renderComparison(comparison, packet) {
    const section = el("section");
    const timing = packet.lag_days === 0 ? "same day" : `${packet.lag_days} day(s) earlier`;
    section.append(el("h3", "finding-title", `${name(comparison.exposure_key)} and ${name(comparison.outcome_key)}`),
      el("p", "muted micro mt-1", `Comparison records: ${timing}.`));
    const notCompared = comparison.missing_exposure_units === null || comparison.missing_exposure_units === undefined;
    section.appendChild(el("p", "note-txt", notCompared
      ? `${comparison.eligible_outcome_units} outcome units available; alignment was not assessed for this comparison.`
      : `${comparison.paired_units} paired units out of ${comparison.eligible_outcome_units} outcome units; ${comparison.missing_exposure_units} have no usable comparison record.`));
    const sourceGroups = comparison.source_comparisons || [];
    if (comparison.reason === "source_incompatible" && sourceGroups.length) {
      section.appendChild(el("p", "note-txt", "No pooled comparison: the records use different sources. Each comparison below uses only its matching source group."));
      section.appendChild(el("p", "muted micro", `Showing ${comparison.source_comparisons_shown} of ${comparison.source_comparisons_total} source groups, selected by paired-record count. These groups are not independent corroboration.`));
      const groups = el("div", "vstack mt-3");
      sourceGroups.forEach((group) => {
        const source = group.source_identity || {};
        const block = el("section", "finding-section");
        block.append(el("h4", "finding-title", `Source group: ${(source.exposure_sources || []).join(", ") || "Not available"}`),
          el("p", "muted micro", `Outcome source: ${(source.outcome_sources || []).join(", ") || "Not available"}.`),
          el("p", "note-txt", `${group.paired_units} paired units in this source group. Unpaired outcome records cannot be assigned to an exposure source group.`));
        appendContrast(block, group, true);
        block.appendChild(evidence("Source-group method, paired sample and source identities", group));
        groups.appendChild(block);
      });
      section.appendChild(groups);
    } else appendContrast(section, comparison);
    section.appendChild(evidence("Comparison method, paired sample and source identities", comparison));
    return section;
  }

  function render(result) {
    const root = $("descriptive-results");
    if (result.status === "unsupported") {
      root.append(el("p", "note-txt", "These measurements are not supported for descriptive exploration. Choose another measurement and try again."),
        evidence("Unsupported measurements", result.limitations));
      return "The selected measurements could not be compared.";
    }
    const packet = (result.facts || [])[0];
    if (!packet || packet.contract !== "descriptive-observations-v1"
      || packet.inference_status !== "not_assessed" || packet.eligible_for_hypothesis !== false) {
      throw new Error("The descriptive response was incomplete. Try again.");
    }
    root.appendChild(el("p", "note-txt m0", "Statistical confidence: not assessed. These observations do not establish an association, cause or diagnosis and cannot be tracked as a hypothesis."));
    (packet.features || []).forEach((feature) => root.appendChild(renderFeature(feature)));
    (packet.comparisons || []).forEach((comparison) => root.appendChild(renderComparison(comparison, packet)));
    const limits = el("details", "evidence-details");
    limits.appendChild(el("summary", "", "Limits, shared sources and full evidence"));
    const list = el("ul", "plain-list");
    (packet.limitations || []).forEach((item) => list.appendChild(el("li", "", item.message || item.code)));
    limits.append(list, el("p", "note-txt", `Loaded dates: ${packet.loaded_range.from} through ${packet.loaded_range.to}, including earlier records needed for the chosen timing. Shared source rows and repeated days are not independent corroboration.`),
      evidence("Complete deterministic response and replay references", result));
    root.appendChild(limits);
    return result.status === "insufficient_data"
      ? "No usable records in this window. Missing records remain unknown."
      : "Available records loaded. Review the counts and limitations before interpreting a difference.";
  }

  async function explore(event) {
    event.preventDefault();
    if (controller) {
      reset("Stopped waiting. Explore again when you are ready.");
      return;
    }
    if ($("descriptive-explore").disabled) return;
    reset("Loading available records…");
    const token = generation;
    controller = new AbortController();
    const active = controller;
    $("descriptive-results").setAttribute("aria-busy", "true");
    updateControls();
    try {
      const result = await request("/api/insights/descriptions", {
        method: "POST",
        headers: { "Content-Type": "application/json", "X-CSRFToken": csrf },
        signal: active.signal,
        body: JSON.stringify({
          outcome_key: $("descriptive-outcome").value, exposure_keys: exposures.slice(),
          range: { from: range.from, to: range.to }, lag_days: Number($("descriptive-lag").value),
        }),
      });
      if (token !== generation) return;
      $("descriptive-status").textContent = render(result);
      $("descriptive-results").focus({ preventScroll: true });
    } catch (error) {
      if (token !== generation || error.name === "AbortError") return;
      $("descriptive-results").replaceChildren();
      $("descriptive-status").textContent = "Records unavailable: " + error.message;
    } finally {
      if (token === generation) {
        controller = null;
        $("descriptive-results").setAttribute("aria-busy", "false");
        updateControls();
      }
    }
  }

  $("descriptive-form").addEventListener("submit", explore);
  $("descriptive-exposure").addEventListener("change", updateControls);
  $("descriptive-outcome").addEventListener("change", () => {
    exposures = exposures.filter((key) => key !== $("descriptive-outcome").value);
    paintChoices();
    reset("Outcome changed. Explore again to load its records.");
  });
  $("descriptive-lag").addEventListener("change", () => reset("Timing changed. Explore again to update comparisons."));
  $("descriptive-add").addEventListener("click", () => {
    const key = $("descriptive-exposure").value;
    if (!key || exposures.includes(key) || exposures.length >= 8) return;
    exposures.push(key);
    paintChoices();
    reset("Comparison added. Explore to load the available records.");
    $("descriptive-exposure").focus();
  });
  $("descriptive-retry-catalog").addEventListener("click", loadCatalog);
  window.HermesDescriptions = {
    setRange(next) {
      if (JSON.stringify(range) === JSON.stringify(next)) return;
      range = { ...next };
      reset("Investigation window changed. Explore to load its available records.");
    },
    setPending(value) {
      pending = value;
      if (value) reset("Updating the investigation window…");
      else updateControls();
    },
  };
  loadCatalog();
})();
