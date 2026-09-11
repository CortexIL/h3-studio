# H3 Studio

A hosted, multi-user front end for running **MiniMax H3** on a rented RunPod GPU.
People sign in, queue prompts, and get finished MP4s in their own archive. One
shared pod renders everyone's work; clips live in an S3-compatible bucket.

It used to be a single-user desktop app with a SQLite file and no login. It is
not that any more — if you find something that assumes a local folder, one user,
or a `config.yaml`, it is a leftover and should go.

## Run it locally

Postgres must be running. Then:

```bash
createdb h3_dev
python3.12 -m venv .venv && .venv/bin/pip install -r requirements-dev.txt
```

```bash
DATABASE_URL="postgresql://$(whoami)@localhost:5432/h3_dev" \
SESSION_SECRET="$(python -c 'import secrets;print(secrets.token_urlsafe(48))')" \
STORAGE_BACKEND=local LOCAL_STORAGE_DIR=/tmp/h3objects \
MOCK=true POD_POLICY=auto COOKIE_SECURE=false \
ADMIN_EMAIL=me@local ADMIN_PASSWORD=passphrase-1 PORT=8799 \
.venv/bin/python -m app.main
```

`MOCK=true` renders real MP4s with ffmpeg and rents nothing. `STORAGE_BACKEND=local`
keeps objects in a directory instead of a bucket. Sign in at
<http://localhost:8799/login>.

The backend serves the built client, so build it once first:

```bash
cd frontend && npm ci && npm run build
```

While working on the client, run `npm run dev` in `frontend/` instead: Vite serves
it at <http://localhost:5173> with hot reload and proxies `/api` to the backend on
:8799. `npm run dev:mock` needs no backend at all - a mock API with sample data
runs in the browser, signed in as an admin.

## Test

```bash
createdb h3_test && .venv/bin/python -m pytest
```

The suite needs a real Postgres — it exercises `FOR UPDATE SKIP LOCKED`, advisory
locks and JSONB, none of which a fake reproduces. It skips with instructions if
there is no database, and `TEST_DATABASE_URL` overrides the default.

The client has its own checks, and CI runs them too:

```bash
cd frontend && npm run lint && npm run typecheck && npm test
```

## Layout

| Module | Owns |
|---|---|
| `app/settings.py` | Every environment variable. Fails fast, naming what is missing. |
| `app/config.py` | What the model does: presets, weights, boot timeouts. Not deployment. |
| `app/store/pool.py` | The async pool, migrations, the orchestrator's advisory lock. |
| `app/store/users.py` | Accounts, argon2 hashes, `token_version`. |
| `app/store/jobs.py` | Job rows. Every route-reachable read is scoped to one user. |
| `app/store/runs.py` | Pod sessions, for cost history. |
| `app/store/kv.py` | Settings an admin changes at runtime. |
| `app/migrations/*.sql` | The schema. Applied in filename order at startup. |
| `app/auth.py` | Session cookies, `current_user`, `require_admin`. |
| `app/storage.py` | The object store: `S3Storage` and `LocalStorage`, one interface. |
| `app/sinks/` | Where a finished clip goes. One sink, over that interface. |
| `app/batch.py` | Parsing an uploaded `.txt` / `.json` / `.zip` batch. |
| `app/routes/` | One router per concern, each carrying its own auth dependency. |
| `app/orchestrator.py` | The single loop that owns the pod and drains the queue. |
| `app/backends/` | RunPod and ComfyUI, behind one protocol. `mock.py` fakes both. |
| `app/main.py` | Wiring, lifespan, and the pages: one `index.html` per route, behind the sign-in guards. |
| `frontend/` | The browser client: React, TypeScript, Vite, Tailwind, shadcn/ui. Built to `frontend/dist`, which `app/main.py` serves. |
| `web/` | The previous client. No page links to it; delete it and the `/static` mount once the React client has run in production for a few days. |

Operator scripts, all reading the same environment: `doctor.py` (check a config
before spending money), `smoketest.py`, `calibrate.py` (measure real cost),
`killpods.py` (kill anything the app left running).

## Rules that are invisible in any single file

- **Every `/api` route carries its auth dependency explicitly.** Never add one
  without it. `tests/test_isolation.py` walks the OpenAPI schema and fails if a
  route answers anything but 401/403 without a session. Do not add a middleware
  that guards by path prefix — a route added later would silently fall outside it.
- **Another user's resource is 404, never 403.** A 403 confirms it exists.
- **`jobs.get_for(user_id, id)` in routes; `jobs.get_any(id)` only in the
  orchestrator.** They are separate names so an unscoped read is visible at the
  call site rather than hidden in an optional argument.
- **Keys from the browser are filtered to the caller's own prefix** before they
  reach a job row. Authorization is decided from the database row, never parsed
  from an object key.
- **One uvicorn worker, one orchestrator, one pod.** The advisory lock in
  `Orchestrator.start` enforces it. Do not remove it, and do not raise the
  replica count.
- **Nothing writes to disk at runtime.** The container filesystem is wiped on
  every redeploy. Anything an admin can change goes in the `kv` table.
- **Never log** a password, a hash, a session cookie, the RunPod key, or S3
  credentials. The key may appear as its last four characters and nothing more.
- **Presets and the weight manifest are code, not environment.** They describe
  what gets rendered, not where it runs.
- **Prompts are React text, never HTML.** They are arbitrary text from another
  user's keyboard. `dangerouslySetInnerHTML` fails lint (`react/no-danger`).
- **No `alert`, `confirm` or `prompt`.** Use the app's dialogs (`useConfirm`) and
  toasts. Lint bans them, and a test tripwire makes them throw.
- **Pages are guarded on the server.** `app/main.py` decides who gets a route's
  HTML before any script runs; the client's own checks are the second line.

## Money

The budget ceiling and the idle shutdown are the only things standing between a
bug and a bill. `_enforce_ceilings` tears the pod down *and* forces policy to
`off`, so a runaway cannot immediately restart itself.

Any change to the policy machine in `orchestrator.py` must leave
`tests/test_orchestrator.py` passing — it drives the whole thing against a fake
backend, including the retry cap and the ceiling, with no GPU and no timers.

## Deploying

See [docs/DEPLOY.md](docs/DEPLOY.md).

## Why things are the way they are

Design and implementation records live in `docs/superpowers/specs/` and
`docs/superpowers/plans/`. If you are about to argue with a decision, the
argument that produced it is probably written down there.
