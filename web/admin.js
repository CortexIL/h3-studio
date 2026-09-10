import { api, mountHeader } from "/static/auth.js";

const $ = (id) => document.getElementById(id);

function flash(id, text) {
  const el = $(id);
  el.textContent = text || "";
  el.hidden = !text;
}

function cell(row, text) {
  const td = row.insertCell();
  td.textContent = text;      // every value here came from somebody's keyboard
  return td;
}

// ─────────────────── users ───────────────────

async function renderUsers() {
  const { users } = await api("/api/admin/users");
  const body = $("userRows");
  body.textContent = "";
  for (const u of users) {
    const row = body.insertRow();
    cell(row, u.email);
    cell(row, u.role);
    cell(row, u.is_active ? "active" : "disabled");
    const actions = row.insertCell();

    const toggle = document.createElement("button");
    toggle.className = "btn";
    toggle.textContent = u.is_active ? "Disable" : "Enable";
    toggle.addEventListener("click", async () => {
      try {
        await api(`/api/admin/users/${u.id}`, {
          method: "PATCH", body: JSON.stringify({ is_active: !u.is_active }) });
        flash("error", "");
        renderUsers();
      } catch (e) { flash("error", e.message); }
    });

    const reset = document.createElement("button");
    reset.className = "btn";
    reset.textContent = "Reset password";
    reset.addEventListener("click", async () => {
      const pw = prompt(`New password for ${u.email} (8 characters or more)`);
      if (!pw) return;
      try {
        await api(`/api/admin/users/${u.id}`, {
          method: "PATCH", body: JSON.stringify({ password: pw }) });
        // No mail server here, so the admin hands it over themselves.
        flash("notice", `Password changed for ${u.email}. Give it to them directly.`);
      } catch (e) { flash("error", e.message); }
    });

    actions.append(toggle, reset);
  }
}

$("newUser").addEventListener("submit", async (e) => {
  e.preventDefault();
  flash("userError", "");
  try {
    await api("/api/admin/users", {
      method: "POST",
      body: JSON.stringify({ email: $("newEmail").value,
                             password: $("newPassword").value,
                             role: $("newRole").value }),
    });
    e.target.reset();
    renderUsers();
  } catch (err) { flash("userError", err.message); }
});

// ─────────────────── gpu ───────────────────

async function renderGpu() {
  let s;
  try { s = await api("/api/admin/status"); } catch { return; }
  $("podState").textContent =
    s.pod.state + (s.pod.detail ? ` — ${s.pod.detail}` : "");
  $("podGpu").textContent = s.pod.gpu
    ? `${s.pod.gpu} · $${s.pod.rate_per_hour.toFixed(2)}/hr` : "—";
  $("sessionCost").textContent =
    `$${s.session.cost_usd.toFixed(2)} of $${s.session.limit_usd.toFixed(2)}`;
  $("leaderWarning").hidden = s.leader;
  if (!$("budgetInput").matches(":focus")) {
    $("budgetInput").value = s.session.limit_usd;
  }
  for (const input of document.querySelectorAll("input[name=policy]")) {
    input.checked = input.value === s.policy;
  }
}

for (const input of document.querySelectorAll("input[name=policy]")) {
  input.addEventListener("change", async () => {
    try {
      await api("/api/admin/policy", {
        method: "POST", body: JSON.stringify({ policy: input.value }) });
      flash("notice", `Policy set to ${input.value}.`);
      renderGpu();
    } catch (e) { flash("error", e.message); }
  });
}

$("saveBudget").addEventListener("click", async () => {
  try {
    await api("/api/admin/budget", {
      method: "POST",
      body: JSON.stringify({ session_limit_usd: Number($("budgetInput").value) }) });
    flash("notice", "Budget ceiling saved.");
    renderGpu();
  } catch (e) { flash("error", e.message); }
});

// ─────────────────── runpod key ───────────────────

async function renderKeyState() {
  try {
    const k = await api("/api/admin/key-state");
    $("keyNote").textContent = k.present
      ? `A key ending ${k.hint} is loaded.`
      : "No key loaded — the app is running in demo mode.";
    $("keyNote").hidden = false;
  } catch { /* leave the note as it is */ }
}

$("saveKey").addEventListener("click", async () => {
  const btn = $("saveKey");
  btn.disabled = true;
  const was = btn.textContent;
  btn.textContent = "Checking…";
  try {
    const r = await api("/api/admin/runpod-key", {
      method: "POST", body: JSON.stringify({ key: $("keyInput").value.trim() }) });
    $("keyInput").value = "";
    $("keyNote").textContent = `Verified and saved (ends ${r.hint}). ${r.note}`;
    $("keyNote").hidden = false;
    flash("error", "");
  } catch (e) {
    flash("error", e.message);
  } finally {
    btn.disabled = false;
    btn.textContent = was;
  }
});

// ─────────────────── runs ───────────────────

async function renderRuns() {
  const { runs, total_cost_usd } = await api("/api/admin/runs");
  const body = $("runRows");
  body.textContent = "";
  for (const r of runs) {
    const row = body.insertRow();
    cell(row, r.started_at ? new Date(r.started_at * 1000).toLocaleString() : "—");
    cell(row, r.gpu_type || "—");
    cell(row, r.status);
    cell(row, `$${Number(r.cost_estimate).toFixed(4)}`);
    cell(row, r.note || "");
  }
  $("totalCost").textContent = `$${total_cost_usd.toFixed(2)}`;
}

// ─────────────────── boot ───────────────────

mountHeader().catch(() => { /* already redirected */ });
renderUsers().catch((e) => flash("error", e.message));
renderGpu();
renderKeyState();
renderRuns().catch((e) => flash("error", e.message));
// The pod state moves on its own; the user and run tables only change when
// somebody on this page changes them.
setInterval(renderGpu, 5000);
