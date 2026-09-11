const form = document.getElementById("loginForm");
const err = document.getElementById("loginError");
const btn = document.getElementById("loginBtn");

form.addEventListener("submit", async (e) => {
  e.preventDefault();
  err.hidden = true;
  btn.disabled = true;
  try {
    const res = await fetch("/api/auth/login", {
      method: "POST",
      credentials: "same-origin",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        email: document.getElementById("email").value,
        password: document.getElementById("password").value,
      }),
    });
    if (res.ok) {
      // Only same-origin paths: "//evil.example" is a protocol-relative URL.
      const next = new URLSearchParams(location.search).get("next") || "";
      location.href = next.startsWith("/") && !next.startsWith("//") ? next : "/";
      return;
    }
    const body = await res.json().catch(() => ({}));
    err.textContent = body.detail || "Could not sign in.";
    err.hidden = false;
  } catch {
    err.textContent = "Could not reach the server.";
    err.hidden = false;
  } finally {
    btn.disabled = false;
  }
});
