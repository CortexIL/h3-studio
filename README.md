# H3 Studio

A desktop app for running **MiniMax H3** on a rented GPU. Write prompts, pick a
folder, and finished videos land there on their own. The GPU starts when there is
work and shuts down when there isn't.

---

## How it works

```
Your PC                                    RunPod
┌──────────────────────────┐              ┌────────────────────────┐
│ orchestrator (FastAPI)   │  ① start pod │  ComfyUI :8188         │
│  ├─ SQLite queue         │─────────────▶│  + H3 weights          │
│  ├─ pod control          │  ② POST      │    (~40GB, fetched     │
│  ├─ ComfyUI client       │────/prompt──▶│     each session)      │
│  └─ OutputSink           │  ③ GET /view │                        │
│         ↓                │◀─────────────│                        │
│  D:\Videos\out\*.mp4     │  ④ terminate │                        │
└──────────────────────────┘─────────────▶└────────────────────────┘
         ▲
         │ localhost:8777
   web page in browser
```

The page is a **dumb client** — all logic and every secret stay server-side. That is
why turning this into a shared site for five people means moving the same process to
a VPS rather than rewriting it.

---

## Running it

**Double-click `H3 Studio` on the desktop.** That's it.

On first run it builds the Python environment and installs dependencies by itself
(a minute or two, once). Then it starts the server and opens your browser.

- **No RunPod key?** It runs in demo mode — real MP4s via ffmpeg, no GPU rented, no
  charges. The whole pipeline can be tested for free.
- **Double-clicked twice?** It won't error. It just opens the window you already have.
- **Port taken?** It moves to the next free one.
- **To stop:** the **Quit** button in the corner of the UI. It stops the app, and in
  live mode shuts down the GPU so you stop paying. (Closing the console window does
  the same.)

To go live you need a RunPod key. **Paste it into the "Connect to RunPod" panel at
the top of the app** — it is checked against RunPod before being saved, so a typo is
caught immediately rather than surfacing later as a failed pod start. It is written to
`config.yaml`, which is gitignored; the key is never sent back to the browser and
never enters git. (Editing `runpod.api_key` in `config.yaml` by hand works too.)

Getting one: [runpod.io](https://runpod.io) → add credit (minimum $10, non-refundable,
does not expire) → Settings → API Keys → Create API Key → permission **All**. Choose
All rather than Restricted: Restricted only covers Serverless endpoints, and this app
starts and stops Pods. RunPod shows the key once and keeps no copy of it.

Restart the app after saving a key — the amber demo badge disappearing is how you know
it took.

### From a terminal (optional)

```bash
.venv\Scripts\python -m app.launch --mock
```

---

## Before your first real batch — two steps that save money

### 1. Pre-flight check (free)

```bash
.venv\Scripts\python -m app.doctor
```

Checks the API key, GPU availability and prices, folder write permissions and budget
ceilings — everything that would otherwise blow up **after** you started paying.

### 2. Calibration (≈ $0.50)

```bash
.venv\Scripts\python -m app.calibrate
```

Starts one GPU, times a real boot and two real clips, writes the result into
`config.yaml`, and terminates the pod. After this every cost figure in the UI reads
"measured" instead of "estimated".

**This is the most important step in the project.** Every timing number here is
extrapolated from a single published benchmark (4×H100 rendering a 5-second clip in
13.25 seconds). If a 10-second clip actually takes 20 minutes rather than 4, you want
to find that out for 50 cents, not for 30 dollars.

### 3. Workflow templates

ComfyUI ships official H3 templates. Rather than guessing at the node graph, export
one once:

1. Start a pod (from the app, or by hand on RunPod)
2. Open ComfyUI → Templates → MiniMax H3 (T2V)
3. Workflow → **Export (API)**
4. Save as `app/workflows/h3_t2v.api.json`

The code injects prompt, size, steps, seed and length into the template **by
`class_type`, never by node id** — so re-exporting from a newer ComfyUI does not
break anything.

If something doesn't line up:

```bash
.venv\Scripts\python -m app.doctor --dump-nodes --endpoint https://<podid>-8188.proxy.runpod.net
```

That prints the real input names from a live ComfyUI. It is the definitive answer
instead of a guess.

---

## Going live

Once the key is in `config.yaml`, the same double-click no longer falls back to demo
mode. The amber badge in the UI disappears — that is your signal you are on a real GPU.

---

## Reference images — three ways in

| Way | How | Best for |
|---|---|---|
| **Drag** | Drop files anywhere on the prompts panel | Fastest |
| **Paste** | Ctrl+V with an image on the clipboard | Screenshots |
| **Inbox folder** | Save an image into `inbox/` | Another app sending them |

All three switch the mode from `text → video` to `image → video` automatically.
Attaching a reference and leaving it on text-only is almost always a mistake, and one
you would otherwise discover after paying for the render.

### The inbox folder

`inbox/` is scanned every couple of seconds. Anything that lands there is taken, and
the original moves to `inbox/_used/` so nothing is processed twice.

This is the lowest-common-denominator integration: **anything that can save a file**
can hand work to H3 Studio — ChatGPT, a screenshot tool, Explorer, a script. None of
them need to know this app exists.

It takes three kinds of file:

**Images** — attached as reference images.

**Batches** (`.txt` or `.json`) — prompts, queued immediately.

**Archives** (`.zip`) — unpacked into the inbox, then handled as above. This is the
one-drag path for an export from somewhere else. Archives are treated as hostile
input: entries are flattened to their basename so `../` and absolute paths cannot
escape the folder, only image and batch extensions are extracted, and entry-count,
size and compression-ratio caps stop a decompression bomb.

A `.txt` is one prompt per line; blank lines and `#` comments are ignored. A `.json`
carries the rest:

```json
{
  "defaults": { "seconds": 10, "preset": "draft" },
  "jobs": [
    { "prompt": "slow orbit around the product", "image": "product.png" },
    { "prompt": "wide establishing shot", "takes": 2, "seconds": 6 }
  ]
}
```

Only `prompt` is required. `image` must be a plain filename dropped into the same
folder — a batch cannot name a path elsewhere on the machine, because it is untrusted
input. Naming an image switches that job to `i2v` automatically. A batch whose images
have not all arrived waits up to 20 seconds for them, then runs with what it has and
reports the rest as missing.

Every field is validated and clamped (`seconds` 4–15, `takes` 1–10, known presets and
modes only) rather than trusted, and batch content is always treated as data, never as
instructions.

> Queued jobs still obey the current GPU policy. With policy **Off**, a dropped batch
> lands in the queue and starts nothing until you say so.

### Getting a batch out of ChatGPT

ChatGPT cannot write to your disk, so the flow is export → download → drop in. Ask
it for a zip containing `h3_batch.json` plus the images, then drop the zip itself into
`inbox/` — no need to extract it. The whole batch queues itself.

`docs/chatgpt-prompt.txt` is the request to paste into ChatGPT, and
`inbox/READ ME.txt` is the format reference that lives next to the folder.

---

## Claude Desktop (MCP)

Claude gets a richer path: an MCP server that lets it drive the app directly.

```bash
.venv\Scripts\python -m app.install_mcp
```

This backs up your Claude Desktop config, merges the registration in, and asks you to
restart. Use `--remove` to undo it.

The six tools exposed:

| Tool | What it does |
|---|---|
| `get_status` | Whether it's running, GPU state, queue, session cost so far |
| `estimate_cost` | Time and price forecast **before** committing |
| `queue_video` | Adds prompts to the queue |
| `add_reference_image` | Hands an image from this PC to the app |
| `list_jobs` | Job states plus output file paths |
| `set_gpu_policy` | auto / keep-warm / off |

The server's `instructions` tell Claude to **call `estimate_cost` and report the
figure to you before queueing a large batch**, because it spends your money.

Note that the server is a thin client over the same HTTP API the web page uses. It
holds no state and duplicates no logic, so Claude and the browser can never disagree
about what is queued or what it costs. `add_reference_image` even writes into
`inbox/` rather than uploading directly — one intake path for every route in.

**The app must be running.** If it isn't, every tool returns a plain message asking
you to start it rather than a technical error.

---

## Editing a job

Click any row in the queue — or its **Edit** button — to open it. You get the full
prompt in an editable box, thumbnails of its reference images with the option to add
or remove them, and its length, quality and mode.

What **Save** does depends on the job's state, because there is only one sensible
answer for each:

| State | Save does |
|---|---|
| Queued | Updates it in place; it runs with your changes |
| Done / failed / cancelled | Updates it **and puts it back in the queue** — the only reason to edit a finished job is to run it again |
| Running | Refused. It is already on the GPU, so a change could not take effect; cancel it first |

**Run again** on a finished job clones it as a fresh take, deliberately without
copying the seed — reusing the seed would reproduce the same clip rather than give
you a new roll. The original and its file stay on record so takes can be compared.
**Re-run finished** does that for every finished job at once.

---

## The power button is a policy, not a switch

| Mode | What happens | When to use it |
|---|---|---|
| **Auto** | Pod starts when there is a queue, dies 10 min after it empties | Default |
| **Keep warm** | Stays up even with no work | An iteration session, where a 5-minute boot annoys more than a few cents |
| **Off** | Shuts down immediately and starts nothing | You're done |

This is also why it is a policy rather than a switch: **five people cannot each hold
their own power switch over one shared pod.** The same code serves one user and five.

### Three safety nets against a surprise bill

1. **Idle shutdown** — `pod.idle_shutdown_minutes`
2. **Budget ceiling** — `budget.session_limit_usd` (default $8). When crossed, the pod
   dies and the policy flips to off
3. **Time ceiling** — `pod.max_session_hours`, regardless of everything else

On top of that, pod shutdown sits in the lifespan's `finally`, so Ctrl-C kills it too.

> If something slips through anyway, [console.runpod.io/pods](https://console.runpod.io/pods)
> is the only source of truth. `app.doctor` also warns about a pod running that you
> didn't ask for.

---

## How to pay less

| Lever | Saving | Where |
|---|---|---|
| **Draft before final** | Largest | `preset: draft` — 768×432 at 20 steps. Iterate cheap, render at final only what survived |
| **Fewer steps** | ~40% | The open checkpoints are CFG-distilled; 30 usually looks identical to 50 |
| **No network volume** | ~$10/month | The default. A persistent disk bills even while the pod is off |
| **One big batch** | ~$0.10/session | Boot overhead is paid once per session |
| **Spot** | ~30% | `interruptible: true`. The queue survives interruptions — a cut job returns to the queue |

The real money in video generation goes to clips you throw away, not to the final
render. That is why `draft` is the default.

---

## Code layout

```
app/
  main.py            HTTP server + API. The page is a dumb client of it
  launch.py          Double-click entry point
  orchestrator.py    The loop: policy -> pod -> queue -> collect -> shutdown
  db.py              SQLite queue (+ cost ledger). Every job has an owner from day one
  config.py          Config. Secrets stay server-side
  estimate.py        Cost maths. Uses a measurement when there is one, else an estimate
  doctor.py          Pre-flight checks (free)
  calibrate.py       Real measurement (≈$0.50)
  inbox.py           Watched folder for images from other apps
  mcp_server.py      MCP server for Claude Desktop (thin client over the HTTP API)
  install_mcp.py     Claude Desktop registration, with backup and merge
  backends/
    __init__.py      The Backend interface
    runpod_pod.py    Pod lifecycle + ComfyUI
    comfy.py         ComfyUI HTTP client
    mock.py          Fake backend for running free
  sinks/             Where videos go (a folder today, S3 tomorrow)
  workflows/         ComfyUI templates + value injection by class_type
web/                 One page, no logic
```

### Seams left open on purpose

- **`owner` on every job** — adding login is a screen, not a migration
- **`OutputSink`** — S3 instead of a folder is one file
- **`Backend`** — Vast.ai instead of RunPod is one class
- **Fair queueing** — deliberately not built. When five people use this it is ~30
  lines in `db.claim_next_queued` (round-robin on `owner` instead of FIFO)

---

## Worth knowing

- **The open weights produce 768p only.** 2K exists only on the managed API.
- **Licence.** The H3 community licence reportedly excludes local deployment in the
  US, EU, UK and South Korea. Choose `data_center_ids` accordingly, and read the
  licence yourself.
- **RunPod bills per second.** No hourly minimum — an 8-minute session costs 8 minutes.
- **H3 takes 4–15 seconds**, whole numbers only.
