// One fetch wrapper for every page. A 401 anywhere means the session is gone, and
// the only useful response is the login screen - handling that per call site would
// mean handling it wrongly in the one place somebody forgets.

export async function api(path, options = {}) {
  const res = await fetch(path, {
    credentials: "same-origin",
    headers: { "Content-Type": "application/json", ...(options.headers || {}) },
    ...options,
  });
  if (res.status === 401 && !location.pathname.startsWith("/login")) {
    location.href = "/login";
    throw new Error("signed out");
  }
  if (!res.ok) {
    let detail = res.statusText;
    try { detail = (await res.json()).detail || detail; } catch { /* not JSON */ }
    throw new Error(detail);
  }
  return res.status === 204 ? null : res.json();
}

// Multipart, so it must not carry the JSON content type the wrapper above sets.
export async function upload(path, file, filename) {
  const fd = new FormData();
  fd.append("file", file, filename || file.name || "upload");
  const res = await fetch(path, { method: "POST", credentials: "same-origin",
                                  body: fd });
  if (res.status === 401) { location.href = "/login"; throw new Error("signed out"); }
  if (!res.ok) {
    let detail = res.statusText;
    try { detail = (await res.json()).detail || detail; } catch { /* not JSON */ }
    throw new Error(detail);
  }
  return res.json();
}

export async function signOut() {
  await fetch("/api/auth/logout", { method: "POST", credentials: "same-origin" });
  location.href = "/login";
}

/** Fill the shared header: who is signed in, the admin link, the sign-out button. */
export async function mountHeader() {
  const me = await api("/api/me");
  const who = document.getElementById("who");
  if (who) who.textContent = me.email;
  const adminLink = document.getElementById("adminLink");
  if (adminLink && me.role === "admin") adminLink.hidden = false;
  const out = document.getElementById("signout");
  if (out) out.addEventListener("click", signOut);
  return me;
}
