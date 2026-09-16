/* Vault notes settings page: edit the four whitelisted vault Markdown notes
   with a diff preview + confirm. Split out of the old Plans page (nav flip);
   the notes logic is moved verbatim. The WRITE POSTs to /api/plans/note, which
   routes through the bridge to health.py write-note (endpoints unchanged).

   A generation counter guards against async races: switching or editing a note
   bumps `noteGen`, and any in-flight load/diff that resolves late bails out.
   `previewedFor` records the exact {path, content} that was previewed, so Save
   can only ever write content that was previewed against the note currently
   selected — never note A's text into note B, never unpreviewed. */
(function () {
  const csrf = document.querySelector('meta[name="csrf-token"]').content;
  const toastWrap = document.getElementById("toast-wrap");

  function toast(msg, kind) {
    const el = document.createElement("div");
    el.className = "toast " + (kind || "");
    el.textContent = msg;
    toastWrap.appendChild(el);
    requestAnimationFrame(() => el.classList.add("show"));
    setTimeout(() => { el.classList.remove("show"); setTimeout(() => el.remove(), 250); }, 3000);
  }
  async function getJSON(url) {
    const r = await fetch(url, { credentials: "same-origin" });
    if (r.status === 401) { location.assign("/login"); throw new Error("unauthenticated"); }
    if (!r.ok) { let d = ""; try { d = (await r.json()).error || ""; } catch (_) {} throw new Error(d || "HTTP " + r.status); }
    return r.json();
  }
  async function postJSON(url, body) {
    const r = await fetch(url, { method: "POST", credentials: "same-origin",
      headers: { "Content-Type": "application/json", "X-CSRFToken": csrf }, body: JSON.stringify(body) });
    if (r.status === 401) { location.assign("/login"); throw new Error("session expired"); }
    const d = await r.json().catch(() => ({}));
    if (!r.ok || d.ok === false) throw new Error(d.error || "HTTP " + r.status);
    return d;
  }

  let noteGen = 0;
  let previewedFor = null;   // { path, content }

  function disableSave() {
    document.getElementById("note-save-btn").disabled = true;
    previewedFor = null;
  }
  async function loadNote() {
    const gen = ++noteGen;
    const path = document.getElementById("note-picker").value;
    const ta = document.getElementById("note-editor");
    document.getElementById("note-diff").hidden = true;
    disableSave();
    document.getElementById("note-status").textContent = "";
    try {
      const d = await getJSON("/api/plans/note?path=" + encodeURIComponent(path));
      if (gen !== noteGen) return;          // user switched notes mid-load
      ta.value = d.content;
    } catch (e) {
      if (gen === noteGen) { ta.value = ""; toast("Couldn't load note: " + e.message, "bad"); }
    }
  }
  async function previewDiff() {
    const gen = noteGen;
    const path = document.getElementById("note-picker").value;
    const content = document.getElementById("note-editor").value;
    const pre = document.getElementById("note-diff");
    try {
      const d = await postJSON("/api/plans/note-diff", { path, content });
      if (gen !== noteGen) return;          // note switched/edited since preview started
      if (!d.changed) { pre.textContent = "No changes."; pre.hidden = false; disableSave(); return; }
      pre.replaceChildren(...d.diff.map(line => {
        const span = document.createElement("span");
        span.className = "dl " + (line.startsWith("+") && !line.startsWith("+++") ? "add"
          : line.startsWith("-") && !line.startsWith("---") ? "del"
          : line.startsWith("@@") ? "hunk" : "");
        span.textContent = line;
        return span;
      }));
      pre.hidden = false;
      previewedFor = { path, content };     // exactly what was previewed
      document.getElementById("note-save-btn").disabled = false;
    } catch (e) { toast("Diff failed: " + e.message, "bad"); }
  }
  async function saveNote() {
    const path = document.getElementById("note-picker").value;
    const content = document.getElementById("note-editor").value;
    // Only save content that was previewed against the currently selected note.
    if (!previewedFor || previewedFor.path !== path || previewedFor.content !== content) {
      toast("Preview changes before saving", "bad");
      disableSave();
      return;
    }
    try {
      await postJSON("/api/plans/note", { path, content });
      document.getElementById("note-diff").hidden = true;
      disableSave();
      document.getElementById("note-status").textContent = "saved ✓";
      toast("Note saved ✓", "good");
    } catch (e) { toast("Couldn't save: " + e.message, "bad"); }
  }

  document.getElementById("note-picker").addEventListener("change", loadNote);
  document.getElementById("note-diff-btn").addEventListener("click", previewDiff);
  document.getElementById("note-save-btn").addEventListener("click", saveNote);
  document.getElementById("note-editor").addEventListener("input", () => {
    noteGen++;                 // invalidate any in-flight preview; must re-preview
    disableSave();
    document.getElementById("note-status").textContent = "";
  });

  loadNote();
})();
