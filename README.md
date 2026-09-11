# H3 Studio

A shared web app for running **MiniMax H3** on a rented GPU. Sign in, write
prompts, and finished videos land in your own archive. The GPU starts when there
is work and shuts down when there isn't.

---

## How it works

```
  browsers                    h3-studio container                  RunPod
┌────────────┐              ┌───────────────────────────┐      ┌──────────────┐
│  Ana       │              │  FastAPI                   │ ①    │ ComfyUI:8188 │
│  Ben       │──HTTPS──────▶│   ├─ session cookies       │─────▶│ + H3 weights │
│  Carla     │              │   ├─ per-user job queue    │ ②    │              │
└────────────┘              │   └─ one orchestrator ─────┼─────▶│              │
                            │        (advisory lock)     │ ③    │              │
                            └──────┬──────────────┬──────┘◀─────┴──────────────┘
                                   │              │           ④ terminate
                            ┌──────▼─────┐  ┌─────▼──────────────┐
                            │  Postgres  │  │  S3                │
                            │  users     │  │  videos/{user}/…   │
                            │  jobs      │  │  uploads/{user}/…  │
                            └────────────┘  └────────────────────┘
```

The page is a **dumb client** — every decision and every secret stays
server-side. Each person sees only their own jobs and their own clips; the pod,
its policy and its budget are shared and admin-controlled.

---

## Using it

**Studio.** Write a prompt, optionally drag in reference images, pick a length
and a preset, and add it to the queue. Drag a `.zip` or a `.txt`/`.json` batch
onto the panel to queue many at once. A queued clip shows how many are ahead of
it in the shared queue, because the first render of a session waits through a
five-minute GPU boot.

**Archive.** Every clip you have finished, newest first, with previews and
downloads. Nobody else can see it, and nobody else's appears there.

**Admin** (administrators only). Create and disable users, set the RunPod key,
set the pod policy and the budget ceiling, and read the cost of every rented
session.

---

## Accounts

An administrator creates them. There is no public sign-up — anyone who could
sign themselves up could spend real money — and no password-reset email, so a
new password is handed over directly. Disabling somebody signs them out at once.

---

## What it costs

The GPU is rented by the minute and only while there is work.

- **Auto** (the usual setting) starts a pod when something is queued and shuts it
  down ten minutes after the queue empties.
- The **budget ceiling** tears the pod down and forces the policy to `off` when a
  session reaches it, so a bug cannot quietly run all night.
- A session also ends after six hours regardless.

`/admin` → Runs shows what every session actually cost.

---

## Running and deploying it

- Operators: **[docs/DEPLOY.md](docs/DEPLOY.md)** — environment, first sign-in,
  turning the GPU on, backups.
- Developers: **[CLAUDE.md](CLAUDE.md)** — local setup, layout, and the rules
  that are not obvious from any one file.

There is a demo mode (`MOCK=true`) that renders real MP4s with ffmpeg and rents
nothing, so the whole pipeline can be exercised for free.
