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
let refImages = [];        // pending references for the next submit
let selectedId = null;     // job open in the detail column
let detailJob = null;
let jobsCache = [];
let view = "list";

const POD_LABEL = { off: "Off", booting: "Starting", ready: "Ready",
                    stopping: "Stopping", error: "Error" };
const STATUS_LABEL = { queued: "Queued", running: "Generating", done: "Ready",
                       failed: "Failed", cancelled: "Cancelled" };
const PILL_CLASS = { done: "pill-ok", failed: "pill-err", running: "pill-run" };

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
    `<span>Done <b>${c.done}</b></span>` +
    (c.failed ? `<span>Failed <b>${c.failed}</b></span>` : "");

  show("notice", s.notice, { fromServer: true });
  show("error", s.error);

  // The key panel lives in the left column until a key exists, then only in settings.
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

  if (!$("preset").options.length && cfg.presets) {
    const names = { draft: "Draft", final: "Final" };
    $("preset").innerHTML = Object.entries(cfg.presets).map(([k, v]) =>
      `<option value="${k}"${k === cfg.default_preset ? " selected" : ""}>` +
      `${names[k] || k} ${v.width}×${v.height}</option>`).join("");
    $("seconds").value = cfg.default_seconds;
    updateEstimate();
  }
}

// ─────────────────── queue ───────────────────

function jobThumb(j) {
  if (j.status === "done" && j.output_path) {
    return `<video src="/api/preview/${j.id}#t=0.5" preload="metadata" muted></video>`;
  }
  if (j.ref_names?.length) {
    return `<img src="/api/image/${encodeURIComponent(j.ref_names[0])}" alt="" loading="lazy">`;
  }
  return `<span class="placeholder">▦</span>`;
}

async function refreshJobs() {
  let jobs;
  try { ({ jobs } = await api("/api/jobs")); } catch { return; }
  jobsCache = jobs;

  const box = $("jobs");
  box.className = `jobs ${view}`;
  if (!jobs.length) {
    box.innerHTML = `<div class="empty">Nothing queued yet. Write a prompt on the left.</div>`;
    return;
  }

  box.innerHTML = jobs.map((j) => {
    const meta = j.status === "failed"
      ? `<div class="meta err">${esc(j.error || "failed")}</div>`
      : `<div class="meta"><span class="stat ${j.status}"></span>` +
        `${STATUS_LABEL[j.status] || j.status} · ${j.seconds}s · ${j.preset}</div>`;
    return `<article class="job ${j.id === selectedId ? "selected" : ""}" data-job="${j.id}">
      <div class="thumb">${jobThumb(j)}</div>
      <div class="body">
        <div class="prompt">${esc(j.prompt)}</div>
        ${meta}
      </div>
    </article>`;
  }).join("");

  if (selectedId && !jobs.some((j) => j.id === selectedId)) closeDetail();
  else if (selectedId) refreshDetailStatus();
}

// ─────────────────── detail ───────────────────

function closeDetail() {
  selectedId = null;
  detailJob = null;
  $("detailFoot").hidden = true;
  $("dtStatus").hidden = true;
  document.querySelector(".col-detail").classList.add("idle");
  $("detailBody").innerHTML =
    `<div class="empty tall"><p>Select a job to see and edit it.</p></div>`;
}

async function openDetail(id) {
  let job;
  try { job = await api(`/api/jobs/${id}`); } catch (e) { show("error", e.message); return; }
  selectedId = id;
  detailJob = job;
  document.querySelector(".col-detail").classList.remove("idle");

  const presets = Object.entries(cfg?.presets || {}).map(([k, v]) =>
    `<option value="${k}"${k === job.preset ? " selected" : ""}>${k} ${v.width}×${v.height}</option>`
  ).join("");

  $("detailBody").innerHTML = `
    ${job.output_path ? `
      <video class="detail-video" controls preload="metadata"
             src="/api/preview/${job.id}"></video>
      <div class="detail-file">${esc(fileOf(job.output_path))}</div>` : ""}

    <div class="field">
      <label class="lbl">Prompt</label>
      <textarea id="dtPrompt"></textarea>
    </div>

    <div class="field">
      <label class="lbl">Reference images</label>
      <div id="dtTiles" class="tiles">
        <label class="tile tile-add" title="Add an image">
          <input type="file" id="dtAddImg" accept="image/*" multiple hidden>
          <span>+</span>
        </label>
      </div>
    </div>

    <div class="field">
      <label class="lbl">Settings</label>
      <div class="chips">
        <label class="chip"><span class="chip-ico">◷</span>
          <input type="number" id="dtSeconds" min="4" max="15" value="${job.seconds}">
          <span class="chip-unit">s</span></label>
        <label class="chip"><span class="chip-ico">◈</span>
          <select id="dtPreset">${presets}</select></label>
        <label class="chip wide"><span class="chip-ico">▷</span>
          <select id="dtMode">
            <option value="t2v"${job.mode === "t2v" ? " selected" : ""}>Text → video</option>
            <option value="i2v"${job.mode === "i2v" ? " selected" : ""}>Image → video</option>
            <option value="r2v"${job.mode === "r2v" ? " selected" : ""}>Reference → video</option>
          </select></label>
      </div>
    </div>

    <p class="hint tiny" id="dtHint"></p>`;

  $("dtPrompt").value = job.prompt || "";
  detailJob.ref_names = job.ref_names || [];
  renderDetailTiles();
  $("dtAddImg").addEventListener("change", onDetailAddImage);
  refreshDetailStatus();
  refreshJobs();
}

function refreshDetailStatus() {
  const j = jobsCache.find((x) => x.id === selectedId);
  if (!j || !detailJob) return;
  const pill = $("dtStatus");
  pill.hidden = false;
  pill.textContent = STATUS_LABEL[j.status] || j.status;
  pill.className = `pill ${PILL_CLASS[j.status] || ""}`;

  const running = j.status === "running";
  const finished = ["done", "failed", "cancelled"].includes(j.status);
  $("detailFoot").hidden = false;
  $("dtSave").disabled = running;
  $("dtAgain").hidden = !finished;
  $("dtCancel").hidden = finished;
  const hint = $("dtHint");
  if (hint) {
    hint.textContent = running
      ? "This job is on the GPU right now. Cancel it first to change anything."
      : finished
        ? "Saving puts it back in the queue so the changes actually run."
        : "Saving updates the job in the queue.";
  }
}

function renderDetailTiles() {
  const box = $("dtTiles");
  if (!box) return;
  const add = box.querySelector(".tile-add");
  box.innerHTML = "";
  (detailJob.ref_names || []).forEach((n, i) => {
    const el = document.createElement("div");
    el.className = "tile";
    el.innerHTML = `<img src="/api/image/${encodeURIComponent(n)}" alt="${esc(n)}" loading="lazy">
                    <button class="x" data-dtref="${i}" title="Remove">×</button>`;
    box.appendChild(el);
  });
  box.appendChild(add);
}

async function onDetailAddImage(ev) {
  for (const file of ev.target.files) {
    if (!file.type.startsWith("image/")) continue;
    const fd = new FormData();
    fd.append("file", file, file.name || "ref.png");
    try {
      const r = await fetch("/api/upload", { method: "POST", body: fd }).then((x) => x.json());
      detailJob.ref_names.push(r.name);
    } catch { show("error", "Upload failed"); }
  }
  ev.target.value = "";
  if (detailJob.ref_names.length && $("dtMode").value === "t2v") $("dtMode").value = "i2v";
  renderDetailTiles();
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

  if (batches) show("notice", `Reading ${batches} batch file(s) — jobs appear shortly`);
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

// ─────────────────── wiring ───────────────────

document.querySelectorAll(".pol").forEach((btn) =>
  btn.addEventListener("click", async () => {
    try {
      await api("/api/policy", { method: "POST",
        body: JSON.stringify({ policy: btn.dataset.policy }) });
      refreshStatus();
    } catch (e) { show("error", e.message); }
  }));

document.querySelectorAll(".view").forEach((btn) =>
  btn.addEventListener("click", () => {
    view = btn.dataset.view;
    document.querySelectorAll(".view").forEach((b) => b.classList.toggle("active", b === btn));
    refreshJobs();
  }));

$("submit").addEventListener("click", async () => {
  const btn = $("submit");
  btn.disabled = true;
  try {
    const r = await api("/api/jobs", { method: "POST", body: JSON.stringify(payload()) });
    $("prompts").value = "";
    $("estimate").textContent = "";
    $("submitCost").textContent = "";
    refImages = [];
    renderTiles();
    show("notice", `Added ${r.count} job(s)`);
    refreshJobs(); refreshStatus();
  } catch (e) { show("error", e.message); }
  finally { btn.disabled = false; }
});

["prompts", "seconds", "preset", "count"].forEach((id) =>
  $(id).addEventListener("input", updateEstimate));

$("refInput").addEventListener("change", async (ev) => {
  await uploadFiles(ev.target.files);
  ev.target.value = "";
});

// Drag & drop over the whole create column.
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

// Detail actions
$("dtSave").addEventListener("click", async () => {
  if (!detailJob) return;
  const btn = $("dtSave");
  btn.disabled = true;
  try {
    const r = await api(`/api/jobs/${detailJob.id}`, {
      method: "PATCH",
      body: JSON.stringify({
        prompt: $("dtPrompt").value,
        seconds: +$("dtSeconds").value || undefined,
        preset: $("dtPreset").value || undefined,
        mode: $("dtMode").value,
        ref_images: detailJob.ref_names,
      }),
    });
    show("notice", r.requeued ? "Saved — back in the queue" : "Saved");
    refreshJobs(); refreshStatus();
  } catch (e) { show("error", e.message); }
  finally { btn.disabled = false; }
});

$("dtAgain").addEventListener("click", async () => {
  if (!detailJob) return;
  await api(`/api/jobs/${detailJob.id}/again`, { method: "POST" });
  show("notice", "Queued another take");
  refreshJobs();
});

$("dtCancel").addEventListener("click", async () => {
  if (!detailJob) return;
  await api(`/api/jobs/${detailJob.id}/cancel`, { method: "POST" });
  refreshJobs();
});

$("againAll").addEventListener("click", async () => {
  const done = jobsCache.filter((j) => j.status === "done").length;
  if (!done) { show("error", "Nothing finished to re-run"); return; }
  if (!confirm(`Queue a fresh take of all ${done} finished job(s)?`)) return;
  const r = await api("/api/jobs/again-all", { method: "POST",
    body: JSON.stringify({ status: "done" }) });
  show("notice", `Queued ${r.queued} job(s) again`);
  refreshJobs(); refreshStatus();
});

$("clearDone").addEventListener("click", async () => {
  await api("/api/jobs/clear-finished", { method: "POST" });
  refreshJobs(); refreshStatus();
});

// Settings
const settings = $("settings");
$("settingsBtn").addEventListener("click", () => { settings.hidden = false; });
$("setClose").addEventListener("click", () => { settings.hidden = true; });
settings.addEventListener("click", (e) => { if (e.target === settings) settings.hidden = true; });
document.addEventListener("keydown", (e) => {
  if (e.key === "Escape") { if (!settings.hidden) settings.hidden = true; else closeDetail(); }
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

// Delegated clicks: job cards and the small × on reference tiles.
document.addEventListener("click", (ev) => {
  const t = ev.target;
  if (t.dataset.ref !== undefined) {
    refImages.splice(+t.dataset.ref, 1); renderTiles(); updateEstimate(); return;
  }
  if (t.dataset.dtref !== undefined) {
    detailJob.ref_names.splice(+t.dataset.dtref, 1); renderDetailTiles(); return;
  }
  const card = t.closest?.("[data-job]");
  if (card) openDetail(card.dataset.job);
});

// ─────────────────── poll ───────────────────

closeDetail();
refreshStatus();
refreshJobs();
const timers = [setInterval(refreshStatus, 2000), setInterval(refreshJobs, 2500)];
function stopPolling() { timers.forEach(clearInterval); }
