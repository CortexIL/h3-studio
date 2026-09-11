# Deploying H3 Studio on Dokploy

H3 Studio is one container. It needs a Postgres database and an S3-compatible
bucket, both of which already exist as Dokploy services.

---

## 1. Before you start

You need, from the Dokploy dashboard:

- **Postgres**: host, port, database, user, password.
- **S3**: endpoint URL, bucket name, access key, secret key.

And two generated secrets:

```bash
python -c "import secrets; print('SESSION_SECRET=' + secrets.token_urlsafe(48))"
python -c "import secrets; print('ADMIN_PASSWORD=' + secrets.token_urlsafe(18))"
```

`SESSION_SECRET` signs every session cookie. **Changing it signs everybody out**,
so generate it once and keep it.

---

## 2. Environment

Copy `.env.example`. Every variable, and what it does:

| Variable | Required | What it does |
|---|---|---|
| `DATABASE_URL` | yes | `postgresql://user:pass@host:5432/dbname`. Migrations run on every start. |
| `SESSION_SECRET` | yes | Signs session cookies. 32 characters or more. Never regenerate casually. |
| `STORAGE_BACKEND` | no (`s3`) | `s3` or `local`. `local` keeps clips in a directory — for development only. |
| `S3_ENDPOINT` | if `s3` | Your MinIO/S3 URL. Leave empty only for real AWS. |
| `S3_BUCKET` | if `s3` | Created on first start if it does not exist. |
| `S3_ACCESS_KEY` | if `s3` | |
| `S3_SECRET_KEY` | if `s3` | |
| `S3_REGION` | no (`us-east-1`) | |
| `ADMIN_EMAIL` | first boot | The first admin, created only when the users table is empty. |
| `ADMIN_PASSWORD` | first boot | Same. Clear it once you have signed in and changed it. |
| `RUNPOD_API_KEY` | no | Can be set here or saved from `/admin` afterwards. The database copy wins. |
| `POD_POLICY` | no (`off`) | `off`, `auto` or `keep-warm`. The database copy wins once an admin sets it. |
| `BUDGET_SESSION_LIMIT_USD` | no (`8`) | The pod is torn down and policy forced `off` at this figure. |
| `KEEP_AUDIO` | no (`true`) | `false` strips H3's audio track from every finished clip. |
| `MOCK` | no (`false`) | `true` renders placeholder clips with ffmpeg and rents no GPU. |
| `COOKIE_SECURE` | no (`true`) | Leave `true`. `false` only for plain-HTTP local development. |

---

## 3. Create the application

In Dokploy: **Create Application**.

- Source: `github.com/CortexIL/h3-studio`, branch `master`
- Build type: **Dockerfile**
- Port: **8777**
- Environment: paste the block from step 2
- Domain: your hostname, **HTTPS on**

For the first deploy leave `POD_POLICY=off` and `RUNPOD_API_KEY` empty. Nothing
should be able to rent a GPU before you have looked at the running app.

---

## 4. Replicas must stay at 1

The orchestrator owns the rented GPU. Two replicas would mean two pods billing at
the same time, and **nothing would error** — the bill would simply double.

A Postgres advisory lock makes this safe: only the process holding it starts or
stops a pod, and any other replica serves pages and drains nothing. So scaling up
buys no throughput and leaves you with a replica that looks healthy but does no
work. `/admin` shows a warning when the process you are talking to is not the one
holding the lock.

Leave replicas at 1.

---

## 5. First sign-in

Deploy, then check the logs in order:

1. `applied migrations: 001_init.sql`
2. `created the first admin account (you@example.com)`
3. `Application startup complete`

Then:

```bash
curl -fsS https://<your-domain>/api/health      # {"ok":true,"leader":true}
```

Open `https://<your-domain>/login` and sign in with `ADMIN_EMAIL` /
`ADMIN_PASSWORD`.

**Then change that password** on your Account page (`/account` → Change password), clear
`ADMIN_PASSWORD` from the Dokploy environment, and redeploy. The bootstrap only
fires against an empty users table, so leaving the variable set is a stored
secret with nothing left to do.

---

## 6. Turning the GPU on

From `/admin`:

1. **RunPod key** → paste it → *Verify & save*. It is checked against RunPod
   before it is stored, so a typo fails here rather than as a mysterious failed
   pod start an hour later.
2. **Session budget** → set the ceiling you are willing to lose to a bug.
3. **Policy** → `auto`.
4. **Redeploy once.** The backend — real RunPod or the mock — is chosen when the
   process starts, so a key saved into a running mock instance takes effect at
   the next restart.

---

## 7. Adding users

`/admin` → Users → fill in email, password, role → **Create**.

There is no email delivery and no password reset link. You hand out the password
yourself. Disabling a user signs them out immediately, on their next request.

---

## 8. Backups

Two things must be backed up, and losing either alone leaves the other useless:

- **Postgres** — accounts, job history, the RunPod key, the pod policy, the
  budget, cost history.
- **S3** — every finished clip and every uploaded reference image.

Dokploy can schedule the Postgres backup. The bucket is on the second drive;
back it up the same way you back up anything else on that drive.

---

## 9. Rolling back

Redeploy the previous commit. Migrations are additive and nothing is ever
dropped, so an older image runs against a newer schema without complaint.

---

## 10. Where the money goes

- `/admin` → **Runs** lists every rented session, why it stopped, and what it
  cost. The total is at the top.
- The pod terminates itself after `idle_shutdown_minutes` (10) with an empty
  queue, and at `max_session_hours` (6) regardless.
- Reaching the budget ceiling tears the pod down **and forces policy to `off`**,
  so it will not come straight back up. You turn it on again deliberately.

If you ever suspect a pod outlived the app:

```bash
python -m app.killpods
```

and check <https://console.runpod.io/pods>.
