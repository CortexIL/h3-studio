# H3 Studio: multi-tenant server on Dokploy

Date: 2026-09-11
Status: approved design, pending implementation plan

## Problem

H3 Studio today is a single-user desktop app: a FastAPI process bound to
`127.0.0.1`, a SQLite file, a queue with one implicit owner, and finished MP4s
written into a folder on the operator's own disk. It is launched by
double-clicking a `.bat` file and stopped by a button that shuts the process down.

It needs to become a hosted service on Dokploy where several people each sign in,
queue their own clips, and see their own archive — without seeing each other's
prompts or videos, and without any one of them being able to start a second rented
GPU.

The existing seams make this cheaper than it sounds. `jobs` already carries an
`owner` column. `OutputSink` is already an interface with one implementation.
`Backend` already isolates RunPod behind a protocol. The work is filling those
seams in, replacing SQLite with Postgres, adding authentication, and deleting the
desktop half.

## Decisions taken

| Question | Decision |
|---|---|
| Account creation | Admin creates users. No public signup. |
| GPU ownership | One shared RunPod key, one shared pod, one queue. Admin owns policy and budget. |
| Video storage | S3-compatible (existing MinIO-style endpoint on a second HDD), private bucket, streamed through the app. |
| Desktop mode | Dropped. Server-only, Postgres-only, one code path. |
| First admin | Created from environment variables on first boot when `users` is empty. |
| Inbox folder watcher | Dropped. Browser upload of images / `.zip` / `.txt` / `.json` keeps the same parser. |
| Normal-user scope | Own jobs and own archive only. |

## Architecture

```
                        Dokploy host
┌──────────────────────────────────────────────────────────┐
│  h3-studio container (1 replica, 1 uvicorn worker)        │
│    FastAPI  ── /api/auth, /api/jobs, /api/archive,        │
│                /api/video/{id}, /api/admin/*              │
│    Orchestrator (singleton, holds a PG advisory lock)     │
│      └── Backend (RunPod) ──▶ ComfyUI on the rented pod   │
│      └── S3Sink ────────────┐                             │
└─────────────────────────────┼─────────────────────────────┘
            │                 │
    ┌───────▼──────┐   ┌──────▼────────────────────┐
    │  Postgres    │   │  S3 (second HDD)          │
    │  users, jobs │   │  videos/{user}/{job}.mp4  │
    │  runs, kv    │   │  uploads/{user}/{id}.png  │
    └──────────────┘   └───────────────────────────┘
```

Browsers hold a signed session cookie and talk only to the container. The bucket
is never exposed to the internet; video bytes are proxied by the app after an
ownership check.

## Components

### 1. Persistence — `app/db.py`

Rewritten against Postgres using `psycopg` 3 with an async connection pool.

The current module opens a fresh `sqlite3` connection on **every call** and runs
synchronously inside `async` request handlers. Against a local file that is merely
wasteful. Against Postgres over a socket, each call blocks the event loop for every
connected user simultaneously, and the two-second status poll multiplies it by the
number of open tabs. Every function in `db.py` becomes `async` and acquires from a
pool created at startup.

Schema:

```sql
CREATE TABLE users (
    id            UUID PRIMARY KEY,
    email         TEXT NOT NULL UNIQUE,      -- stored lowercased
    password_hash TEXT NOT NULL,
    role          TEXT NOT NULL DEFAULT 'user',   -- user | admin
    is_active     BOOLEAN NOT NULL DEFAULT TRUE,
    token_version INTEGER NOT NULL DEFAULT 1,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE jobs (
    id           TEXT PRIMARY KEY,
    user_id      UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    status       TEXT NOT NULL DEFAULT 'queued',
    prompt       TEXT NOT NULL,
    ref_images   JSONB NOT NULL DEFAULT '[]',   -- S3 keys
    seconds      INTEGER NOT NULL DEFAULT 10,
    seed         BIGINT,
    mode         TEXT NOT NULL DEFAULT 'i2v',
    preset       TEXT NOT NULL DEFAULT 'final',
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    started_at   TIMESTAMPTZ,
    finished_at  TIMESTAMPTZ,
    attempts     INTEGER NOT NULL DEFAULT 0,
    error        TEXT,
    output_key   TEXT,                          -- S3 key, was output_path
    output_bytes BIGINT,
    remote_id    TEXT
);
CREATE INDEX idx_jobs_user_created ON jobs(user_id, created_at DESC);
CREATE INDEX idx_jobs_queue        ON jobs(status, created_at) WHERE status = 'queued';
CREATE INDEX idx_jobs_archive      ON jobs(user_id, finished_at DESC) WHERE status = 'done';

CREATE TABLE runs (                       -- pod sessions, for cost tracking
    id            TEXT PRIMARY KEY,
    pod_id        TEXT,
    status        TEXT NOT NULL,            -- booting | ready | stopping | stopped | error
    endpoint      TEXT,
    gpu_type      TEXT,
    started_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    ended_at      TIMESTAMPTZ,
    cost_estimate NUMERIC(10,4) NOT NULL DEFAULT 0,
    note          TEXT
);

CREATE TABLE kv (k TEXT PRIMARY KEY, v TEXT NOT NULL);

CREATE TABLE schema_migrations (
    filename    TEXT PRIMARY KEY,
    applied_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
```

`owner TEXT DEFAULT 'me'` becomes `user_id UUID` with a foreign key.
`output_path` becomes `output_key`. `seed` widens to `BIGINT` because ComfyUI
seeds exceed 32 bits.

`claim_next_queued()` keeps its atomicity but drops the manual `BEGIN IMMEDIATE`
in favour of the Postgres idiom, which is both correct and non-blocking:

```sql
UPDATE jobs SET status='running', started_at=now(), attempts=attempts+1
WHERE id = (SELECT id FROM jobs WHERE status='queued'
            ORDER BY created_at FOR UPDATE SKIP LOCKED LIMIT 1)
RETURNING *;
```

The queue stays global and FIFO across all users — it feeds one shared pod, so
per-user fairness is not a scheduling problem yet. Noted as a known limitation.

### 2. Migrations — `app/migrations/`

Numbered plain-SQL files (`001_init.sql`, `002_….sql`) applied at startup inside a
single transaction, with applied filenames recorded in `schema_migrations`. No
Alembic: the schema is four tables and an ORM-less codebase, and a migration
runner that is thirty lines is easier to reason about at deploy time than a
framework whose autogenerate would have nothing to introspect.

There is no data migration from SQLite. The desktop install's job history stays on
the desktop; the server starts empty.

### 3. Authentication — `app/auth.py` (new)

Passwords hashed with `argon2-cffi` at library defaults.

Sessions are stateless signed cookies (`itsdangerous.URLSafeTimedSerializer`)
carrying `{user_id, token_version}`, set `HttpOnly`, `SameSite=Lax`, `Secure`, and
a 14-day max age. A server-side session table is avoided, but revocation still
works: disabling a user increments `users.token_version`, and every existing
cookie for that user stops validating on its next request.

`SESSION_SECRET` comes from the environment. If it is unset the app refuses to
start rather than generating one at boot — a generated secret would silently log
every user out on each redeploy, and would differ between replicas.

Routes:

- `POST /api/auth/login` — email + password, constant-time-ish failure (same error
  message and roughly the same work whether the email exists or the password is
  wrong: a missing email still runs one argon2 verification against a fixed dummy
  hash, so response time does not reveal which accounts exist). Rate limited to 10
  attempts per IP per 5 minutes by an in-process counter — sufficient at one
  replica, and the replica count is already pinned at 1 for the orchestrator.
- `POST /api/auth/logout`
- `GET  /api/me` — id, email, role.

A FastAPI dependency `current_user` guards every `/api/*` route except
`/api/auth/login` and `/api/health`. A second dependency `require_admin` guards
`/api/admin/*`. Guarding is by explicit dependency on each router, not by
middleware pattern-matching on paths, so a new route added later is not silently
public.

Bootstrap: on startup, if `users` is empty and `ADMIN_EMAIL` / `ADMIN_PASSWORD`
are set, create that admin. Idempotent — with any user present it does nothing.

### 4. Ownership scoping — `app/main.py`

Every job read and write is filtered by `user_id`. An id belonging to another user
returns **404, not 403**: a 403 confirms the job exists, which leaks that someone
else queued something.

Admins do not see other users' jobs on the normal endpoints either. `/api/admin/jobs`
is a separate, explicitly-admin route, so there is no branch inside the normal path
that could be reached with the wrong role.

Endpoints after the change:

| Route | Who | Notes |
|---|---|---|
| `GET /api/status` | user | own counts, own queue position, shared pod state, no other prompts |
| `GET/POST /api/jobs` | user | own only |
| `GET/PATCH/DELETE /api/jobs/{id}` | owner | 404 otherwise |
| `POST /api/jobs/{id}/retry|again|cancel` | owner | |
| `POST /api/jobs/again-all` | user | own jobs of a status |
| `GET /api/archive?cursor=` | user | own finished clips, keyset-paginated |
| `GET /api/video/{id}` | owner | streams from S3, Range supported |
| `GET /api/image/{key}` | owner | reference image preview |
| `POST /api/upload` | user | reference image → S3 under own prefix |
| `POST /api/inbox/upload` | user | zip / batch file → parsed → own jobs |
| `POST /api/estimate` | user | |
| `GET/POST /api/admin/users` | admin | create, disable, reset password |
| `GET /api/admin/jobs` | admin | all jobs |
| `GET/POST /api/admin/policy` | admin | pod policy |
| `POST /api/admin/runpod-key` | admin | verified against RunPod, stored in `kv` |
| `GET/POST /api/admin/budget` | admin | session ceiling |
| `GET /api/admin/runs` | admin | cost history |
| `GET /api/health` | public | liveness for Dokploy |

Queue position: a user's queued job reports how many jobs sit ahead of it in the
global queue — a count only, never another user's prompt. Without it, a job that is
waiting through a five-minute cold boot is indistinguishable from a broken one.

### 5. Storage — `app/sinks/s3.py` (new)

`S3Sink` implements the existing `OutputSink` protocol, so the orchestrator is
untouched. `boto3` client configured from `S3_ENDPOINT`, `S3_BUCKET`,
`S3_ACCESS_KEY`, `S3_SECRET_KEY`, `S3_REGION`, with path-style addressing (MinIO
does not do virtual-host style by default). Blocking boto3 calls are wrapped in
`asyncio.to_thread`.

Keys:

```
videos/{user_id}/{job_id}_{slug}.mp4
uploads/{user_id}/{uuid}.{ext}
```

The user id is in the key so an object's owner is legible from the key alone, but
authorization is always decided from the database row, never parsed from the key.

`GET /api/video/{job_id}` looks up the job, checks ownership, then returns a
`StreamingResponse` over the S3 object, forwarding the `Range` header so the
player can seek without pulling the whole clip. `Content-Disposition` is set for
the download button.

Audio stripping (`strip_audio`) currently rewrites a file in place with ffmpeg. It
moves to run on a temp file before upload, so exactly one object is ever written.

The `LocalFolderSink` is kept for tests and the mock backend, selected by
`OUTPUT_SINK=local`; `s3` is the default.

### 6. Reference images — `Backend.upload_image`

`upload_image(self, local_path: Path)` becomes `upload_image(self, data: bytes,
name: str)`. References now live in S3, not on the container's disk, and the
orchestrator reads the object and hands the bytes to the backend. `ComfyClient.upload_image`
changes the same way. This is the one interface change that ripples into
`runpod_pod.py`, `comfy.py` and `mock.py`.

### 7. Orchestrator — `app/orchestrator.py`

Logic unchanged: same policy machine, same budget ceilings, same retry rules. Two
additions:

**Singleton enforcement.** Before starting its loop the orchestrator takes a
Postgres session-level advisory lock (`pg_try_advisory_lock`). If it cannot get it,
it logs and runs the app in web-only mode. The orchestrator owns a rented GPU; two
of them means two pods billing simultaneously, and the failure is silent — nothing
errors, the bill just doubles. The lock is insurance against a replica count that
gets bumped in the Dokploy UI months from now.

**Policy default.** `pod.policy` reads from `kv` as it does today, but `off` is
harmless and `auto` costs money, so a fresh install starts at `auto` only if
`POD_POLICY` says so; otherwise `off` until an admin turns it on.

`db.counts()` becomes global (for pod decisions) plus a per-user variant (for the
UI), so the status poll does not need two round trips.

### 8. Configuration — `app/config.py`

All configuration comes from environment variables via `pydantic-settings`. The
`config.yaml` file becomes baked-in defaults only; **nothing writes to disk at
runtime**, because a container filesystem does not survive a redeploy.

Settings that an admin can change from the UI — RunPod key, pod policy, budget
ceiling — are stored in the `kv` table, which does survive. Environment variables
provide the initial value; the database wins once set.

Required at boot: `DATABASE_URL`, `SESSION_SECRET`, `S3_*`. Optional:
`RUNPOD_API_KEY`, `ADMIN_EMAIL`, `ADMIN_PASSWORD`, `POD_POLICY`,
`BUDGET_SESSION_LIMIT_USD`, `MOCK`. The app fails fast with a readable message
naming the missing variable rather than starting and breaking at first use.

### 9. Frontend — `web/`

- `login.html` — email, password, one error line.
- `index.html` — the studio. Feed shows own jobs. Header gains the signed-in email
  and a Sign out control. Loses the Quit button, the output-folder settings and the
  RunPod key panel.
- `archive.html` — own finished clips, newest first, infinite scroll on the keyset
  cursor, inline `<video>` preview and a download link.
- `admin.html` — users table with create/disable, pod policy, RunPod key, budget,
  all-jobs list, run cost history.
- `app.js` — all requests carry `credentials: 'same-origin'`; a 401 redirects to
  `/login`. Polling stays at two seconds but the status payload shrinks: the inbox
  scan is gone, and `list_jobs` becomes a bounded page instead of up to 500 rows on
  every tick.

### 10. Container and deployment

`Dockerfile`: `python:3.12-slim`, ffmpeg installed (needed by the audio strip and
by the mock backend's real-MP4 generation), dependencies installed as a separate
layer from source so code changes do not re-resolve pip, runs as a non-root user,
`EXPOSE 8777`, healthcheck on `/api/health`.

`uvicorn --workers 1` — non-negotiable while the orchestrator is in-process. The
advisory lock backs this up; the flag documents it.

`.dockerignore` excludes `.git`, `data/`, `out/`, `inbox/`, `__pycache__`,
`config.yaml`.

`docker-compose.yml` for Dokploy: the app service, the volume-free filesystem, and
every environment variable listed with an empty value so the Dokploy UI shows the
full set that needs filling in. Postgres and S3 are existing Dokploy services and
are referenced, not defined.

`docs/DEPLOY.md`: the env var table, the first-admin flow, and the "replicas must
stay at 1" warning.

## Deleted

`app/launch.py`, `H3 Studio.bat`, `H3 Studio (Demo).bat`, `h3studio.ico`,
`app/mcp_server.py`, `app/install_mcp.py`, `app/inbox.py`'s filesystem scanner (the
batch/zip parser survives, fed by uploads), `/api/quit`, `/api/folder`,
`/api/folder/open`, `_reveal()`, the port-conflict console help in `main.py`.

Kept as operator scripts, adapted to env config: `doctor.py`, `smoketest.py`,
`calibrate.py`, `killpods.py`.

## Data flow: one clip, end to end

1. User signs in; browser holds a signed cookie.
2. User uploads a reference image → `POST /api/upload` → S3 `uploads/{user}/…` →
   the key is returned.
3. User submits a prompt → `POST /api/jobs` → row in `jobs` with `user_id` and the
   reference key.
4. Orchestrator (holding the advisory lock) sees queued work, starts the pod if
   policy allows, claims the job with `FOR UPDATE SKIP LOCKED`.
5. It fetches the reference bytes from S3 and hands them to the backend, which
   uploads them to ComfyUI and submits the workflow.
6. On completion it downloads the MP4, optionally strips audio, uploads to
   `videos/{user}/…`, and sets `status='done'`, `output_key`.
7. The user's next poll shows the job done. `GET /api/video/{id}` checks ownership
   and streams the object.
8. Queue empties; after `idle_shutdown_minutes` the pod is terminated and the run's
   cost is recorded.

## Error handling

- **Missing required env var** → refuse to start, name the variable.
- **Postgres unreachable at boot** → retry with backoff for 60s (Dokploy may start
  the app before the database is accepting connections), then exit non-zero so the
  platform restarts it.
- **S3 unreachable when saving output** → the existing `_fail_or_retry` path
  applies; the job returns to the queue rather than losing a clip that has already
  been paid for.
- **Advisory lock unavailable** → serve the web app, log loudly, do not process the
  queue.
- **Session cookie invalid or user disabled** → 401, browser redirects to login.
- **Another user's job id** → 404.
- **Budget ceiling reached** → unchanged: tear down the pod and force policy `off`.

## Testing

- `pytest` with a Postgres service (testcontainers or a `DATABASE_URL` pointing at
  a scratch database), migrations applied per session, truncation between tests.
- Auth unit tests: hash round-trip, cookie tamper rejection, expiry, token_version
  revocation.
- **Isolation tests are the ones that matter**: for each of `/api/jobs/{id}`,
  `PATCH`, `DELETE`, `retry`, `again`, `cancel`, `/api/video/{id}`, `/api/image/…`,
  user B receives 404 for user A's resource. Written as one parametrized test over
  the route table so a route added later without scoping fails the suite.
- Admin routes reject a normal user.
- Orchestrator tests against `MockBackend` with a fake sink: queue drains, retries
  cap at three, budget ceiling forces `off`.
- `claim_next_queued` concurrency: N parallel claimers over M jobs yield each job
  exactly once.
- S3 sink against a MinIO container or `moto`.

## Known limitations, accepted

- The queue is globally FIFO. One user submitting forty clips delays everyone.
  Per-user round-robin is a later change; the schema already supports it.
- No per-user quota. The budget ceiling is global. Any user can consume it.
- One pod for everyone; concurrency is bounded by `MAX_INFLIGHT = 2`.
- Video bytes proxy through the app. Fine for a handful of users on the same host;
  presigned URLs are the escape hatch if that stops being true.
- No email delivery. Admins hand out passwords directly; there is no reset link.

## Out of scope

Billing, per-user quotas, multi-pod scheduling, public signup, password reset by
email, an S3 lifecycle/retention policy for old clips.
