/* WebAuthn ceremonies, no dependencies.
   Server messages use py_webauthn's JSON option format (base64url fields);
   we convert to ArrayBuffers for navigator.credentials and back. */
(function () {
  const msg = document.getElementById("auth-msg");
  const csrf = document.querySelector('meta[name="csrf-token"]').content;

  const say = (t, isErr) => { if (msg) { msg.textContent = t; msg.classList.toggle("err", !!isErr); } };
  const b2buf = (s) => Uint8Array.from(atob(s.replace(/-/g, "+").replace(/_/g, "/")), c => c.charCodeAt(0)).buffer;
  const buf2b = (buf) => btoa(String.fromCharCode(...new Uint8Array(buf))).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");

  async function post(url, body) {
    const res = await fetch(url, {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-CSRFToken": csrf },
      credentials: "same-origin",
      body: JSON.stringify(body || {}),
    });
    if (!res.ok) {
      let detail = "";
      try { detail = (await res.json()).error || ""; } catch (_) {}
      // HTTP/2 has no status text, so fall back to the code, never to "".
      throw new Error(detail || res.statusText || ("HTTP " + res.status));
    }
    return res.json();
  }

  /* ---------------- login ---------------- */
  const loginBtn = document.getElementById("passkey-login");
  if (loginBtn) loginBtn.addEventListener("click", async () => {
    try {
      say("Waiting for your passkey…");
      const opts = await post("/api/auth/login/options");
      const pk = opts; // py_webauthn options JSON
      pk.challenge = b2buf(pk.challenge);
      (pk.allowCredentials || []).forEach(c => { c.id = b2buf(c.id); });
      const cred = await navigator.credentials.get({ publicKey: pk });
      const payload = JSON.stringify({
        id: cred.id,
        rawId: buf2b(cred.rawId),
        type: cred.type,
        clientExtensionResults: cred.getClientExtensionResults(),
        response: {
          clientDataJSON: buf2b(cred.response.clientDataJSON),
          authenticatorData: buf2b(cred.response.authenticatorData),
          signature: buf2b(cred.response.signature),
          userHandle: cred.response.userHandle ? buf2b(cred.response.userHandle) : null,
        },
      });
      await post("/api/auth/login/verify", { credential: payload });
      const next = loginBtn.dataset.next || "/";
      location.assign(next.startsWith("/") && !next.startsWith("//") ? next : "/");
    } catch (e) {
      say("Sign-in failed: " + e.message, true);
    }
  });

  /* ---------------- enrollment ---------------- */
  const enrollBtn = document.getElementById("passkey-enroll");
  if (enrollBtn) enrollBtn.addEventListener("click", async () => {
    const token = enrollBtn.dataset.token;
    try {
      say("Follow your device's passkey prompt…");
      const pk = await post("/api/auth/register/options", { token });
      pk.challenge = b2buf(pk.challenge);
      pk.user.id = b2buf(pk.user.id);
      (pk.excludeCredentials || []).forEach(c => { c.id = b2buf(c.id); });
      const cred = await navigator.credentials.create({ publicKey: pk });
      const payload = JSON.stringify({
        id: cred.id,
        rawId: buf2b(cred.rawId),
        type: cred.type,
        clientExtensionResults: cred.getClientExtensionResults(),
        response: {
          clientDataJSON: buf2b(cred.response.clientDataJSON),
          attestationObject: buf2b(cred.response.attestationObject),
          transports: cred.response.getTransports ? cred.response.getTransports() : [],
        },
      });
      const label = (document.getElementById("cred-label") || {}).value || "";
      await post("/api/auth/register/verify", { token, credential: payload, label });
      say("Passkey enrolled ✓ — taking you to the dashboard…");
      setTimeout(() => location.assign("/"), 600);
    } catch (e) {
      if (e.name === "InvalidStateError") {
        // The authenticator already holds a passkey for this panel — with
        // iCloud Keychain one enrollment syncs to every Apple device.
        say("This device already has a passkey for the panel (passkeys sync via iCloud). Taking you to sign-in…");
        setTimeout(() => location.assign("/login"), 2500);
        return;
      }
      say("Enrollment failed: " + e.message, true);
    }
  });
})();
