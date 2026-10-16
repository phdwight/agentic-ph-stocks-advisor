/**
 * Passkey (WebAuthn) sign-in — email-first.
 *
 * "Continue" sends the typed email to the server, which picks the method:
 * an account with a passkey runs the authentication ceremony; an account
 * without one (or an email with no account yet) is emailed a one-time
 * 6-digit code, and the form switches to a code field (plus name + consent
 * for a brand-new account). "Create an account" reveals a name field and
 * runs passkey registration.
 * All POSTs carry the CSRF token.
 */
(function () {
  "use strict";

  const form = document.getElementById("passkey-form");
  if (!form) return; // No passkey UI — leave OAuth as the path.

  // Without WebAuthn the emailed-code path still works; only the passkey
  // ceremonies need it, and they report the problem when they're reached.
  const webauthnOK = !!window.PublicKeyCredential;
  const NO_WEBAUTHN_MSG = "This browser doesn't support passkeys. Use a recovery sign-in below.";

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
  const codeLeadEl = document.getElementById("pk-code-lead");
  const codeTailEl = document.getElementById("pk-code-tail");
  const resendBtn = document.getElementById("pk-resend");

  let mode = "login"; // "login" | "register"
  // Registration is two steps: "form" (email + consent → server emails a
  // code) then "code" (type the code → WebAuthn ceremony).
  let regStage = "form";
  // Sign-in is "email" (server picks passkey or emailed code) then, for an
  // email without a passkey, "code" (type the emailed one-time code).
  let loginStage = "email";
  let loginNew = false; // code-stage email has no account yet → name + consent
  let loginEmail = ""; // the (normalised) address the sign-in code went to

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
    if (mode === "register") {
      return regStage === "code" ? "Verify code & create account" : "Email me a verification code";
    }
    if (loginStage === "code") return loginNew ? "Verify code & create account" : "Verify code & sign in";
    return "Continue";
  }
  const btnSpinner = signinBtn ? signinBtn.querySelector(".pk-btn-spinner") : null;

  function busy(on, label) {
    signinBtn.querySelector("span").textContent = on ? label || "Working…" : idleLabel();
    signinBtn.classList.toggle("is-busy", !!on);
    signinBtn.setAttribute("aria-busy", on ? "true" : "false");
    if (btnSpinner) btnSpinner.hidden = !on;
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

  /** An error carrying a message meant for the user (shown verbatim). */
  function userError(msg, status) {
    const err = new Error(msg);
    err.name = "BeginError";
    err.serverMessage = msg;
    err.status = status;
    return err;
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
    const err = userError(msg || `Request failed (${resp.status})`, resp.status);
    err.serverMessage = msg;
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
  /** Ask the server how this email signs in: WebAuthn options, or
   *  ``{otp: true, new_account, sent}`` when a code was emailed instead. */
  async function beginLogin(email) {
    const optResp = await postJSON("/auth/passkey/login/begin", { email });
    if (!optResp.ok) throw await beginError(optResp);
    return optResp.json();
  }

  async function doLogin(options) {
    if (!webauthnOK) throw userError(NO_WEBAUTHN_MSG);
    options.challenge = b64urlToBuf(options.challenge);
    (options.allowCredentials || []).forEach((c) => (c.id = b64urlToBuf(c.id)));

    busy(true, "Waiting for passkey…");
    const cred = await navigator.credentials.get({ publicKey: options });
    busy(true, "Signing in…");
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

  /** Sign in with the emailed one-time code. */
  async function doCodeLogin(email, code, name) {
    return postJSON("/auth/passkey/login/verify-code", {
      email,
      code,
      name,
      // Only a brand-new account needs consent; the server re-checks.
      accept_disclaimer: loginNew && !!(acceptBox && acceptBox.checked),
    });
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

    busy(true, "Waiting for passkey…");
    const cred = await navigator.credentials.create({ publicKey: options });
    busy(true, "Creating account…");
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

  /** Ask the server to (re)email a code for the current flow; true on success. */
  async function sendCode(email) {
    const resp =
      mode === "register"
        ? await postJSON("/auth/passkey/register/send-code", {
            email,
            accept_disclaimer: true, // the server re-checks; the UI cannot be the only gate
          })
        : await postJSON("/auth/passkey/login/send-code", { email });
    if (resp.ok) return true;
    let data = {};
    try {
      data = (await resp.json()) || {};
    } catch {
      /* non-JSON body — fall through to the generic message */
    }
    // The account gained a passkey — go back so "Continue" runs the ceremony.
    if (data.use_passkey && mode === "login") leaveLoginCodeStage();
    showError(data.error || "Couldn't send the code. Try again.");
    return false;
  }

  function setCodeHint(lead, email, tail) {
    if (codeLeadEl) codeLeadEl.textContent = lead;
    if (codeEmailEl) codeEmailEl.textContent = email;
    if (codeTailEl) codeTailEl.textContent = tail;
  }

  function enterCodeStage(email) {
    regStage = "code";
    setCodeHint("We emailed a 6-digit code to", email, "Enter it below to confirm your email.");
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

  /** No passkey for this email — a sign-in code was emailed (or, if
   *  ``info.sent`` is false, one sent moments ago is still valid). */
  function enterLoginCodeStage(email, info) {
    loginStage = "code";
    loginNew = !!info.new_account;
    loginEmail = (info.email || email).toLowerCase();
    setCodeHint(
      info.sent === false ? "We already emailed a 6-digit sign-in code to" : "We emailed a 6-digit sign-in code to",
      loginEmail,
      loginNew
        ? "There's no account for this email yet — enter the code, your name, and accept the terms below to create one."
        : "Enter it below to sign in."
    );
    if (codeBlock) codeBlock.style.display = "";
    if (codeInput) codeInput.value = "";
    // A new account gets a display name, like passkey sign-up.
    nameInput.style.display = loginNew ? "" : "none";
    syncConsentState();
    if (codeInput) codeInput.focus();
  }

  function leaveLoginCodeStage() {
    loginStage = "email";
    loginNew = false;
    loginEmail = "";
    if (codeBlock) codeBlock.style.display = "none";
    if (codeInput) codeInput.value = "";
    if (mode === "login") nameInput.style.display = "none";
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
      if (!webauthnOK) {
        showError(NO_WEBAUTHN_MSG);
        return;
      }
      busy(true, "Sending code…");
      try {
        if (await sendCode(email)) enterCodeStage(email);
      } finally {
        busy(false);
      }
      return;
    }

    const loginByCode = mode === "login" && loginStage === "code";
    let code = "";
    if (mode === "register" || loginByCode) {
      code = (codeInput && codeInput.value ? codeInput.value : "").replace(/\s+/g, "");
      if (!/^\d{6}$/.test(code)) {
        showError("Enter the 6-digit code from the email.");
        return;
      }
    }

    let redirecting = false;
    busy(true, mode === "register" || loginByCode ? "Verifying code…" : "Working…");
    try {
      let resp;
      if (mode === "register") {
        resp = await doRegister(email, name, code);
      } else if (loginByCode) {
        resp = await doCodeLogin(email, code, name);
      } else {
        const begin = await beginLogin(email);
        if (begin.otp) {
          // No passkey on this account (or no account yet) — a code was emailed.
          enterLoginCodeStage(email, begin);
          return;
        }
        resp = await doLogin(begin);
      }
      const data = await resp.json().catch(() => ({}));
      if (resp.ok && data.ok) {
        redirecting = true;
        busy(true, "Redirecting…");
        window.location.href = data.redirect || "/";
        return;
      }
      // The account gained a passkey since the code went out — step back
      // so "Continue" runs the passkey ceremony instead.
      if (data.use_passkey && loginByCode) leaveLoginCodeStage();
      showError(data.error || "Something went wrong. Try again.");
    } catch (err) {
      // Log the real cause for support/diagnosis; the visible text stays
      // user-appropriate but is no longer a single catch-all.
      console.error("[passkey]", mode, err && err.name, err && err.message, err);

      if (err && err.name === "BeginError" && err.serverMessage) {
        // The server explained itself (already registered, terms not
        // accepted, invalid email) — show that, don't bury it.
        showError(err.serverMessage);
      } else if (loginByCode) {
        showError("Couldn't sign you in right now. Check your connection and try again.");
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
      if (!redirecting) busy(false);
    }
  }

  form.addEventListener("submit", (e) => {
    e.preventDefault();
    handleSubmit();
  });

  // Creating an account requires accepting the disclaimer — both passkey
  // registration and a new account via an emailed sign-in code. The button
  // stays disabled until the box is ticked; the server independently rejects
  // an unaccepted sign-up, so this is convenience, not the security boundary.
  function syncConsentState() {
    const registering = mode === "register";
    const newViaCode = mode === "login" && loginStage === "code" && loginNew;
    if (consentBlock && acceptBox) {
      // Consent was already given when the registration code was requested
      // — in that code stage the big terms block makes way for the code
      // input. A new account via sign-in code shows it alongside the code.
      consentBlock.style.display = (registering && regStage === "form") || newViaCode ? "" : "none";
      signinBtn.disabled = (registering || newViaCode) && !acceptBox.checked;
    } else {
      signinBtn.disabled = false;
    }
    signinBtn.classList.toggle("is-disabled", signinBtn.disabled);
    signinBtn.querySelector("span").textContent = idleLabel();
  }

  if (acceptBox) {
    acceptBox.addEventListener("change", () => {
      syncConsentState();
      clearError();
    });
  }

  // Editing the email after a sign-in code went out means a different
  // account — drop back to the email step so "Continue" starts afresh.
  emailInput.addEventListener("input", () => {
    if (mode === "login" && loginStage === "code" && emailInput.value.trim().toLowerCase() !== loginEmail) {
      leaveLoginCodeStage();
      clearError();
    }
  });

  registerToggle.addEventListener("click", () => {
    // Switching modes always restarts either flow at step 1.
    leaveCodeStage();
    leaveLoginCodeStage();
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
      resendBtn.classList.add("is-busy");
      resendBtn.textContent = "Sending code…";
      try {
        if (await sendCode(email)) {
          resendBtn.classList.remove("is-busy");
          resendBtn.textContent = "Code sent — check your inbox.";
          setTimeout(() => {
            resendBtn.textContent = "Didn't get it? Resend the code";
            resendBtn.disabled = false;
          }, 5000);
          return;
        }
      } finally {
        resendBtn.classList.remove("is-busy");
        if (resendBtn.disabled && resendBtn.textContent.indexOf("sent") === -1) {
          resendBtn.textContent = "Didn't get it? Resend the code";
          resendBtn.disabled = false;
        }
      }
    });
  }
})();
