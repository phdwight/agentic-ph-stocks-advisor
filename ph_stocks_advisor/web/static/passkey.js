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

  let mode = "login"; // "login" | "register"

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
  function busy(on) {
    signinBtn.disabled = on;
    signinBtn.querySelector("span").textContent = on
      ? "Waiting for your device…"
      : "Sign in with a passkey";
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
    if (!optResp.ok) throw new Error("begin");
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

  async function doRegister(email, name) {
    const optResp = await postJSON("/auth/passkey/register/begin", { email, name });
    if (!optResp.ok) throw new Error("begin");
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
    busy(true);
    try {
      const resp = mode === "register" ? await doRegister(email, name) : await doLogin(email);
      const data = await resp.json().catch(() => ({}));
      if (resp.ok && data.ok) {
        window.location.href = data.redirect || "/";
        return;
      }
      showError(data.error || "Something went wrong. Try again.");
    } catch (err) {
      // NotAllowedError = user cancelled / no matching passkey; keep it generic.
      showError(
        mode === "register"
          ? "Couldn't create a passkey. Try again, or use a recovery sign-in."
          : "Couldn't sign you in with a passkey. Check the email and try again."
      );
    } finally {
      busy(false);
    }
  }

  form.addEventListener("submit", (e) => {
    e.preventDefault();
    handleSubmit();
  });

  registerToggle.addEventListener("click", () => {
    if (mode === "login") {
      mode = "register";
      nameInput.style.display = "";
      signinBtn.querySelector("span").textContent = "Create account with a passkey";
      registerToggle.textContent = "Already have an account? Sign in";
    } else {
      mode = "login";
      nameInput.style.display = "none";
      signinBtn.querySelector("span").textContent = "Sign in with a passkey";
      registerToggle.textContent = "New here? Create an account with a passkey";
    }
    clearError();
  });
})();
