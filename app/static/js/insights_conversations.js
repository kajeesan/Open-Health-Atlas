/* Phase 7 panel conversation browser. Conversations are never grouped by day
   and no Telegram source is queried. All untrusted strings use textContent. */
(function () {
  "use strict";
  const root = document.getElementById("conversations-root");
  if (!root) return;
  const $ = (id) => document.getElementById(id);
  const csrf = document.querySelector('meta[name="csrf-token"]').content;
  let conversations = [];
  let listCursor = null;
  let openConversation = null;
  let messageCursor = null;

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

  function el(tag, className, text) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined && text !== null) node.textContent = String(text);
    return node;
  }

  function human(value) {
    return String(value || "").replace(/_/g, " ");
  }

  function windowLabel(range) {
    if (!range) return "Window unavailable";
    return range.kind === "all" ? "All available data"
      : `${range.from} through ${range.to}`;
  }

  function updatedLabel(milliseconds) {
    if (!Number.isSafeInteger(milliseconds)) return "Updated time unavailable";
    return "Updated " + new Date(milliseconds).toLocaleString();
  }

  function compactEvidenceId(value) {
    const text = String(value || "");
    return text.length > 24 ? text.slice(0, 20) + "…" : text;
  }

  function evidenceBlock(evidence) {
    if (!evidence || evidence.contract !== "openhealthatlas-chat-evidence-v1") return null;
    const block = el("div", "message-evidence");
    block.appendChild(el(
      "strong", "message-evidence-title",
      evidence.kind === "verified_receipt"
        ? "Verified OpenHealthAtlas evidence"
        : "Selected deterministic evidence"
    ));
    block.appendChild(el("span", "micro muted", windowLabel(evidence.range)));
    const refs = evidence.kind === "verified_receipt"
      ? (evidence.evidence_refs || [])
      : (evidence.selected_findings || []).map((item) => item.finding_id);
    refs.forEach((value) => {
      const ref = el("code", "message-evidence-ref", compactEvidenceId(value));
      ref.title = String(value);
      block.appendChild(ref);
    });
    const commands = (evidence.tool_receipts || []).map((item) => item.command).filter(Boolean);
    if (commands.length) {
      block.appendChild(el("span", "micro muted", `Tools: ${commands.join(" → ")}`));
    }
    return block;
  }

  function listURL() {
    const params = new URLSearchParams({
      archived: $("conv-archived").checked ? "1" : "0",
      limit: "25",
    });
    if ($("conv-lens").value) params.set("lens", $("conv-lens").value);
    if (listCursor) params.set("before_updated_at", String(listCursor));
    return "/api/chat/conversations?" + params.toString();
  }

  function paintList() {
    const list = $("conv-list");
    list.replaceChildren();
    if (!conversations.length) {
      list.appendChild(el(
        "p", "muted note-txt m0",
        $("conv-archived").checked
          ? "No archived conversations."
          : "No conversations in this view."
      ));
      return;
    }
    conversations.forEach((conversation) => {
      const button = el("button", "conversation-row", "");
      button.type = "button";
      if (openConversation && openConversation.id === conversation.id) {
        button.classList.add("on");
      }
      const top = el("span", "row between gap-2");
      top.append(
        el("strong", "conversation-row-title", conversation.title),
        el("span", "micro muted", human(conversation.lens))
      );
      const excerpt = el(
        "span", "conversation-excerpt",
        conversation.latest_owner_excerpt || "No owner message yet."
      );
      const meta = el(
        "span", "micro muted",
        `${windowLabel(conversation.context.range)} · ${updatedLabel(conversation.updated_at)}`
      );
      button.append(top, excerpt, meta);
      button.addEventListener("click", () => openOne(conversation.id));
      list.appendChild(button);
    });
  }

  async function loadList(reset) {
    if (reset) {
      conversations = [];
      listCursor = null;
      $("conv-list").replaceChildren(el("p", "muted note-txt m0", "Loading…"));
    }
    const data = await requestJSON(listURL());
    conversations = conversations.concat(data.conversations || []);
    listCursor = data.next_before_updated_at;
    $("conv-more").hidden = listCursor === null;
    paintList();
  }

  function messageBubble(message) {
    const node = el("div", "msg " + message.role);
    node.appendChild(el("div", "message-content", message.content));
    const receipt = evidenceBlock(message.evidence);
    if (receipt) node.appendChild(receipt);
    if (message.delivery_status && !["complete", "legacy"].includes(message.delivery_status)) {
      node.appendChild(el("span", "message-state", human(message.delivery_status)));
    }
    return node;
  }

  async function loadMessages(reset) {
    if (!openConversation) return;
    const params = new URLSearchParams({ limit: "100" });
    if (!reset && messageCursor) params.set("before_id", String(messageCursor));
    const data = await requestJSON(
      `/api/chat/conversations/${encodeURIComponent(openConversation.id)}/messages?`
      + params.toString()
    );
    const nodes = (data.messages || []).map(messageBubble);
    if (reset) {
      $("conv-messages").replaceChildren(...nodes);
    } else {
      $("conv-messages").prepend(...nodes);
    }
    messageCursor = data.next_before_id;
    $("conv-older").hidden = messageCursor === null;
    if (reset) $("conv-messages").scrollTop = $("conv-messages").scrollHeight;
  }

  function paintDetail() {
    $("conv-empty").hidden = Boolean(openConversation);
    $("conv-open").hidden = !openConversation;
    if (!openConversation) return;
    $("conv-title").textContent = openConversation.title;
    $("conv-meta").textContent = `${human(openConversation.lens)} lens · panel only, never Telegram`;
    $("conv-context").textContent = windowLabel(openConversation.context.range);
    $("conv-updated").textContent = updatedLabel(openConversation.updated_at);
    $("conv-archive-toggle").textContent = openConversation.archived ? "Unarchive" : "Archive";
    $("conv-input").disabled = openConversation.archived;
    $("conv-send").disabled = openConversation.archived;
    $("conv-input").placeholder = openConversation.archived
      ? "Unarchive to resume this conversation"
      : "Resume this conversation…";
    $("conv-status").textContent = openConversation.archived
      ? "Archived conversations stay readable. Unarchive this one to resume."
      : "";
    paintList();
  }

  async function openOne(id) {
    $("conv-status").textContent = "";
    try {
      const data = await requestJSON(`/api/chat/conversations/${encodeURIComponent(id)}`);
      openConversation = data.conversation;
      messageCursor = null;
      paintDetail();
      await loadMessages(true);
    } catch (error) {
      $("conv-status").textContent = "Could not open conversation: " + error.message;
    }
  }

  async function refreshOpen() {
    if (!openConversation) return;
    const data = await requestJSON(
      `/api/chat/conversations/${encodeURIComponent(openConversation.id)}`
    );
    openConversation = data.conversation;
    paintDetail();
  }

  $("conv-lens").addEventListener("change", () => loadList(true).catch(showListError));
  $("conv-archived").addEventListener("change", () => {
    openConversation = null;
    paintDetail();
    loadList(true).catch(showListError);
  });
  $("conv-more").addEventListener("click", () => loadList(false).catch(showListError));
  $("conv-older").addEventListener("click", () => {
    loadMessages(false).catch((error) => {
      $("conv-status").textContent = "Could not load older messages: " + error.message;
    });
  });

  function showListError(error) {
    $("conv-list").replaceChildren(el(
      "p", "muted note-txt m0", "Could not load conversations: " + error.message
    ));
  }

  $("conv-new").addEventListener("click", async () => {
    $("conv-new").disabled = true;
    try {
      const lens = $("conv-lens").value || "general";
      const data = await mutate("/api/chat/conversations", "POST", {
        lens,
        context: {
          version: 1,
          range: { kind: "all" },
          selected_region_ids: [],
        },
      });
      $("conv-archived").checked = false;
      conversations = [];
      listCursor = null;
      await loadList(true);
      await openOne(data.conversation.id);
    } catch (error) {
      showListError(error);
    } finally {
      $("conv-new").disabled = false;
    }
  });

  $("conv-archive-toggle").addEventListener("click", async () => {
    if (!openConversation) return;
    $("conv-archive-toggle").disabled = true;
    try {
      const data = await mutate(
        `/api/chat/conversations/${encodeURIComponent(openConversation.id)}`,
        "PATCH",
        { archived: !openConversation.archived }
      );
      openConversation = data.conversation;
      paintDetail();
      await loadList(true);
    } catch (error) {
      $("conv-status").textContent = "Could not update the archive: " + error.message;
    } finally {
      $("conv-archive-toggle").disabled = false;
    }
  });

  $("conv-resume-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    if (!openConversation || openConversation.archived) return;
    const input = $("conv-input");
    const message = input.value.trim();
    if (!message) return;
    input.value = "";
    $("conv-messages").appendChild(messageBubble({
      role: "user", content: message, delivery_status: "pending",
    }));
    const pending = el("div", "msg assistant pending", "OpenHealthAtlas is thinking…");
    $("conv-messages").appendChild(pending);
    $("conv-messages").scrollTop = $("conv-messages").scrollHeight;
    input.disabled = true;
    $("conv-send").disabled = true;
    try {
      const data = await mutate(
        `/api/chat/conversations/${encodeURIComponent(openConversation.id)}/send`,
        "POST",
        {
          message,
          turn_id: crypto.randomUUID().toLowerCase(),
          context: openConversation.context,
        }
      );
      pending.remove();
      $("conv-messages").appendChild(messageBubble({
        role: "assistant", content: data.reply, evidence: data.evidence,
        delivery_status: "complete",
      }));
      await refreshOpen();
      await loadList(true);
    } catch (error) {
      pending.textContent = "Could not send: " + error.message;
      pending.classList.remove("pending");
    } finally {
      input.disabled = openConversation.archived;
      $("conv-send").disabled = openConversation.archived;
      input.focus();
    }
  });

  loadList(true).then(() => {
    if (conversations.length) return openOne(conversations[0].id);
    return null;
  }).catch(showListError);
})();
