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

The browser runs the interface and nothing more — every decision about who may
see what, and every secret, stays server-side. Each person sees only their own jobs and their own clips; the pod,
its policy and its budget are shared and admin-controlled.

---

## Using it

**Studio.** Write a prompt — or one per line, with *Line = clip* — drag or paste
in reference images, pick a length, quality and mode, and add it to the queue
(⌘/Ctrl + Enter works too). The estimate under the form says what it will cost
before you commit. Drop a `.zip` or a `.txt`/`.json` batch onto the panel to
queue many at once — a `.zip` carries the reference images and the audio tracks
its jobs name, so a whole lip-synced song goes up in one file:

```json
{ "defaults": { "seconds": 12, "preset": "balanced" },
  "jobs": [ { "prompt": "the first line", "image": "face.png", "audio": "seg01.wav" },
            { "prompt": "the second line", "image": "face.png", "audio": "seg02.wav" } ] }
```

A job that names a track the archive does not carry is refused before anything is
queued, because a clip rendered without the audio it was written for costs the
same rented minutes as a right one. A queued clip shows how many are ahead of it in the shared
queue, because the first render of a session waits through a five-minute GPU
boot. *Use again* copies any clip's settings back into the form.

**Archive.** Every clip you have finished, newest first. Search your prompts,
filter by quality and mode, and open any clip to watch it, copy its prompt,
download it or queue it again — the arrow keys step through the rest. Deleting a
clip removes the file for good. Nobody else can see your archive, and nobody
else's clips appear in it.

**Account.** Change your password, and sign out of every other device.

**Admin** (administrators only). Create users, reset passwords, change roles and
disable accounts, with each person's clips and storage at a glance. Set the
RunPod key, the pod policy and the budget ceiling, and read the cost of every
rented session.

---

## Accounts

An administrator creates them. There is no public sign-up — anyone who could
sign themselves up could spend real money — and no password-reset email, so a
new password is handed over directly. Anyone can change their own afterwards,
from their Account page. Disabling somebody signs them out at once.

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
