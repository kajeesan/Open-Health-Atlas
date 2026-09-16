/* Pain / Mobility workspace. The vendored keyboard-accessible body map is
   reused unchanged; selected canonical IDs are persisted in one scoped panel
   conversation and copied into every immutable user-message context. */
(function () {
  "use strict";
  const root = document.getElementById("rw-root");
  if (!root) return;
  const lens = root.dataset.lens;
  const $ = (id) => document.getElementById(id);
  const csrf = document.querySelector('meta[name="csrf-token"]').content;
  const selection = new Set();
  const idName = new Map();
  let conversations = [];
  let current = null;
  let mfFront = null;
  let mfBack = null;
  let nonMuscle = new Set();
  let contextQueue = Promise.resolve();
  let conversationVersion = 0;
  let navigationPending = false;
  let sending = false;

  function selectionIsSaved() {
    const saved = current ? current.context.selected_region_ids || [] : [];
    return saved.length === selection.size && saved.every((id) => selection.has(id));
  }

  function setSending(value) {
    sending = value;
    ["rw-input", "rw-send", "rw-conversation", "rw-new-conversation", "rw-asking-clear"]
      .forEach((id) => { $(id).disabled = value; });
    renderSelection();
  }

  function errorText(value) {
    if (typeof value === "string") return value;
    if (value && typeof value.message === "string") return value.message;
    return "That request could not be completed.";
  }

  async function requestJSON(url, options) {
    const response = await fetch(url, Object.assign(
      { credentials: "same-origin" }, options || {}
    ));
    if (response.status === 401) {
      location.assign("/login");
      throw new Error("Please sign in again.");
    }
    const data = await response.json().catch(() => ({}));
    if (!response.ok || data.ok === false) throw new Error(errorText(data.error));
    return data;
  }

  function mutate(url, method, body) {
    return requestJSON(url, {
      method,
      headers: {
        "Content-Type": "application/json",
        "X-CSRFToken": csrf,
      },
      body: JSON.stringify(body),
    });
  }

  function mfEmpty(node, message) {
    const paragraph = document.createElement("p");
    paragraph.className = "muted note-txt m0";
    paragraph.textContent = message;
    if (node) node.replaceChildren(paragraph);
  }

  (function buildNames() {
    const library = window.BodyMuscles;
    if (!library) return;
    [].concat(library.FRONT_MUSCLES || [], library.BACK_MUSCLES || [])
      .forEach((region) => {
        if (region && region.id) idName.set(region.id, region.name || region.id);
      });
  })();

  function regionLabel(id) {
    return idName.get(id) || id.replace(/-/g, " ");
  }

  function rangeLabel(range) {
    return range && range.kind === "bounded"
      ? `${range.from} through ${range.to}`
      : "All available data";
  }

  function renderSelection() {
    const ids = Array.from(selection);
    $("rw-selcount").textContent = ids.length
      ? `${ids.length} selected` : "none selected";
    $("rw-asking").hidden = ids.length === 0;
    const chips = ids.map((id) => {
      const button = document.createElement("button");
      button.type = "button";
      button.className = "mchip on";
      button.textContent = regionLabel(id) + " ×";
      button.title = "remove " + regionLabel(id);
      button.disabled = sending;
      button.addEventListener("click", () => {
        if (sending || navigationPending) return;
        selection.delete(id);
        renderSelection();
        persistSelection();
      });
      return button;
    });
    $("rw-asking-chips").replaceChildren(...chips);
    if (mfFront) mfFront.mark(selection);
    if (mfBack) mfBack.mark(selection);
    if (current) {
      $("rw-context").textContent = `${lens} · ${rangeLabel(current.context.range)} · `
        + (ids.length ? `${ids.length} region${ids.length === 1 ? "" : "s"}` : "no region selected")
        + (selectionIsSaved() ? "" : " · selection not saved; send will retry");
    }
  }

  function useConversation(conversation) {
    conversationVersion++;
    current = conversation;
    selection.clear();
    (conversation.context.selected_region_ids || []).forEach((id) => {
      if (idName.has(id) && !nonMuscle.has(id)) selection.add(id);
    });
    paintConversationPicker();
    renderSelection();
    loadMessages();
  }

  function paintConversationPicker() {
    const picker = $("rw-conversation");
    picker.replaceChildren();
    conversations.forEach((conversation) => {
      const option = document.createElement("option");
      option.value = conversation.id;
      option.textContent = conversation.title;
      option.selected = Boolean(current && current.id === conversation.id);
      picker.appendChild(option);
    });
  }

  async function persistSelection() {
    if (!current) return;
    const snapshot = Array.from(selection);
    const id = current.id;
    const version = conversationVersion;
    const range = current.context.range;
    contextQueue = contextQueue.then(async () => {
      const data = await mutate(
        `/api/chat/conversations/${encodeURIComponent(id)}`,
        "PATCH",
        {
          context: {
            version: 1,
            range,
            selected_region_ids: snapshot,
          },
        }
      );
      if (current && current.id === id && conversationVersion === version) {
        current = data.conversation;
        const index = conversations.findIndex((item) => item.id === id);
        if (index >= 0) conversations[index] = current;
        renderSelection();
      }
    }).catch((error) => {
      if (current && current.id === id && conversationVersion === version) {
        renderSelection();
        bubble("assistant", "Could not save the selected regions: " + error.message);
      }
    });
    return contextQueue;
  }

  function toggleSelect(id) {
    if (sending || navigationPending || nonMuscle.has(id)) return;
    if (selection.has(id)) selection.delete(id);
    else selection.add(id);
    renderSelection();
    persistSelection();
  }

  async function loadMap() {
    const front = $("mf-front");
    const back = $("mf-back");
    if (!window.MuscleFigure || !window.BodyMuscles) {
      [front, back].forEach((node) => mfEmpty(node, "The body map could not load."));
      return;
    }
    let data;
    try {
      data = (await requestJSON(
        "/api/training/muscle-map?lens=" + encodeURIComponent(lens)
      )).result;
    } catch (error) {
      [front, back].forEach((node) => mfEmpty(node, error.message));
      return;
    }
    nonMuscle = new Set(data.non_muscle || []);
    const options = {
      interactive: true,
      onRegionClick: toggleSelect,
      actionable: () => true,
    };
    mfFront = window.MuscleFigure.create(front, Object.assign({ view: "front" }, options));
    mfBack = window.MuscleFigure.create(back, Object.assign({ view: "back" }, options));
    mfFront.update(data);
    mfBack.update(data);
    window.MuscleFigure.legend($("mf-legend-f"), data);
    window.MuscleFigure.legend($("mf-legend-b"), data);
    $("rw-maphint").textContent = lens === "pain"
      ? "Colour shows logged pain (0–10 NRS). Use Enter, Space, or click to select one or more regions."
      : "Colour shows the cited mobility screen state. Use Enter, Space, or click to select regions.";
    if (window.HermesPhysio) {
      if (lens === "pain") window.HermesPhysio.renderPhysioLoop(data);
      else window.HermesPhysio.renderMobility(data);
    }
    if (current) {
      selection.clear();
      (current.context.selected_region_ids || []).forEach((id) => {
        if (idName.has(id) && !nonMuscle.has(id)) selection.add(id);
      });
    }
    const wanted = new URLSearchParams(location.search).get("region");
    if (wanted && idName.has(wanted) && !nonMuscle.has(wanted)) {
      selection.add(wanted);
      renderSelection();
      await persistSelection();
    } else {
      renderSelection();
    }
  }

  const chatLog = $("rw-chat");
  function bubble(role, text, pending) {
    const node = document.createElement("div");
    node.className = "msg " + role + (pending ? " pending" : "");
    node.textContent = text;
    chatLog.appendChild(node);
    chatLog.scrollTop = chatLog.scrollHeight;
    return node;
  }

  async function loadMessages() {
    chatLog.replaceChildren();
    if (!current) {
      bubble("assistant", "Start an investigation to use OpenHealthAtlas.");
      return;
    }
    const version = conversationVersion;
    try {
      const data = await requestJSON(
        `/api/chat/conversations/${encodeURIComponent(current.id)}/messages?limit=100`
      );
      if (version !== conversationVersion) return;
      if (!data.messages.length) {
        bubble("assistant", lens === "pain"
          ? "Point at where it hurts, then describe what you feel. I organise evidence and suggest checks."
          : "Point at what feels restricted, then describe it. I can compare the scope with your logged mobility and training.");
      } else {
        data.messages.forEach((message) => bubble(message.role, message.content));
      }
    } catch (error) {
      if (version === conversationVersion) bubble("assistant", "Conversation unavailable: " + error.message);
    }
  }

  async function loadConversations() {
    const data = await requestJSON(
      `/api/chat/conversations?lens=${encodeURIComponent(lens)}&archived=0&limit=100`
    );
    conversations = data.conversations || [];
    if (!conversations.length) {
      const created = await mutate("/api/chat/conversations", "POST", {
        lens,
        context: {
          version: 1,
          range: { kind: "all" },
          selected_region_ids: [],
        },
      });
      conversations = [created.conversation];
    }
    const remembered = localStorage.getItem(`hermes.${lens}.conversation`);
    current = conversations.find((item) => item.id === remembered) || conversations[0];
    localStorage.setItem(`hermes.${lens}.conversation`, current.id);
    useConversation(current);
  }

  $("rw-conversation").addEventListener("change", async (event) => {
    if (sending || navigationPending) { paintConversationPicker(); return; }
    const version = ++conversationVersion;
    navigationPending = true;
    $("rw-conversation").disabled = true;
    try {
      const data = await requestJSON(
        `/api/chat/conversations/${encodeURIComponent(event.target.value)}`
      );
      if (version !== conversationVersion) return;
      localStorage.setItem(`hermes.${lens}.conversation`, data.conversation.id);
      useConversation(data.conversation);
    } catch (error) {
      if (version === conversationVersion) bubble("assistant", "Could not open that investigation: " + error.message);
    } finally {
      navigationPending = false;
      $("rw-conversation").disabled = false;
    }
  });

  $("rw-new-conversation").addEventListener("click", async () => {
    if (sending || navigationPending) return;
    navigationPending = true;
    $("rw-new-conversation").disabled = true;
    try {
      const created = await mutate("/api/chat/conversations", "POST", {
        lens,
        context: {
          version: 1,
          range: current ? current.context.range : { kind: "all" },
          selected_region_ids: Array.from(selection),
        },
      });
      conversations.unshift(created.conversation);
      localStorage.setItem(`hermes.${lens}.conversation`, created.conversation.id);
      useConversation(created.conversation);
    } catch (error) {
      bubble("assistant", "Could not start a new investigation: " + error.message);
    } finally {
      navigationPending = false;
      $("rw-new-conversation").disabled = false;
    }
  });

  $("rw-asking-clear").addEventListener("click", () => {
    if (sending || navigationPending) return;
    selection.clear();
    renderSelection();
    persistSelection();
  });

  $("rw-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    if (!current || sending || navigationPending) return;
    const input = $("rw-input");
    const message = input.value.trim();
    if (!message) return;
    const id = current.id;
    const version = conversationVersion;
    let pending = null;
    setSending(true);
    try {
      await contextQueue;
      if (current.id !== id || conversationVersion !== version) return;
      if (!selectionIsSaved()) await persistSelection();
      if (!selectionIsSaved()) throw new Error("The selected regions are not saved. Your message is still here; try sending again.");
      bubble("user", message);
      pending = bubble("assistant", "OpenHealthAtlas is thinking…", true);
      const data = await mutate(
        `/api/chat/conversations/${encodeURIComponent(id)}/send`,
        "POST",
        {
          message,
          turn_id: crypto.randomUUID().toLowerCase(),
          context: current.context,
        }
      );
      input.value = "";
      pending.remove();
      bubble("assistant", data.reply);
      const detail = await requestJSON(
        `/api/chat/conversations/${encodeURIComponent(id)}`
      );
      current = detail.conversation;
      const index = conversations.findIndex((item) => item.id === current.id);
      if (index >= 0) conversations[index] = current;
      paintConversationPicker();
    } catch (error) {
      if (pending) {
        pending.textContent = "Could not send: " + error.message;
        pending.classList.remove("pending");
      } else {
        bubble("assistant", "Could not send: " + error.message);
      }
    } finally {
      setSending(false);
      input.focus();
    }
  });

  (async function boot() {
    try {
      await loadConversations();
      await loadMap();
    } catch (error) {
      bubble("assistant", "Workspace unavailable: " + error.message);
    }
  })();
})();
