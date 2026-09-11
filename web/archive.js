import { api, mountHeader } from "/static/auth.js";

const grid = document.getElementById("grid");
const more = document.getElementById("more");
const err = document.getElementById("error");
let cursor = null;
let loading = false;

const fmtBytes = (n) => (!n ? "" : `${(n / 1048576).toFixed(1)} MB`);
const fmtWhen = (s) => (s ? new Date(s * 1000).toLocaleString() : "");

function card(clip) {
  const fig = document.createElement("figure");
  fig.className = "clip";

  const video = document.createElement("video");
  video.src = clip.video_url;
  video.controls = true;
  video.playsInline = true;
  // Forty clips would otherwise open forty range requests against the app before
  // the user has clicked anything.
  video.preload = "none";

  const cap = document.createElement("figcaption");
  const prompt = document.createElement("p");
  prompt.className = "prompt";
  // Prompts are arbitrary text from someone's keyboard; never innerHTML.
  prompt.textContent = clip.prompt;

  const meta = document.createElement("span");
  meta.className = "meta";
  meta.textContent = [`${clip.seconds}s`, clip.preset, clip.mode,
                      fmtBytes(clip.bytes), fmtWhen(clip.finished_at)]
    .filter(Boolean).join(" · ");

  const dl = document.createElement("a");
  dl.className = "btn";
  dl.href = clip.video_url;
  dl.download = "";
  dl.textContent = "Download";

  cap.append(prompt, meta, dl);
  fig.append(video, cap);
  return fig;
}

async function load() {
  if (loading) return;
  loading = true;
  more.disabled = true;
  try {
    const q = cursor ? `?cursor=${encodeURIComponent(cursor)}` : "";
    const body = await api(`/api/archive${q}`);
    body.clips.forEach((c) => grid.append(card(c)));
    cursor = body.next_cursor;
    more.hidden = !cursor;
    document.getElementById("empty").hidden = grid.childElementCount > 0;
  } catch (e) {
    err.textContent = e.message;
    err.hidden = false;
  } finally {
    loading = false;
    more.disabled = false;
  }
}

more.addEventListener("click", load);
mountHeader().catch(() => { /* already redirected */ });
load();
