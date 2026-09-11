// Dumb client. Every decision is made server-side; this file renders what the API
// says and sends back what the user typed.
import { api, mountHeader, upload } from "/static/auth.js";

const $ = (id) => document.getElementById(id);

let cfg = null;
let refImages = [];        // references staged for the next submit: {key, name}
let jobsCache = [];
let filter = "all";
let formDefaults = null;   // set once the server reports them
let splitMode = "single";

const POD_LABEL = { off: "Off", booting: "Starting", ready: "Ready",
                    stopping: "Stopping", error: "Error" };
const STATUS_LABEL = { queued: "Queued", running: "Generating", done: "Ready",
                       failed: "Failed", cancelled: "Cancelled" };
const MODE_LABEL = { t2v: "text → video", i2v: "image → video",
                     r2v: "reference → video" };

const esc = (s) => String(s ?? "").replace(/[&<>"]/g, (c) =>
  ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
const fileOf = (p) => String(p || "").split("/").pop();
const imgUrl = (key) => `/api/image/${key.split("/").map(encodeURIComponent).join("/")}`;

function fmtDur(s) {
  s = Math.round(s);
  if (s < 60) return `${s}s`;
  const m = Math.floor(s / 60);
  return m < 60 ? `${m}m ${s % 60}s` : `${Math.floor(m / 60)}h ${m % 60}m`;
}

// Notices come from two places - the server, and this file reacting to a click.
// Without a hold, the next 2-second poll wipes a local message before it is read.
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

let queueDepth = 0;

async function refreshStatus() {
  let s;
  try { s = await api("/api/status"); } catch { return; }
  cfg = s.config;

  $("mockBadge").hidden = !cfg.mock;

  const pod = s.pod;
  $("podDot").className = `dot dot-${pod.state}`;
  $("podState").textContent = POD_LABEL[pod.state] || pod.state;
  $("podDetail").textContent = pod.detail || "";

  const c = s.counts;
  queueDepth = s.queue.total_queued;
  $("counts").innerHTML =
    `<span>Queued <b>${c.queued}</b></span><span>Running <b>${c.running}</b></span>` +
    `<span>Ready <b>${c.done}</b></span>` +
    (c.failed ? `<span>Failed <b>${c.failed}</b></span>` : "");

  show("notice", s.notice, { fromServer: true });

  // Defaults come from the server, not the markup, so the form and the API always
  // start from the same place.
  if (!$("preset").options.length && cfg.presets) {
    const names = { draft: "Draft", final: "Final", turbo: "Turbo" };
    $("preset").innerHTML = Object.entries(cfg.presets).map(([k, v]) =>
      `<option value="${esc(k)}"${k === cfg.default_preset ? " selected" : ""}>` +
      `${esc(names[k] || k)} ${v.width}×${v.height}</option>`).join("");
    $("seconds").value = cfg.default_seconds;
    if (cfg.default_mode) $("mode").value = cfg.default_mode;
    formDefaults = { preset: cfg.default_preset, mode: cfg.default_mode,
                     seconds: cfg.default_seconds };
    updateEstimate();
  }
}

// ─────────────────── feed ───────────────────

function stage(j) {
  if (j.status === "done" && j.video_url) {
    return `<video src="/api/video/${j.id}" controls preload="metadata"
                   playsinline></video>`;
  }
  const bg = j.ref_images?.length
    ? `<img src="${imgUrl(j.ref_images[0])}" alt="">` : "";
  const label = j.status === "running" ? "Generating…"
    : j.status === "failed" ? "Failed"
    : j.status === "cancelled" ? "Cancelled" : "Waiting in the queue";
  // A queue depth, never another user's prompt. Without it, a job waiting through
  // a five-minute cold boot is indistinguishable from a broken one.
  const waiting = queueDepth > 1
    ? `${queueDepth} clips in the shared queue · nothing is charged until it starts`
    : "nothing is charged until it starts";
  const sub = j.status === "running" ? "the GPU is on this one now"
    : j.status === "failed" ? esc(j.error || "")
    : j.status === "cancelled" ? "" : waiting;
  return `${bg}<div class="waiting"><b>${label}</b><span>${sub}</span></div>`;
}

function entryHtml(j) {
  const chips = [
    `<span class="metachip"><span class="stat ${j.status}"></span>${STATUS_LABEL[j.status] || esc(j.status)}</span>`,
    `<span class="metachip">${j.seconds}s</span>`,
    `<span class="metachip">${esc(j.preset)}</span>`,
    `<span class="metachip">${MODE_LABEL[j.mode] || esc(j.mode)}</span>`,
  ].join("");

  const refs = (j.ref_images || []).map((k) =>
    `<img src="${imgUrl(k)}" alt="" loading="lazy" title="${esc(fileOf(k))}">`).join("");

  const canCancel = j.status === "queued" || j.status === "running";
  const actions = [
    `<button class="btn" data-again="${j.id}" title="Copy this back into the form on the left">Use again</button>`,
    j.status === "done" && j.video_url
      ? `<a class="btn" href="/api/video/${j.id}?download=1" download>Download</a>` : "",
    canCancel ? `<button class="btn danger-ghost" data-cancel="${j.id}">Cancel</button>` : "",
    j.status !== "running" ? `<button class="icon-btn" data-del="${j.id}" title="Remove from the list (keeps the clip)">🗑</button>` : "",
  ].join("");

  return `<article class="entry ${j.status === "done" ? "" : "pending"}" data-entry="${j.id}">
    <div class="stage">${stage(j)}</div>
    <div class="entry-side">
      <div class="entry-prompt">${esc(j.prompt)}</div>
      ${refs ? `<div class="entry-refs">${refs}</div>` : ""}
      <div class="metachips">${chips}</div>
      <div class="entry-actions">${actions}</div>
    </div>
  </article>`;
}

function visible(jobs) {
  if (filter === "pending") {
    return jobs.filter((j) => ["queued", "running"].includes(j.status));
  }
  if (filter === "done") return jobs.filter((j) => j.status === "done");
  return jobs;
}

async function refreshFeed() {
  let jobs;
  try { ({ jobs } = await api("/api/jobs")); } catch { return; }

  // Re-rendering every 2.5s would restart any video the user is watching, so only
  // redraw when something actually changed.
  const sig = JSON.stringify(jobs.map((j) => [j.id, j.status, j.video_url, j.prompt]));
  if (sig === refreshFeed.sig && filter === refreshFeed.filter) {
    jobsCache = jobs;
    return;
  }
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

function useAgain(id) {
  const j = jobsCache.find((x) => x.id === id);
  if (!j) return;

  $("prompts").value = j.prompt || "";
  $("seconds").value = j.seconds;
  $("mode").value = j.mode || "i2v";
  $("count").value = 1;
  if ([...$("preset").options].some((o) => o.value === j.preset)) {
    $("preset").value = j.preset;
  }

  // Reference images come across as staged uploads, so the copy is a complete,
  // editable starting point rather than a half-filled form.
  refImages = (j.ref_images || []).map((k) => ({ key: k, name: fileOf(k) }));
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
  let added = 0, queued = 0;
  const rejected = [];
  const missing = [];

  for (const file of files) {
    if (BATCH_EXT.test(file.name || "")) {
      try {
        const r = await upload("/api/inbox/upload", file);
        queued += r.queued;
        missing.push(...(r.missing_images || []));
      } catch (e) {
        show("error", e.message || `Could not accept ${file.name}`);
      }
      continue;
    }
    if (!file.type.startsWith("image/")) { rejected.push(file.name || "file"); continue; }
    try {
      const r = await upload("/api/upload", file, file.name || "pasted.png");
      refImages.push({ key: r.key, name: file.name || r.name });
      added++;
    } catch (e) { show("error", e.message || "Upload failed"); }
  }

  if (queued) {
    const miss = missing.length ? ` (${missing.length} image(s) were not in the file)` : "";
    show("notice", `Queued ${queued} job(s)${miss}`);
    refreshFeed(); refreshStatus();
  }
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
    el.className = "tile";
    el.title = r.name;
    const img = document.createElement("img");
    img.src = imgUrl(r.key);
    img.alt = "";
    const x = document.createElement("button");
    x.className = "x";
    x.dataset.ref = String(i);
    x.title = "Remove";
    x.textContent = "×";
    el.append(img, x);
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
    ref_images: refImages.map((r) => r.key),
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
      const e = await api("/api/estimate", { method: "POST",
                                             body: JSON.stringify(body) });
      const tag = e.confidence === "estimated"
        ? `<span class="tag">Estimated, not measured.</span>`
        : `<span class="tag ok">Measured on this account.</span>`;
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
if (savedW >= MIN_W && savedW <= MAX_W) {
  layout.style.setProperty("--left-w", `${savedW}px`);
}

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

document.querySelectorAll(".spl").forEach((btn) =>
  btn.addEventListener("click", () => {
    splitMode = btn.dataset.split;
    document.querySelectorAll(".spl").forEach((b) =>
      b.classList.toggle("active", b === btn));
    updateEstimate();
  }));

document.querySelectorAll(".flt").forEach((btn) =>
  btn.addEventListener("click", () => {
    filter = btn.dataset.filter;
    document.querySelectorAll(".flt").forEach((b) =>
      b.classList.toggle("active", b === btn));
    refreshFeed();
  }));

$("submit").addEventListener("click", async () => {
  const btn = $("submit");
  btn.disabled = true;
  try {
    const r = await api("/api/jobs", { method: "POST",
                                       body: JSON.stringify(payload()) });
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

// Delegated clicks
document.addEventListener("click", async (ev) => {
  const t = ev.target;
  if (t.dataset.ref !== undefined) {
    refImages.splice(+t.dataset.ref, 1); renderTiles(); updateEstimate(); return;
  }
  if (t.dataset.again) { useAgain(t.dataset.again); return; }
  if (t.dataset.cancel) {
    try {
      await api(`/api/jobs/${t.dataset.cancel}/cancel`, { method: "POST" });
      refreshFeed();
    } catch (e) { show("error", e.message); }
    return;
  }
  if (t.dataset.del) {
    try {
      await api(`/api/jobs/${t.dataset.del}`, { method: "DELETE" });
      refreshFeed(); refreshStatus();
    } catch (e) { show("error", e.message); }
  }
});

// ─────────────────── poll ───────────────────

mountHeader().catch(() => { /* the wrapper has already sent us to /login */ });
refreshStatus();
refreshFeed();
setInterval(refreshStatus, 2000);
setInterval(refreshFeed, 2500);
