// Dumb client. Every decision is made server-side, so this file stays the same when
// the app moves from localhost to a shared team instance.

const $ = (id) => document.getElementById(id);

const api = async (path, opts = {}) => {
  const r = await fetch(path, { headers: { "Content-Type": "application/json" }, ...opts });
  if (!r.ok) {
    let detail = r.statusText;
    try { detail = (await r.json()).detail || detail; } catch {}
    throw new Error(detail);
  }
  return r.json();
};

let cfg = null;
let refImages = [];        // references staged for the next submit
let jobsCache = [];
let filter = "all";
let formDefaults = null;   // set once the server reports them
let splitMode = "single";

const POD_LABEL = { off: "Off", booting: "Starting", ready: "Ready",
                    stopping: "Stopping", error: "Error" };
const STATUS_LABEL = { queued: "Queued", running: "Generating", done: "Ready",
                       failed: "Failed", cancelled: "Cancelled" };
const MODE_LABEL = { t2v: "text → video", i2v: "image → video", r2v: "reference → video" };

const esc = (s) => String(s ?? "").replace(/[&<>"]/g, (c) =>
  ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
const fileOf = (p) => String(p || "").split(/[\\/]/).pop();

function fmtDur(s) {
  s = Math.round(s);
  if (s < 60) return `${s}s`;
  const m = Math.floor(s / 60);
  return m < 60 ? `${m}m ${s % 60}s` : `${Math.floor(m / 60)}h ${m % 60}m`;
}

// Notices come from two places - the server, and this file reacting to a click.
// Without a hold, the next 2-second poll wipes a local message before it can be read.
const NOTICE_HOLD_MS = 5000;
let localNoticeUntil = 0;

function show(id, text, opts = {}) {
  const el = $(id);
  if (id === "notice") {
    if (opts.fromServer) { if (Date.now() < localNoticeUntil) return; }
    else if (text) localNoticeUntil = Date.now() + NOTICE_HOLD_MS;
  }
  el.hidden = !text;
  el.textContent = text || "";
}

// ─────────────────── status ───────────────────

async function refreshStatus() {
  let s;
  try { s = await api("/api/status"); } catch { return; }
  cfg = s.config;

  $("mockBadge").hidden = !cfg.mock;

  const pod = s.pod;
  $("podDot").className = `dot dot-${pod.state}`;
  $("podState").textContent = POD_LABEL[pod.state] || pod.state;
  $("podDetail").textContent = pod.detail || "";
  $("podGpu").textContent = pod.gpu && pod.gpu !== "?"
    ? `${pod.gpu} · $${pod.rate_per_hour.toFixed(2)}/hr` : "";

  const sess = s.session;
  const cost = $("podCost");
  if (sess.seconds > 0) {
    cost.textContent = `$${sess.cost_usd.toFixed(3)} · ${fmtDur(sess.seconds)}`;
    cost.classList.toggle("warn", sess.cost_usd >= sess.warn_usd);
  } else cost.textContent = "";

  document.querySelectorAll(".pol").forEach((b) =>
    b.classList.toggle("active", b.dataset.policy === s.policy));

  const c = s.counts;
  $("counts").innerHTML =
    `<span>Queued <b>${c.queued}</b></span><span>Running <b>${c.running}</b></span>` +
    `<span>Ready <b>${c.done}</b></span>` +
    (c.failed ? `<span>Failed <b>${c.failed}</b></span>` : "");

  show("notice", s.notice, { fromServer: true });
  show("error", s.error);

  $("setupCard").hidden = !!s.has_api_key;
  $("keyState").hidden = !s.has_api_key;
  $("keyRow").hidden = !!s.has_api_key && $("settings").dataset.editingKey !== "1";
  $("keyHint").textContent = s.api_key_hint || "";

  if (!$("folder").matches(":focus")) $("folder").value = s.output_folder || "";
  $("inbox").value = s.inbox_folder || "";

  for (const f of s.inbox_new || []) {
    if (!refImages.some((r) => r.path === f.path)) {
      refImages.push({ path: f.path, name: f.name, from: "inbox" });
      renderTiles();
    }
  }
  for (const b of s.inbox_batches || []) {
    if (b.error) { show("error", `Could not read ${b.source}: ${b.error}`); continue; }
    const miss = b.missing_images?.length
      ? ` (${b.missing_images.length} image(s) never arrived)` : "";
    show("notice", `Queued ${b.queued} job(s) from ${b.source}${miss}`);
  }

  // Defaults come from the server, not the markup, so the UI, the HTTP API and the
  // MCP server all start from the same place.
  if (!$("preset").options.length && cfg.presets) {
    const names = { draft: "Draft", final: "Final" };
    $("preset").innerHTML = Object.entries(cfg.presets).map(([k, v]) =>
      `<option value="${k}"${k === cfg.default_preset ? " selected" : ""}>` +
      `${names[k] || k} ${v.width}×${v.height}</option>`).join("");
    $("seconds").value = cfg.default_seconds;
    if (cfg.default_mode) $("mode").value = cfg.default_mode;
    formDefaults = { preset: cfg.default_preset, mode: cfg.default_mode,
                     seconds: cfg.default_seconds };
    updateEstimate();
  }
}

// ─────────────────── feed ───────────────────

function stage(j) {
  if (j.status === "done" && j.output_path) {
    return `<video src="/api/preview/${j.id}" controls preload="metadata"
                   playsinline poster=""></video>`;
  }
  const bg = j.ref_images?.length
    ? `<img src="/api/image/${encodeURIComponent(fileOf(j.ref_images[0]))}" alt="">` : "";
  const label = j.status === "running" ? "Generating…"
    : j.status === "failed" ? "Failed"
    : j.status === "cancelled" ? "Cancelled" : "Waiting in the queue";
  const sub = j.status === "running" ? "the GPU is on this one now"
    : j.status === "failed" ? esc(j.error || "")
    : j.status === "cancelled" ? "" : "nothing is charged until it starts";
  return `${bg}<div class="waiting"><b>${label}</b><span>${sub}</span></div>`;
}

function entryHtml(j) {
  const chips = [
    `<span class="metachip"><span class="stat ${j.status}"></span>${STATUS_LABEL[j.status] || j.status}</span>`,
    `<span class="metachip">${j.seconds}s</span>`,
    `<span class="metachip">${esc(j.preset)}</span>`,
    `<span class="metachip">${MODE_LABEL[j.mode] || j.mode}</span>`,
  ].join("");

  const refs = (j.ref_images || []).map((p) =>
    `<img src="/api/image/${encodeURIComponent(fileOf(p))}" alt="" loading="lazy"
          title="${esc(fileOf(p))}">`).join("");

  const canCancel = j.status === "queued" || j.status === "running";
  const actions = [
    `<button class="btn" data-again="${j.id}" title="Copy this back into the form on the left">Use again</button>`,
    canCancel ? `<button class="btn danger-ghost" data-cancel="${j.id}">Cancel</button>` : "",
    j.status !== "running" ? `<button class="icon-btn" data-del="${j.id}" title="Remove from the list (keeps the file)">🗑</button>` : "",
  ].join("");

  return `<article class="entry ${j.status === "done" ? "" : "pending"}" data-entry="${j.id}">
    <div class="stage">${stage(j)}</div>
    <div class="entry-side">
      <div class="entry-prompt">${esc(j.prompt)}</div>
      ${refs ? `<div class="entry-refs">${refs}</div>` : ""}
      <div class="metachips">${chips}</div>
      ${j.output_path ? `<div class="hint tiny">${esc(fileOf(j.output_path))}</div>` : ""}
      <div class="entry-actions">${actions}</div>
    </div>
  </article>`;
}

function visible(jobs) {
  if (filter === "pending") return jobs.filter((j) => ["queued", "running"].includes(j.status));
  if (filter === "done") return jobs.filter((j) => j.status === "done");
  return jobs;
}

async function refreshFeed() {
  let jobs;
  try { ({ jobs } = await api("/api/jobs")); } catch { return; }

  // Re-rendering every 2.5s would restart any video the user is watching, so only
  // redraw when something actually changed.
  const sig = JSON.stringify(jobs.map((j) => [j.id, j.status, j.output_path, j.prompt]));
  if (sig === refreshFeed.sig && filter === refreshFeed.filter) { jobsCache = jobs; return; }
  refreshFeed.sig = sig;
  refreshFeed.filter = filter;
  jobsCache = jobs;

  const list = visible(jobs);
  $("feed").innerHTML = list.length
    ? list.map(entryHtml).join("")
    : `<div class="empty">${jobs.length ? "Nothing matches this filter."
        : "Nothing here yet. Write a prompt on the left and add it to the queue."}</div>`;
}

// ─────────────────── use again ───────────────────

async function useAgain(id) {
  const j = jobsCache.find((x) => x.id === id);
  if (!j) return;

  $("prompts").value = j.prompt || "";
  $("seconds").value = j.seconds;
  $("mode").value = j.mode || "t2v";
  $("count").value = 1;
  if ([...$("preset").options].some((o) => o.value === j.preset)) $("preset").value = j.preset;

  // Reference images come across as staged uploads, so the copy is a complete,
  // editable starting point rather than a half-filled form.
  refImages = (j.ref_images || []).map((p) => ({ path: p, name: fileOf(p) }));
  renderTiles();
  updateEstimate();

  $("prompts").focus();
  $("prompts").setSelectionRange($("prompts").value.length, $("prompts").value.length);
  document.querySelector(".col-create .col-body").scrollTop = 0;
  show("notice", "Copied into the form — edit it, then Add to queue");
}

// ─────────────────── compose ───────────────────

const BATCH_EXT = /\.(zip|json|txt)$/i;

async function uploadFiles(files) {
  let added = 0, batches = 0;
  const rejected = [];

  for (const file of files) {
    if (BATCH_EXT.test(file.name || "")) {
      // Zips and batches go through the watched folder, so a file dragged onto the
      // window and one saved into the folder by another app take the same path in.
      const fd = new FormData();
      fd.append("file", file, file.name);
      try {
        await fetch("/api/inbox/upload", { method: "POST", body: fd })
          .then(async (x) => { if (!x.ok) throw new Error((await x.json()).detail); });
        batches++;
      } catch (e) { show("error", e.message || `Could not accept ${file.name}`); }
      continue;
    }
    if (!file.type.startsWith("image/")) { rejected.push(file.name || "file"); continue; }
    const fd = new FormData();
    fd.append("file", file, file.name || "pasted.png");
    try {
      const r = await fetch("/api/upload", { method: "POST", body: fd }).then((x) => x.json());
      refImages.push({ path: r.path, name: file.name || r.name });
      added++;
    } catch { show("error", "Upload failed"); }
  }

  if (batches) show("notice", `Reading ${batches} batch file(s) — entries appear shortly`);
  if (rejected.length) {
    show("error", `Not usable: ${rejected.join(", ")}. Images, .zip or .json/.txt only.`);
  }
  if (added) {
    renderTiles();
    // A reference on a text-to-video job would be silently ignored downstream.
    if ($("mode").value === "t2v") {
      $("mode").value = "i2v";
      show("notice", `${added} reference(s) added — switched to image → video`);
    }
    updateEstimate();
  }
  return added;
}

function renderTiles() {
  const box = $("refTiles");
  const add = box.querySelector(".tile-add");
  box.innerHTML = "";
  refImages.forEach((r, i) => {
    const el = document.createElement("div");
    el.className = `tile${r.from === "inbox" ? " from-inbox" : ""}`;
    el.title = r.name;
    el.innerHTML = `<img src="/api/image/${encodeURIComponent(fileOf(r.path))}" alt="">
                    <button class="x" data-ref="${i}" title="Remove">×</button>`;
    box.appendChild(el);
  });
  box.appendChild(add);
}

function payload() {
  return {
    prompts: $("prompts").value,
    seconds: +$("seconds").value || undefined,
    preset: $("preset").value || undefined,
    mode: $("mode").value,
    count: +$("count").value || 1,
    split: splitMode,
    ref_images: refImages.map((r) => r.path),
  };
}

let estTimer = null;
function updateEstimate() {
  clearTimeout(estTimer);
  estTimer = setTimeout(async () => {
    const body = payload();
    if (!body.prompts.trim()) {
      $("estimate").textContent = "";
      $("submitCost").textContent = "";
      return;
    }
    try {
      const e = await api("/api/estimate", { method: "POST", body: JSON.stringify(body) });
      const tag = e.confidence === "estimated"
        ? `<span class="tag">Estimated, not measured — run calibration for a real number.</span>`
        : `<span class="tag ok">Measured on your account.</span>`;
      $("estimate").innerHTML =
        `<b>${e.clips}</b> clips · <b>${fmtDur(e.total_minutes * 60)}</b> · ` +
        `<b>$${e.cost_usd.toFixed(2)}</b> ($${e.cost_per_clip_usd.toFixed(3)} each)${tag}`;
      $("submitCost").textContent = `$${e.cost_usd.toFixed(2)}`;
    } catch {
      $("estimate").textContent = "";
      $("submitCost").textContent = "";
    }
  }, 350);
}

function clearCompose() {
  $("prompts").value = "";
  refImages = [];
  renderTiles();
  $("estimate").textContent = "";
  $("submitCost").textContent = "";
  $("count").value = 1;
  if (formDefaults) {
    $("seconds").value = formDefaults.seconds;
    if (formDefaults.mode) $("mode").value = formDefaults.mode;
    if (formDefaults.preset &&
        [...$("preset").options].some((o) => o.value === formDefaults.preset)) {
      $("preset").value = formDefaults.preset;
    }
  }
}

// ─────────────────── resizable divider ───────────────────

const MIN_W = 280, MAX_W = 680;
const layout = document.querySelector(".layout");
const savedW = parseInt(localStorage.getItem("h3.leftWidth") || "", 10);
if (savedW >= MIN_W && savedW <= MAX_W) layout.style.setProperty("--left-w", `${savedW}px`);

$("resizer").addEventListener("pointerdown", (e) => {
  e.preventDefault();
  const rz = $("resizer");
  rz.setPointerCapture(e.pointerId);
  rz.classList.add("dragging");
  document.body.classList.add("resizing");
  const startX = e.clientX;
  const startW = document.querySelector(".col-create").getBoundingClientRect().width;

  const move = (ev) => {
    const w = Math.min(MAX_W, Math.max(MIN_W, startW + ev.clientX - startX));
    layout.style.setProperty("--left-w", `${Math.round(w)}px`);
  };
  const up = () => {
    rz.classList.remove("dragging");
    document.body.classList.remove("resizing");
    rz.removeEventListener("pointermove", move);
    rz.removeEventListener("pointerup", up);
    const w = parseInt(layout.style.getPropertyValue("--left-w"), 10);
    if (w) localStorage.setItem("h3.leftWidth", String(w));
  };
  rz.addEventListener("pointermove", move);
  rz.addEventListener("pointerup", up);
});

$("resizer").addEventListener("dblclick", () => {
  layout.style.removeProperty("--left-w");
  localStorage.removeItem("h3.leftWidth");
});

// ─────────────────── wiring ───────────────────

document.querySelectorAll(".pol").forEach((btn) =>
  btn.addEventListener("click", async () => {
    try {
      await api("/api/policy", { method: "POST",
        body: JSON.stringify({ policy: btn.dataset.policy }) });
      refreshStatus();
    } catch (e) { show("error", e.message); }
  }));

document.querySelectorAll(".spl").forEach((btn) =>
  btn.addEventListener("click", () => {
    splitMode = btn.dataset.split;
    document.querySelectorAll(".spl").forEach((b) => b.classList.toggle("active", b === btn));
    updateEstimate();
  }));

document.querySelectorAll(".flt").forEach((btn) =>
  btn.addEventListener("click", () => {
    filter = btn.dataset.filter;
    document.querySelectorAll(".flt").forEach((b) => b.classList.toggle("active", b === btn));
    refreshFeed();
  }));

$("submit").addEventListener("click", async () => {
  const btn = $("submit");
  btn.disabled = true;
  try {
    const r = await api("/api/jobs", { method: "POST", body: JSON.stringify(payload()) });
    clearCompose();
    show("notice", `Added ${r.count} job(s)`);
    refreshFeed(); refreshStatus();
  } catch (e) { show("error", e.message); }
  finally { btn.disabled = false; }
});

$("clearCompose").addEventListener("click", clearCompose);

["prompts", "seconds", "preset", "count"].forEach((id) =>
  $(id).addEventListener("input", updateEstimate));

$("refInput").addEventListener("change", async (ev) => {
  await uploadFiles(ev.target.files);
  ev.target.value = "";
});

const panel = $("composePanel");
let dragDepth = 0;
["dragenter", "dragover"].forEach((evt) =>
  panel.addEventListener(evt, (e) => {
    if (!e.dataTransfer?.types?.includes("Files")) return;
    e.preventDefault();
    if (evt === "dragenter") dragDepth++;
    panel.classList.add("dragging");
  }));
panel.addEventListener("dragleave", () => {
  if (--dragDepth <= 0) { dragDepth = 0; panel.classList.remove("dragging"); }
});
panel.addEventListener("drop", async (e) => {
  e.preventDefault();
  dragDepth = 0;
  panel.classList.remove("dragging");
  await uploadFiles(e.dataTransfer.files);
});

document.addEventListener("paste", async (e) => {
  const files = [...(e.clipboardData?.files || [])];
  if (files.length) { e.preventDefault(); await uploadFiles(files); }
});

$("clearDone").addEventListener("click", async () => {
  await api("/api/jobs/clear-finished", { method: "POST" });
  refreshFeed(); refreshStatus();
});

// Settings
const settings = $("settings");
$("settingsBtn").addEventListener("click", () => { settings.hidden = false; });
$("setClose").addEventListener("click", () => { settings.hidden = true; });
settings.addEventListener("click", (e) => { if (e.target === settings) settings.hidden = true; });
document.addEventListener("keydown", (e) => {
  if (e.key === "Escape" && !settings.hidden) settings.hidden = true;
});

$("changeKey").addEventListener("click", () => {
  settings.dataset.editingKey = "1";
  $("keyRow").hidden = false;
  $("apiKey2").focus();
});

async function saveKey(inputId, btnId) {
  const key = $(inputId).value.trim();
  if (!key) { show("error", "Paste your RunPod API key first"); return; }
  const btn = $(btnId);
  btn.disabled = true;
  const was = btn.textContent;
  btn.textContent = "Checking…";
  try {
    const r = await api("/api/runpod-key", { method: "POST", body: JSON.stringify({ key }) });
    $(inputId).value = "";
    settings.dataset.editingKey = "";
    show("notice", `Key ending ${r.hint} saved. ${r.note}`);
    refreshStatus();
  } catch (e) { show("error", e.message); }
  finally { btn.disabled = false; btn.textContent = was; }
}

$("saveKey").addEventListener("click", () => saveKey("apiKey", "saveKey"));
$("saveKey2").addEventListener("click", () => saveKey("apiKey2", "saveKey2"));

$("saveFolder").addEventListener("click", async () => {
  try {
    await api("/api/folder", { method: "POST",
      body: JSON.stringify({ folder: $("folder").value }) });
    show("notice", "Output folder updated");
  } catch (e) { show("error", e.message); }
});

$("openFolder").addEventListener("click", () =>
  api("/api/folder/open", { method: "POST" }).catch((e) => show("error", e.message)));
$("openInbox").addEventListener("click", () =>
  api("/api/inbox/open", { method: "POST" }).catch((e) => show("error", e.message)));

$("quit").addEventListener("click", async () => {
  const live = cfg && !cfg.mock;
  if (!confirm(live
    ? "Close H3 Studio and shut down the GPU?\n\nRunning jobs return to the queue."
    : "Close H3 Studio?")) return;
  stopPolling();
  try { await api("/api/quit", { method: "POST" }); } catch {}
  document.body.innerHTML = `<div class="goodbye">
      <h1>H3 Studio closed</h1>
      <p>${live ? "The GPU was shut down, so you are no longer being charged. " : ""}
         You can close this tab. Double-click the shortcut to start again.</p>
    </div>`;
});

// Delegated clicks
document.addEventListener("click", async (ev) => {
  const t = ev.target;
  if (t.dataset.ref !== undefined) {
    refImages.splice(+t.dataset.ref, 1); renderTiles(); updateEstimate(); return;
  }
  if (t.dataset.again) { useAgain(t.dataset.again); return; }
  if (t.dataset.cancel) {
    await api(`/api/jobs/${t.dataset.cancel}/cancel`, { method: "POST" });
    refreshFeed(); return;
  }
  if (t.dataset.del) {
    try {
      await api(`/api/jobs/${t.dataset.del}`, { method: "DELETE" });
      refreshFeed(); refreshStatus();
    } catch (e) { show("error", e.message); }
  }
});

// ─────────────────── poll ───────────────────

refreshStatus();
refreshFeed();
const timers = [setInterval(refreshStatus, 2000), setInterval(refreshFeed, 2500)];
function stopPolling() { timers.forEach(clearInterval); }
