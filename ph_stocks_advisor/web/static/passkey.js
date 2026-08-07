/**
 * Passkey (WebAuthn) sign-in — email-first.
 *
 * "Sign in with a passkey" runs the authentication ceremony for the typed
 * email; "Create an account" reveals a name field and runs registration.
 * All POSTs carry the CSRF token. Failures show one generic message
 * (the server is deliberately uniform for anti-enumeration).
 */
(function () {
  "use strict";

  const form = document.getElementById("passkey-form");
  if (!form || !window.PublicKeyCredential) {
    // No passkey UI, or the browser lacks WebAuthn — leave OAuth as the path.
    if (form && !window.PublicKeyCredential) {
      showError("This browser doesn't support passkeys. Use a recovery sign-in below.");
    }
    return;
  }

  const emailInput = document.getElementById("pk-email");
  const nameInput = document.getElementById("pk-name");
  const signinBtn = document.getElementById("pk-signin");
  const registerToggle = document.getElementById("pk-register");
  const errorEl = document.getElementById("pk-error");
  const consentBlock = document.getElementById("pk-consent");
  const acceptBox = document.getElementById("pk-accept");
  const codeBlock = document.getElementById("pk-code-block");
  const codeInput = document.getElementById("pk-code");
  const codeEmailEl = document.getElementById("pk-code-email");
  const resendBtn = document.getElementById("pk-resend");

  let mode = "login"; // "login" | "register"
  // Registration is two steps: "form" (email + consent → server emails a
  // code) then "code" (type the code → WebAuthn ceremony).
  let regStage = "form";

  const csrf = () =>
    document.querySelector('meta[name="csrf-token"]')?.getAttribute("content") || "";

  // ---- base64url <-> ArrayBuffer -----------------------------------------
  function b64urlToBuf(value) {
    const s = value.replace(/-/g, "+").replace(/_/g, "/");
    const pad = s.length % 4 ? "=".repeat(4 - (s.length % 4)) : "";
    const bin = atob(s + pad);
    const bytes = new Uint8Array(bin.length);
    for (let i = 0; i < bin.length; i++) bytes[i] = bin.charCodeAt(i);
    return bytes.buffer;
  }
  function bufToB64url(buf) {
    const bytes = new Uint8Array(buf);
    let bin = "";
    for (let i = 0; i < bytes.length; i++) bin += String.fromCharCode(bytes[i]);
    return btoa(bin).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
  }

  function showError(msg) {
    if (!errorEl) return;
    errorEl.querySelector("span").textContent = msg;
    errorEl.style.display = "block";
  }
  function clearError() {
    if (errorEl) errorEl.style.display = "none";
  }
  function idleLabel() {
    if (mode !== "register") return "Sign in with a passkey";
    return regStage === "code" ? "Verify code & create account" : "Email me a verification code";
  }
  function busy(on) {
    signinBtn.querySelector("span").textContent = on ? "Working…" : idleLabel();
    if (on) {
      signinBtn.disabled = true;
      signinBtn.classList.add("is-disabled");
    } else {
      // Restore the consent gate rather than blindly re-enabling: after a
      // failed registration the button must stay locked if the box is
      // unticked, and must not revert to the sign-in label mid-registration.
      syncConsentState();
    }
  }

  /** Raise an error that carries the server's own message, so a real cause
   *  ("you already have an account", "accept the terms") reaches the user
   *  instead of a generic "couldn't create a passkey". */
  async function beginError(resp) {
    let msg = "";
    try {
      msg = (await resp.json()).error || "";
    } catch {
      /* non-JSON body (proxy error page, 500) — fall through */
    }
    const err = new Error(msg || `Request failed (${resp.status})`);
    err.name = "BeginError";
    err.serverMessage = msg;
    err.status = resp.status;
    return err;
  }

  async function postJSON(url, body) {
    return fetch(url, {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-CSRFToken": csrf() },
      body: JSON.stringify(body || {}),
    });
  }
  async function postCredential(url, credentialJSON) {
    return fetch(url, {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-CSRFToken": csrf() },
      body: JSON.stringify(credentialJSON),
    });
  }

  // ---- ceremonies ---------------------------------------------------------
  async function doLogin(email) {
    const optResp = await postJSON("/auth/passkey/login/begin", { email });
    if (!optResp.ok) throw await beginError(optResp);
    const options = await optResp.json();
    options.challenge = b64urlToBuf(options.challenge);
    (options.allowCredentials || []).forEach((c) => (c.id = b64urlToBuf(c.id)));

    const cred = await navigator.credentials.get({ publicKey: options });
    const payload = {
      id: cred.id,
      rawId: bufToB64url(cred.rawId),
      type: cred.type,
      response: {
        clientDataJSON: bufToB64url(cred.response.clientDataJSON),
        authenticatorData: bufToB64url(cred.response.authenticatorData),
        signature: bufToB64url(cred.response.signature),
        userHandle: cred.response.userHandle ? bufToB64url(cred.response.userHandle) : null,
      },
      clientExtensionResults: cred.getClientExtensionResults ? cred.getClientExtensionResults() : {},
    };
    const done = await postCredential("/auth/passkey/login/complete", payload);
    return done;
  }

  async function doRegister(email, name, code) {
    const optResp = await postJSON("/auth/passkey/register/begin", {
      email,
      name,
      code, // emailed verification code — the server refuses new accounts without it
      accept_disclaimer: true, // the server re-checks; the UI cannot be the only gate
    });
    if (!optResp.ok) throw await beginError(optResp);
    const options = await optResp.json();
    options.challenge = b64urlToBuf(options.challenge);
    options.user.id = b64urlToBuf(options.user.id);
    (options.excludeCredentials || []).forEach((c) => (c.id = b64urlToBuf(c.id)));

    const cred = await navigator.credentials.create({ publicKey: options });
    const payload = {
      id: cred.id,
      rawId: bufToB64url(cred.rawId),
      type: cred.type,
      response: {
        clientDataJSON: bufToB64url(cred.response.clientDataJSON),
        attestationObject: bufToB64url(cred.response.attestationObject),
        transports: cred.response.getTransports ? cred.response.getTransports() : [],
      },
      clientExtensionResults: cred.getClientExtensionResults ? cred.getClientExtensionResults() : {},
      authenticatorAttachment: cred.authenticatorAttachment || undefined,
    };
    const done = await postCredential("/auth/passkey/register/complete", payload);
    return done;
  }

  /** Ask the server to email a verification code; true on success. */
  async function sendCode(email) {
    const resp = await postJSON("/auth/passkey/register/send-code", {
      email,
      accept_disclaimer: true, // the server re-checks; the UI cannot be the only gate
    });
    if (resp.ok) return true;
    let msg = "";
    try {
      msg = (await resp.json()).error || "";
    } catch {
      /* non-JSON body — fall through to the generic message */
    }
    showError(msg || "Couldn't send the verification code. Try again.");
    return false;
  }

  function enterCodeStage(email) {
    regStage = "code";
    if (codeEmailEl) codeEmailEl.textContent = email;
    if (codeBlock) codeBlock.style.display = "";
    // The form step is done — hide it so the code has the user's focus.
    // Email stays visible (and typable: editing it just means the code
    // won't match, and the server rejects a mismatched email anyway).
    nameInput.style.display = "none";
    syncConsentState();
    if (codeInput) codeInput.focus();
  }

  function leaveCodeStage() {
    regStage = "form";
    if (codeBlock) codeBlock.style.display = "none";
    if (codeInput) codeInput.value = "";
    syncConsentState();
  }

  // Mirrors the server's acceptable-form check (not deliverability).
  const EMAIL_RE = /^[^@\s]+@[^@\s]+\.[^@\s]+$/;

  async function handleSubmit() {
    clearError();
    const email = (emailInput.value || "").trim();
    if (!EMAIL_RE.test(email) || email.length > 254) {
      showError("Enter a valid email address.");
      return;
    }
    const name = (nameInput.value || "").trim();

    // Registration step 1: no ceremony yet — just get the code emailed.
    if (mode === "register" && regStage === "form") {
      busy(true);
      try {
        if (await sendCode(email)) enterCodeStage(email);
      } finally {
        busy(false);
      }
      return;
    }

    let code = "";
    if (mode === "register") {
      code = (codeInput && codeInput.value ? codeInput.value : "").trim();
      if (!/^\d{6}$/.test(code)) {
        showError("Enter the 6-digit code from the email.");
        return;
      }
    }

    busy(true);
    try {
      const resp = mode === "register" ? await doRegister(email, name, code) : await doLogin(email);
      const data = await resp.json().catch(() => ({}));
      if (resp.ok && data.ok) {
        window.location.href = data.redirect || "/";
        return;
      }
      showError(data.error || "Something went wrong. Try again.");
    } catch (err) {
      // Log the real cause for support/diagnosis; the visible text stays
      // user-appropriate but is no longer a single catch-all.
      console.error("[passkey]", mode, err && err.name, err && err.message, err);

      if (err && err.name === "BeginError" && err.serverMessage) {
        // The server explained itself (already registered, terms not
        // accepted, invalid email) — show that, don't bury it.
        showError(err.serverMessage);
      } else if (err && err.name === "InvalidStateError") {
        showError(
          mode === "register"
            ? "This device already has a passkey for that email. Sign in instead — or remove the old passkey in your device's password settings and try again."
            : "That passkey can't be used here. Try a recovery sign-in."
        );
      } else if (err && err.name === "NotAllowedError") {
        showError(
          mode === "register"
            ? "The passkey prompt was dismissed, timed out, or your device refused to create a second passkey for this email. If you previously had an account here, remove the old passkey in your device's password settings and try again."
            : "The passkey prompt was dismissed or timed out. Try again, or use a recovery sign-in."
        );
      } else if (err && (err.name === "SecurityError" || err.name === "NotSupportedError")) {
        showError("Passkeys aren't available on this connection or device. Use a recovery sign-in.");
      } else {
        showError(
          mode === "register"
            ? "Couldn't create a passkey. Try again, or use a recovery sign-in."
            : "Couldn't sign you in with a passkey. Check the email and try again."
        );
      }
    } finally {
      busy(false);
    }
  }

  form.addEventListener("submit", (e) => {
    e.preventDefault();
    handleSubmit();
  });

  // Registration requires accepting the disclaimer. The button stays disabled
  // until the box is ticked; the server independently rejects an unaccepted
  // registration, so this is convenience, not the security boundary.
  function syncConsentState() {
    if (!consentBlock || !acceptBox) return;
    const registering = mode === "register";
    // Consent was already given when the code was requested — in the code
    // stage the big terms block makes way for the code input.
    consentBlock.style.display = registering && regStage === "form" ? "" : "none";
    signinBtn.disabled = registering && !acceptBox.checked;
    signinBtn.classList.toggle("is-disabled", signinBtn.disabled);
    signinBtn.querySelector("span").textContent = idleLabel();
  }

  if (acceptBox) {
    acceptBox.addEventListener("change", () => {
      syncConsentState();
      clearError();
    });
  }

  registerToggle.addEventListener("click", () => {
    leaveCodeStage(); // switching modes always restarts registration at step 1
    if (mode === "login") {
      mode = "register";
      nameInput.style.display = "";
      registerToggle.textContent = "Already have an account? Sign in";
    } else {
      mode = "login";
      nameInput.style.display = "none";
      registerToggle.textContent = "New here? Create an account with a passkey";
    }
    syncConsentState();
    clearError();
  });

  if (resendBtn) {
    resendBtn.addEventListener("click", async () => {
      clearError();
      const email = (emailInput.value || "").trim();
      resendBtn.disabled = true;
      try {
        if (await sendCode(email)) {
          resendBtn.textContent = "Code sent — check your inbox.";
          setTimeout(() => {
            resendBtn.textContent = "Didn't get it? Resend the code";
            resendBtn.disabled = false;
          }, 5000);
          return;
        }
      } finally {
        if (resendBtn.disabled && resendBtn.textContent.indexOf("sent") === -1) {
          resendBtn.disabled = false;
        }
      }
    });
  }
})();
