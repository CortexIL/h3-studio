// Dumb client. All logic lives server-side so this file stays the same when the
// app moves from localhost to a shared team instance.

const $ = (id) => document.getElementById(id);
const api = async (path, opts = {}) => {
  const r = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    ...opts,
  });
  if (!r.ok) {
    let detail = r.statusText;
    try { detail = (await r.json()).detail || detail; } catch {}
    throw new Error(detail);
  }
  return r.json();
};

let refImages = [];
let cfg = null;

const POD_LABEL = {
  off: "Off",
  booting: "Starting…",
  ready: "Ready",
  stopping: "Stopping…",
  error: "Error",
};

const STATUS_LABEL = {
  queued: "Queued",
  running: "Generating",
  done: "Ready",
  failed: "Failed",
  cancelled: "Cancelled",
};

function fmtDuration(s) {
  s = Math.round(s);
  if (s < 60) return `${s}s`;
  const m = Math.floor(s / 60);
  if (m < 60) return `${m}m ${s % 60}s`;
  return `${Math.floor(m / 60)}h ${m % 60}m`;
}

// ---------- status ----------

async function refreshStatus() {
  let s;
  try {
    s = await api("/api/status");
  } catch {
    return;
  }
  cfg = s.config;

  $("mockBadge").hidden = !cfg.mock;
  // The panel never disappears entirely: with a key set it collapses to one line
  // with a Change button, because rotating a key is a thing people actually do -
  // hiding it outright left no way to replace a compromised one.
  const hasKey = !!s.has_api_key;
  const editing = $("setupPanel").dataset.editing === "1";
  $("setupPanel").hidden = false;
  $("keyState").hidden = !hasKey || editing;
  $("keyHint").textContent = s.api_key_hint || "";
  for (const id of ["setupTitle", "setupHelp", "keyRow", "keyNote"]) {
    $(id).hidden = hasKey && !editing;
  }
  $("setupPanel").classList.toggle("compact", hasKey && !editing);

  const pod = s.pod;
  $("podDot").className = `dot dot-${pod.state}`;
  $("podState").textContent = POD_LABEL[pod.state] || pod.state;
  $("podDetail").textContent = pod.detail || "";
  $("podGpu").textContent = pod.gpu ? `${pod.gpu} · $${pod.rate_per_hour.toFixed(2)}/hr` : "";

  const sess = s.session;
  if (sess.seconds > 0) {
    const el = $("podCost");
    el.textContent = `$${sess.cost_usd.toFixed(3)} · ${fmtDuration(sess.seconds)}`;
    el.classList.toggle("warn", sess.cost_usd >= sess.warn_usd);
  } else {
    $("podCost").textContent = "";
  }

  document.querySelectorAll(".pol").forEach((b) =>
    b.classList.toggle("active", b.dataset.policy === s.policy)
  );

  const c = s.counts;
  $("counts").innerHTML =
    `<span>Queued <b>${c.queued}</b></span>` +
    `<span>Running <b>${c.running}</b></span>` +
    `<span>Done <b>${c.done}</b></span>` +
    (c.failed ? `<span>Failed <b>${c.failed}</b></span>` : "");

  show("notice", s.notice, { fromServer: true });
  show("error", s.error);

  if (!$("folder").matches(":focus")) $("folder").value = s.output_folder || "";
  $("inbox").value = s.inbox_folder || "";

  // Images dropped into the inbox folder by another app show up as references.
  if (s.inbox_new && s.inbox_new.length) {
    for (const f of s.inbox_new) {
      if (!refImages.some((r) => r.path === f.path)) {
        refImages.push({ path: f.path, name: f.name, from: "inbox" });
      }
    }
    renderRefs();
    show("notice", `Picked up ${s.inbox_new.length} image(s) from the inbox folder`);
  }

  // A prompt/batch file dropped into the inbox is queued straight away.
  for (const b of s.inbox_batches || []) {
    if (b.error) {
      show("error", `Could not read ${b.source}: ${b.error}`);
      continue;
    }
    const miss = b.missing_images?.length
      ? ` (${b.missing_images.length} image(s) never arrived: ${b.missing_images.join(", ")})`
      : "";
    show("notice", `Queued ${b.queued} job(s) from ${b.source}${miss}`);
    refreshJobs();
  }

  if (!$("preset").options.length && cfg.presets) {
    const names = { draft: "Draft (cheap)", final: "Final (quality)" };
    $("preset").innerHTML = Object.entries(cfg.presets)
      .map(([k, v]) =>
        `<option value="${k}"${k === cfg.default_preset ? " selected" : ""}>` +
        `${names[k] || k} — ${v.width}×${v.height}, ${v.steps} steps</option>`
      ).join("");
    $("seconds").value = cfg.default_seconds;
    updateEstimate();
  }
}

// Notices come from two places: the server (in the status poll) and this file, in
// response to a click. Without this, a poll two seconds later wipes anything shown
// locally - so "Added 8 jobs" vanished before it could be read.
const NOTICE_HOLD_MS = 5000;
let localNoticeUntil = 0;

function show(id, text, opts = {}) {
  const el = $(id);
  if (id === "notice") {
    if (opts.fromServer) {
      if (Date.now() < localNoticeUntil) return;   // local message still has the floor
    } else if (text) {
      localNoticeUntil = Date.now() + NOTICE_HOLD_MS;
    }
  }
  el.hidden = !text;
  el.textContent = text || "";
}

// ---------- jobs ----------

async function refreshJobs() {
  let jobs;
  try {
    ({ jobs } = await api("/api/jobs"));
  } catch { return; }

  const box = $("jobs");
  if (!jobs.length) {
    box.innerHTML = `<div class="empty">Queue is empty. Add prompts above.</div>`;
    return;
  }

  box.innerHTML = jobs.map((j) => {
    const sub = j.status === "failed"
      ? `<div class="sub err">${esc(j.error || "failed")}</div>`
      : `<div class="sub">${STATUS_LABEL[j.status] || j.status} · ${j.seconds}s · ${j.preset}` +
        (j.output_path ? ` · ${esc(shortPath(j.output_path))}` : "") + `</div>`;

    const actions = [];
    if (j.status === "done") {
      actions.push(`<button class="ghost small" data-preview="${j.id}">View</button>`);
      actions.push(`<button class="ghost small" data-again="${j.id}">Run again</button>`);
    }
    if (j.status === "failed" || j.status === "cancelled") {
      actions.push(`<button class="ghost small" data-retry="${j.id}">Retry</button>`);
    }
    if (j.status === "queued" || j.status === "running") {
      actions.push(`<button class="ghost small" data-cancel="${j.id}">Cancel</button>`);
    }
    if (j.status !== "running") {
      actions.push(`<button class="ghost small" data-edit="${j.id}">Edit</button>`);
    }

    const refCount = (j.ref_images || []).length;
    const refTag = refCount
      ? `<span class="reftag" title="${refCount} reference image(s)">${refCount} img</span>`
      : "";

    return `<div class="job ${j.status}" data-open="${j.id}">
      <div class="bar"></div>
      <div class="txt">
        <div class="prompt">${refTag}${esc(j.prompt)}</div>
        ${sub}
      </div>
      <div class="actions">${actions.join("")}</div>
    </div>`;
  }).join("");
}

const esc = (s) => String(s ?? "").replace(/[&<>"]/g, (c) =>
  ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));

const shortPath = (p) => p.split(/[\\/]/).pop();

// ---------- estimate ----------

let estTimer = null;
function updateEstimate() {
  clearTimeout(estTimer);
  estTimer = setTimeout(async () => {
    const body = payload();
    if (!body.prompts.trim()) { $("estimate").textContent = ""; return; }
    try {
      const e = await api("/api/estimate", { method: "POST", body: JSON.stringify(body) });
      const tag = e.confidence === "estimated"
        ? `<span class="tag">— estimated, not measured. Run calibration for a real number.</span>`
        : `<span class="tag ok">— measured on your account</span>`;
      $("estimate").innerHTML =
        `<b>${e.clips}</b> clips · about <b>${fmtDuration(e.total_minutes * 60)}</b> · ` +
        `<b>$${e.cost_usd.toFixed(2)}</b> ` +
        `<span style="opacity:.7">($${e.cost_per_clip_usd.toFixed(3)} per clip)</span> ${tag}`;
    } catch {
      $("estimate").textContent = "";
    }
  }, 350);
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

// ---------- reference images ----------

const BATCH_EXT = /\.(zip|json|txt)$/i;

async function uploadFiles(files) {
  let added = 0;
  let batches = 0;
  let rejected = [];

  for (const file of files) {
    const isImage = file.type.startsWith("image/");
    const isBatch = BATCH_EXT.test(file.name || "");

    // Zips and batch files go through the watched folder, not straight into the
    // reference list: that way a file dragged onto the window and a file saved into
    // the folder by another app take exactly the same path in.
    if (isBatch) {
      const fd = new FormData();
      fd.append("file", file, file.name);
      try {
        await fetch("/api/inbox/upload", { method: "POST", body: fd })
          .then(async (x) => { if (!x.ok) throw new Error((await x.json()).detail); });
        batches++;
      } catch (e) {
        show("error", e.message || `Could not accept ${file.name}`);
      }
      continue;
    }

    if (!isImage) { rejected.push(file.name || "file"); continue; }

    const fd = new FormData();
    fd.append("file", file, file.name || "pasted.png");
    try {
      const r = await fetch("/api/upload", { method: "POST", body: fd }).then((x) => x.json());
      refImages.push({ path: r.path, name: file.name || r.name });
      added++;
    } catch {
      show("error", "Upload failed");
    }
  }

  if (batches) {
    show("notice", `Reading ${batches} batch file(s) — jobs appear in a moment`);
  }
  if (rejected.length) {
    show("error", `Not usable: ${rejected.join(", ")}. Drop images, a .zip, or a ` +
                  `.json/.txt batch file.`);
  }
  if (added) {
    renderRefs();
    // Switching to text-to-video with references attached is almost always a mistake.
    if ($("mode").value === "t2v") {
      $("mode").value = "i2v";
      show("notice", `${added} reference image(s) added — mode switched to image → video`);
    }
    updateEstimate();
  }
  return added;
}

function renderRefs() {
  $("refList").innerHTML = refImages.map((r, i) =>
    `<span class="chip${r.from === "inbox" ? " chip-inbox" : ""}">` +
    `${esc(r.name)}<button data-ref="${i}" title="Remove">×</button></span>`
  ).join("");
}

$("refInput").addEventListener("change", async (ev) => {
  await uploadFiles(ev.target.files);
  ev.target.value = "";
});

// Drag & drop anywhere on the compose panel.
const panel = $("composePanel");
let dragDepth = 0;   // counter, because dragenter/leave fire on every child element

["dragenter", "dragover"].forEach((evt) =>
  panel.addEventListener(evt, (e) => {
    if (!e.dataTransfer?.types?.includes("Files")) return;
    e.preventDefault();
    if (evt === "dragenter") dragDepth++;
    panel.classList.add("dragging");
  })
);

panel.addEventListener("dragleave", () => {
  if (--dragDepth <= 0) { dragDepth = 0; panel.classList.remove("dragging"); }
});

panel.addEventListener("drop", async (e) => {
  e.preventDefault();
  dragDepth = 0;
  panel.classList.remove("dragging");
  await uploadFiles(e.dataTransfer.files);
});

// Paste an image straight from the clipboard.
document.addEventListener("paste", async (e) => {
  const files = [...(e.clipboardData?.files || [])];
  if (files.length) {
    e.preventDefault();
    await uploadFiles(files);
  }
});


// ---------- job editor ----------

let editing = null;   // the job currently open in the modal

async function openEditor(id) {
  let job;
  try {
    job = await api(`/api/jobs/${id}`);
  } catch (e) {
    show("error", e.message);
    return;
  }
  editing = job;

  $("edTitle").textContent = job.status === "running" ? "Job (generating)" : "Edit job";
  $("edStatus").textContent = STATUS_LABEL[job.status] || job.status;
  $("edStatus").className = `edstatus st-${job.status}`;
  $("edPrompt").value = job.prompt || "";
  $("edSeconds").value = job.seconds;
  $("edMode").value = job.mode || "t2v";

  if (cfg?.presets) {
    const names = { draft: "Draft (cheap)", final: "Final (quality)" };
    $("edPreset").innerHTML = Object.entries(cfg.presets)
      .map(([k, v]) => `<option value="${k}"${k === job.preset ? " selected" : ""}>` +
                       `${names[k] || k} — ${v.width}×${v.height}</option>`).join("");
  }

  editing.ref_names = job.ref_names || [];
  renderThumbs();

  const out = $("edOutput");
  if (job.output_path) {
    out.hidden = false;
    out.innerHTML = `<video controls preload="metadata" src="/api/preview/${job.id}"></video>` +
                    `<div class="hint">${esc(shortPath(job.output_path))}</div>`;
  } else {
    out.hidden = true;
    out.innerHTML = "";
  }

  const willRequeue = ["done", "failed", "cancelled"].includes(job.status);
  $("edHint").textContent = job.status === "running"
    ? "This job is on the GPU right now. Cancel it first if you want to change it."
    : willRequeue
      ? "Saving puts this job back in the queue so your changes actually run."
      : "Saving updates the job in the queue.";
  $("edSave").disabled = job.status === "running";

  $("editor").hidden = false;
  $("edPrompt").focus();
}

function renderThumbs() {
  const names = editing?.ref_names || [];
  $("edThumbs").innerHTML = names.length
    ? names.map((n, i) => `
        <div class="thumb">
          <img src="/api/image/${encodeURIComponent(n)}" alt="${esc(n)}" loading="lazy">
          <button data-delref="${i}" title="Remove">×</button>
          <span>${esc(n)}</span>
        </div>`).join("")
    : `<div class="nothumbs">No reference images. Text-to-video uses the prompt alone.</div>`;
}

function closeEditor() {
  $("editor").hidden = true;
  editing = null;
}

$("edClose").addEventListener("click", closeEditor);
$("edCancel").addEventListener("click", closeEditor);
$("editor").addEventListener("click", (e) => { if (e.target.id === "editor") closeEditor(); });
document.addEventListener("keydown", (e) => {
  if (e.key === "Escape" && !$("editor").hidden) closeEditor();
});

$("edAddImg").addEventListener("change", async (ev) => {
  for (const file of ev.target.files) {
    if (!file.type.startsWith("image/")) continue;
    const fd = new FormData();
    fd.append("file", file, file.name || "ref.png");
    try {
      const r = await fetch("/api/upload", { method: "POST", body: fd }).then((x) => x.json());
      editing.ref_names.push(r.name);
    } catch { show("error", "Upload failed"); }
  }
  ev.target.value = "";
  // An image on a text-to-video job would be silently ignored by the workflow.
  if (editing.ref_names.length && $("edMode").value === "t2v") $("edMode").value = "i2v";
  renderThumbs();
});

$("edSave").addEventListener("click", async () => {
  if (!editing) return;
  const btn = $("edSave");
  btn.disabled = true;
  try {
    const r = await api(`/api/jobs/${editing.id}`, {
      method: "PATCH",
      body: JSON.stringify({
        prompt: $("edPrompt").value,
        seconds: +$("edSeconds").value || undefined,
        preset: $("edPreset").value || undefined,
        mode: $("edMode").value,
        ref_images: editing.ref_names,
      }),
    });
    show("notice", r.requeued ? "Saved — back in the queue" : "Saved");
    closeEditor();
    refreshJobs();
    refreshStatus();
  } catch (e) {
    show("error", e.message);
  } finally {
    btn.disabled = false;
  }
});

// ---------- wiring ----------

document.querySelectorAll(".pol").forEach((btn) =>
  btn.addEventListener("click", async () => {
    try {
      await api("/api/policy", {
        method: "POST",
        body: JSON.stringify({ policy: btn.dataset.policy }),
      });
      refreshStatus();
    } catch (e) { show("error", e.message); }
  })
);

$("submit").addEventListener("click", async () => {
  const btn = $("submit");
  btn.disabled = true;
  try {
    const r = await api("/api/jobs", { method: "POST", body: JSON.stringify(payload()) });
    $("prompts").value = "";
    $("estimate").textContent = "";
    show("notice", `Added ${r.count} job(s) to the queue`);
    refreshJobs();
    refreshStatus();
  } catch (e) {
    show("error", e.message);
  } finally {
    btn.disabled = false;
  }
});

["prompts", "seconds", "preset", "count"].forEach((id) =>
  $(id).addEventListener("input", updateEstimate)
);

$("changeKey").addEventListener("click", () => {
  $("setupPanel").dataset.editing = "1";
  refreshStatus().then(() => $("apiKey").focus());
});

$("saveKey").addEventListener("click", async () => {
  const btn = $("saveKey");
  const key = $("apiKey").value.trim();
  if (!key) { show("error", "Paste your RunPod API key first"); return; }
  btn.disabled = true;
  btn.textContent = "Verifying…";
  try {
    const r = await api("/api/runpod-key", {
      method: "POST",
      body: JSON.stringify({ key }),
    });
    $("apiKey").value = "";
    $("setupPanel").dataset.editing = "";
    show("notice", `Key ending ${r.hint} verified and saved. ${r.note}`);
    refreshStatus();
  } catch (e) {
    show("error", e.message);
  } finally {
    btn.disabled = false;
    btn.textContent = "Verify & save";
  }
});

$("saveFolder").addEventListener("click", async () => {
  try {
    await api("/api/folder", {
      method: "POST",
      body: JSON.stringify({ folder: $("folder").value }),
    });
    show("notice", "Output folder updated");
  } catch (e) { show("error", e.message); }
});

$("openFolder").addEventListener("click", () =>
  api("/api/folder/open", { method: "POST" }).catch((e) => show("error", e.message))
);

$("openInbox").addEventListener("click", () =>
  api("/api/inbox/open", { method: "POST" }).catch((e) => show("error", e.message))
);

$("quit").addEventListener("click", async () => {
  const live = cfg && !cfg.mock;
  const msg = live
    ? "Close H3 Studio and shut down the GPU?\n\nRunning jobs will stop and return to the queue."
    : "Close H3 Studio?";
  if (!confirm(msg)) return;

  // Stop polling first: once the server is gone every tick would just throw.
  stopPolling();
  // The server stops mid-request, so a network error here is the success case.
  try { await api("/api/quit", { method: "POST" }); } catch {}

  document.body.innerHTML =
    `<div class="goodbye">
       <h1>H3 Studio closed</h1>
       <p>${live ? "The GPU was shut down, so you are no longer being charged. " : ""}
          You can close this tab. To start again, double-click the shortcut.</p>
     </div>`;
});

$("againAll").addEventListener("click", async () => {
  const done = +($("counts").querySelector("span:nth-child(3) b")?.textContent || 0);
  if (!done) { show("error", "Nothing finished to re-run"); return; }
  if (!confirm(`Queue a fresh take of all ${done} finished job(s)?`)) return;
  const r = await api("/api/jobs/again-all", {
    method: "POST",
    body: JSON.stringify({ status: "done" }),
  });
  show("notice", `Queued ${r.queued} job(s) again`);
  refreshJobs();
  refreshStatus();
});

$("clearDone").addEventListener("click", async () => {
  await api("/api/jobs/clear-finished", { method: "POST" });
  refreshJobs();
  refreshStatus();
});

document.addEventListener("click", async (ev) => {
  const t = ev.target;
  if (t.dataset.ref !== undefined) {
    refImages.splice(+t.dataset.ref, 1);
    renderRefs();
  } else if (t.dataset.again) {
    await api(`/api/jobs/${t.dataset.again}/again`, { method: "POST" });
    show("notice", "Queued another take");
    refreshJobs();
  } else if (t.dataset.retry) {
    await api(`/api/jobs/${t.dataset.retry}/retry`, { method: "POST" });
    refreshJobs();
  } else if (t.dataset.cancel) {
    await api(`/api/jobs/${t.dataset.cancel}/cancel`, { method: "POST" });
    refreshJobs();
  } else if (t.dataset.preview) {
    window.open(`/api/preview/${t.dataset.preview}`, "_blank");
  } else if (t.dataset.delref !== undefined) {
    editing.ref_names.splice(+t.dataset.delref, 1);
    renderThumbs();
  } else if (t.dataset.edit) {
    openEditor(t.dataset.edit);
  } else {
    // Clicking anywhere on the row opens it too - the Edit button is a hint, not
    // the only way in.
    const row = t.closest?.("[data-open]");
    if (row && !t.closest(".actions")) openEditor(row.dataset.open);
  }
});

// ---------- poll ----------

refreshStatus();
refreshJobs();
const timers = [setInterval(refreshStatus, 2000), setInterval(refreshJobs, 2500)];
function stopPolling() { timers.forEach(clearInterval); }
