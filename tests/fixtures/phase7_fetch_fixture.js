/* Deterministic fetch layer for the local file:// Phase 7 visual fallback.
   Loaded only by scripts/phase7_visual_fixture.py --render-static. */
(function () {
  "use strict";
  const FINDING = {
    finding_id: "sha256:" + "d".repeat(64),
    outcome: {
      key: "subjective.day_rating", display: "Day rating",
      mode: "green-vs-non-green",
    },
    exposure: { components: [{
      exposure_key: "sleep.duration_hours", display: "Sleep duration",
      unit: "hours", temporal_type: "outcome", direction: "target_range",
      lag_days: 1, window_days: 1, transform: "point",
      merge_rule: "sleep_log_then_selected_daily_metrics",
      zero_semantics: "invalid", temporal_direction: "exposure_precedes_outcome",
    }] },
    sample: {
      eligible_n: 90, complete_n: 72, missing_n: 18,
      exposed_n: 31, unexposed_n: 41,
    },
    effect: { estimate: 0.18, ci95: [0.04, 0.31] },
    testing: { q: 0.08 },
    stability: {
      status: "stable", full: 0.18, first_half: 0.15, second_half: 0.2,
    },
    confounders: {
      checked: ["weekend", "training day"],
      sensitive_to: ["training day"], weighted_effect: 0.11,
    },
    warnings: ["association_not_causation", "confounder_sensitive"],
    quality: {
      tier: "exploratory_unreplicated", eligible_for_hypothesis: true,
    },
    evidence_for: [{ code: "effect_gate_pass", value: 0.18, threshold: 0.1 }],
    evidence_against: [{ code: "confounder_attenuation", detail: "training day" }],
    provenance: {
      analysis_version: "outcome-v1", registry_version: "feature-registry-v1",
      engine_sha256: "sha256:46afb3a71875217d43ecd2bfda7e1268c"
        + "cab1da86b1e3a69eb74a927984e5723",
      registry_sha256: "sha256:" + "2".repeat(64),
      input_fingerprint: "sha256:" + "e".repeat(64),
      dependencies: {},
    },
  };
  let clock = 1784797200000;
  let serial = 0;
  const range = {
    kind: "bounded", from: "2026-07-01", to: "2026-07-23",
  };
  const conversations = [
    {
      id: "legacy-general", title: "July wellbeing patterns", lens: "general",
      context: { version: 1, range: { ...range }, selected_region_ids: [] },
      archived: false, archived_at: null,
      created_at: clock - 9000000, updated_at: clock,
      latest_owner_excerpt: "What appears most consistently before my green days?",
      busy: false,
    },
    {
      id: "a".repeat(32), title: "Left knee after running", lens: "pain",
      context: {
        version: 1, range: { ...range },
        selected_region_ids: ["knee-left", "hip-flexor-left"],
      },
      archived: false, archived_at: null,
      created_at: clock - 8000000, updated_at: clock - 1000,
      latest_owner_excerpt: "The outside of my left knee feels irritated after longer runs.",
      busy: false,
    },
    {
      id: "b".repeat(32), title: "Ankle mobility baseline", lens: "mobility",
      context: {
        version: 1, range: { kind: "all" },
        selected_region_ids: ["calves-soleus-left"],
      },
      archived: true, archived_at: clock - 500000,
      created_at: clock - 7000000, updated_at: clock - 2000,
      latest_owner_excerpt: "Compare my left and right ankle screens.",
      busy: false,
    },
  ];
  const messages = {
    "legacy-general": [
      {
        id: 1, ts: 1784790000, role: "user",
        content: "What appears most consistently before my green days?",
        turn_id: "11111111-1111-4111-8111-111111111111",
        context: {
          version: 1, surface: "panel", lens: "general",
          conversation_id: "legacy-general", range: { ...range },
          selected_region_ids: [], evidence_contract: "health-tool-v1",
        },
        delivery_status: "complete",
      },
      {
        id: 2, ts: 1784790010, role: "assistant",
        content: "Sleep duration is an exploratory association in this window. Training-day sensitivity means it should not be read as cause.",
        turn_id: "11111111-1111-4111-8111-111111111111",
        context: null, delivery_status: "complete",
      },
    ],
    ["a".repeat(32)]: [],
    ["b".repeat(32)]: [],
  };

  function json(value, status) {
    return Promise.resolve(new Response(JSON.stringify(value), {
      status: status || 200, headers: { "Content-Type": "application/json" },
    }));
  }

  function findConversation(id) {
    return conversations.find((item) => item.id === id);
  }

  const analysisJobs = new Map();
  window.fetch = async function fixtureFetch(input, options) {
    const method = ((options || {}).method || "GET").toUpperCase();
    const url = new URL(String(input), "http://fixture.example");
    const path = url.pathname;
    let match;
    if (path === "/api/insights/outcome-jobs" && method === "POST") {
      const existing = [...analysisJobs].find((entry) => entry[1] === url.search);
      const id = existing ? existing[0] : (++serial).toString(16).padStart(32, "0");
      analysisJobs.set(id, url.search);
      return json({ok: true, job: {job_id: id, status: "queued"}});
    }
    if ((match = path.match(/^\/api\/insights\/outcome-jobs\/([0-9a-f]{32})$/))) {
      const id = match[1];
      if (!analysisJobs.has(id)) {
        return json({ok: false, error: {code: "job_not_found", message: "Fictional job not found."}});
      }
      if (analysisJobs.get(id) !== url.search) {
        return json({ok: false, error: {code: "job_scope_mismatch", message: "Different fictional window."}});
      }
      const result = await (await fixtureFetch("/api/insights/outcomes" + url.search)).json();
      return json({ok: true, job: {job_id: id, status: "completed", result: result.result}});
    }
    if (path === "/api/chat/conversations" && method === "GET") {
      const archived = url.searchParams.get("archived") === "1";
      const lens = url.searchParams.get("lens");
      return json({
        conversations: conversations.filter((item) =>
          item.archived === archived && (!lens || item.lens === lens)
        ),
        next_before_updated_at: null,
      });
    }
    if (path === "/api/chat/conversations" && method === "POST") {
      const body = JSON.parse(options.body);
      serial += 1;
      const id = serial.toString(16).padStart(32, "0");
      const item = {
        id, title: "New conversation", lens: body.lens,
        context: body.context, archived: false, archived_at: null,
        created_at: ++clock, updated_at: clock,
        latest_owner_excerpt: null, busy: false,
      };
      conversations.unshift(item);
      messages[id] = [];
      return json({ ok: true, conversation: item }, 201);
    }
    match = path.match(/^\/api\/chat\/conversations\/([^/]+)$/);
    if (match && method === "GET") {
      return json({ conversation: findConversation(match[1]) });
    }
    if (match && method === "PATCH") {
      const item = findConversation(match[1]);
      const body = JSON.parse(options.body);
      if (body.context) item.context = body.context;
      if (body.title) item.title = body.title;
      if (typeof body.archived === "boolean") {
        item.archived = body.archived;
        item.archived_at = body.archived ? ++clock : null;
      }
      item.updated_at = ++clock;
      return json({ ok: true, conversation: item });
    }
    match = path.match(/^\/api\/chat\/conversations\/([^/]+)\/messages$/);
    if (match) {
      return json({ messages: messages[match[1]] || [], next_before_id: null });
    }
    match = path.match(/^\/api\/chat\/conversations\/([^/]+)\/send$/);
    if (match && method === "POST") {
      const body = JSON.parse(options.body);
      const item = findConversation(match[1]);
      const list = messages[match[1]] || (messages[match[1]] = []);
      list.push({
        id: list.length + 1, ts: 1784791000, role: "user",
        content: body.message, turn_id: body.turn_id,
        context: body.context, delivery_status: "complete",
      });
      list.push({
        id: list.length + 1, ts: 1784791010, role: "assistant",
        content: "This is a deterministic visual-review reply.",
        turn_id: body.turn_id, context: body.context, delivery_status: "complete",
      });
      item.latest_owner_excerpt = body.message;
      item.updated_at = ++clock;
      return json({ ok: true, reply: "This is a deterministic visual-review reply." });
    }
    if (path === "/api/insights/range") {
      const granularity = url.searchParams.get("granularity");
      const ranges = {
        day: { kind: "bounded", from: "2026-07-23", to: "2026-07-23" },
        week: { kind: "bounded", from: "2026-07-20", to: "2026-07-26" },
        month: { ...range },
        year: { kind: "bounded", from: "2026-01-01", to: "2026-12-31" },
        all: { kind: "all" },
      };
      return json({ ok: true, result: {
        granularity, anchor: granularity === "all" ? null : "2026-07-23",
        range: ranges[granularity], canonical_today: "2026-07-23",
      } });
    }
    if (path === "/api/insights/outcomes") {
      return json({ ok: true, result: {
        ok: true, contract_version: "outcome-associations-v1",
        meta: {
          analysis_version: "outcome-v1",
          registry_version: "feature-registry-v1",
          engine_sha256: "sha256:46afb3a71875217d43ecd2bfda7e1268c"
            + "cab1da86b1e3a69eb74a927984e5723",
          registry_sha256: "sha256:" + "2".repeat(64),
          input_fingerprint: "sha256:" + "e".repeat(64),
          analysis_range: { ...range },
          baseline_range: {
            kind: "bounded", from: "2026-05-28", to: "2026-07-23",
          },
        },
        coverage: {
          source_manifests: [{
            table: "sleep_log",
            adapter: "daily",
            merge_rule: "sleep_log_then_selected_daily_metrics",
            source_labels: ["Fitbit"],
            natural_key_scheme: "table-prefixed-natural-key-v1",
            row_count: 72,
            date_from: "2026-05-28",
            date_to: "2026-07-23",
            digest: "sha256:" + "3".repeat(64),
          }],
        },
        findings: [FINDING], warnings: [],
      } });
    }
    if (path === "/api/insights/readiness") {
      const states = [
        "logic_not_implemented", "present_not_connected",
        "implemented_never_logged", "stale",
        "too_sparse_for_analysis", "sufficient",
      ];
      return json({ ok: true, result: {
        ok: true,
        meta: {
          range: { ...range }, predicate_order: states,
          state_counts: Object.fromEntries(states.map((state, index) => [state, index])),
        },
        features: [{ key: "sleep.duration_hours", state: "sufficient" }],
      } });
    }
    if (path === "/api/insights/hypotheses") {
      return json({ ok: true, result: {
        ok: true, contract_version: "hypothesis-ledger-v1",
        items: [{
          hypothesis_id: "sha256:" + "a".repeat(64),
          outcome_key: "subjective.day_rating",
          created_at: "2026-07-18T08:10:00+02:00",
          components: [{
            exposure_key: "sleep.duration_hours", lag_days: 1, window_days: 1,
          }],
          latest_evaluation: { status: "strengthening" },
        }],
        next_before: null,
      } });
    }
    if (path.startsWith("/api/insights/hypotheses/") && method === "GET") {
      return json({ ok: true, result: {
        ok: true, contract_version: "hypothesis-ledger-v1",
        hypothesis: { latest_evaluation: {
          status: "strengthening", evidence_class: "supportive",
          confidence: "exploratory",
          range_from: "2026-04-01", range_to: "2026-07-18",
          sample_size: { complete_n: 72, missing_n: 18 },
          effect_summary: {
            direction: "positive", effect: FINDING.effect,
            testing: FINDING.testing, quality: FINDING.quality,
            provenance: FINDING.provenance,
          },
          stability: FINDING.stability, confounders: FINDING.confounders,
        }, components: [{
          exposure_key: "sleep.duration_hours", lag_days: 1, window_days: 1,
        }] },
        evidence_for: [{
          range_from: "2026-04-01", range_to: "2026-07-18", finding: FINDING,
        }], evidence_against: [],
        latest_annotations: [
          {
            annotation_kind: "alternative",
            content: "Training load may explain part of the pattern.",
          },
          {
            annotation_kind: "next_experiment",
            content: "Keep logging comparable training days.",
          },
        ],
      } });
    }
    if (path === "/api/insights/hypotheses/promote") {
      return json({ ok: true, result: {
        status: "created", hypothesis_id: "sha256:" + "a".repeat(64),
      } });
    }
    if (path === "/api/insights/syntheses") {
      return json({ ok: true, result: {
        ok: true, contract: "synthesis-v1",
        syntheses: [{
          cadence: "weekly", created_at: "2026-07-20T08:05:00+02:00",
          cutoff_date: "2026-07-19",
          run_refs: [
            { purpose: "analysis" }, { purpose: "wider baseline" },
          ],
          rendered_md: "What changed\n\nSleep duration remains an exploratory candidate. The wider baseline is consistent, but training-day sensitivity remains.",
        }],
      } });
    }
    if (path === "/api/insights/runs") {
      return json({ ok: true, result: {
        read_only: true,
        batches: [{
          run_kind: "nightly", status: "no-novelty", anchor_date: "2026-07-22",
        }],
        triggers: [],
        notifications: [{ status: "suppressed", reason: "delivery_disabled" }],
      } });
    }
    if (path === "/api/training/muscle-map") {
      const lens = url.searchParams.get("lens");
      const common = {
        window_days: 90, non_muscle: ["head", "face", "hand-left", "hand-right"],
      };
      if (lens === "pain") {
        return json({ ok: true, result: {
          ...common, lens,
          regions: {
            "knee-left": {
              status: "moderate", group: null, tips: ["latest explicit NRS 4"],
            },
            "hip-flexor-left": { status: "mild", group: "Legs", tips: [] },
          },
          legend: [
            { status: "none", label: "none logged" },
            { status: "mild", label: "mild" },
            { status: "moderate", label: "moderate" },
            { status: "severe", label: "severe" },
          ],
          loop: [], cv_note: "Pain evidence is descriptive, not diagnostic.",
          boundary_note: "",
        } });
      }
      return json({ ok: true, result: {
        ...common, lens: "mobility",
        regions: {
          "calves-soleus-left": {
            status: "restricted", group: "Legs", tips: [],
          },
          "hip-flexor-left": { status: "normal", group: "Legs", tips: [] },
        },
        legend: [
          { status: "restricted", label: "restricted" },
          { status: "normal", label: "within norm" },
          { status: "untested", label: "untested" },
        ],
        tests: [], flexibility_note: "A screen, not a diagnosis.",
      } });
    }
    return json({ ok: false, error: "Visual fixture endpoint unavailable." }, 404);
  };
})();
