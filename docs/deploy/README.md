# Deploying GovCareerOS

GovCareerOS stays local-first: the Windows setup (native Postgres or `docker/docker-compose.yml`, `uvicorn`,
`npm run dev`, `scripts/install_schedule.ps1`) is unchanged. This guide is the containerised stack in
`docker-compose.yml` at the project root, for a single machine or a small VPS.

| Service | Image | What it does |
| --- | --- | --- |
| `postgres` | `postgres:17` | Database, data in the `pgdata` volume. No published port. |
| `migrate` | `govcareeros-api:local` (`Dockerfile`) | One-shot `alembic upgrade head`; `api` and `scheduler` start only after it succeeds. |
| `api` | `govcareeros-api:local` | FastAPI on `127.0.0.1:8080`, health check `GET /health`. |
| `scheduler` | `govcareeros-api:local` | `scripts/scheduler.py`: daily job, weekly digest (and optional triage). |
| `web` | `govcareeros-web:local` (`frontend/Dockerfile`) | Next.js standalone dashboard on `127.0.0.1:3000`. |

Documents, digests and run logs live in the `storage` volume (`/data/storage`), shared by `api` and `scheduler`.
Both images run as a non-root user. No `.env` is copied into an image (`.dockerignore`): settings are read from
the environment when a container starts.

Every eligibility verdict shown by the deployed dashboard comes from the deterministic engine with its official
document page and quote; LLM drafts still wait for a reviewer. The official notification prevails.

## 1. Run locally with compose

Needs Docker Engine with Compose v2.24 or later (`env_file` with `required: false`).

```sh
cd govcareeros
# .env next to docker-compose.yml: at least POSTGRES_PASSWORD (see the list below)
docker compose config --quiet          # check the file and the variables
docker compose up -d --build
docker compose ps                      # migrate: exited (0); api, web: healthy
docker compose logs -f api scheduler
```

Open http://localhost:3000. The API is at http://localhost:8080 (`/health`, `/docs`).

Image options (build arguments, set in `.env` or the shell):

- `INSTALL_OCR=1` (compose default) adds Tesseract (English) for scanned notice pages.
- `INSTALL_PLAYWRIGHT=1` adds Chromium for the sources that need a browser (about 500 MB more).

Run one scheduled job by hand: `docker compose exec scheduler python govcareeros/scripts/scheduler.py --run-now daily_job`;
see the schedule: `... scheduler.py --list`. Any other script works the same way, e.g.
`docker compose exec api python govcareeros/scripts/triage_notices.py` (a dry run).

## 2. Environment variables

Names only; the defaults and meaning of each are in `backend/config/settings.py`. Put them in `.env` next to
`docker-compose.yml` (never commit it).

Compose / images

- `POSTGRES_PASSWORD` (required): password of the `govcareeros` database user; compose passes it to the app as
  `DATABASE_PASSWORD` beside a `DATABASE_URL` without a password, so any characters work (in `.env`, single-quote
  a value that contains `$`: compose expands `$` in unquoted values)
- `API_PORT`, `WEB_PORT`: host ports (bound to 127.0.0.1)
- `NEXT_PUBLIC_API_URL`: the API address as the browser sees it; compiled into the dashboard at build time
- `INSTALL_OCR`, `INSTALL_PLAYWRIGHT`: build arguments of the API image
- `FORWARDED_ALLOW_IPS`: proxy addresses whose `X-Forwarded-*` headers uvicorn trusts
- `WEB_CONCURRENCY`: uvicorn worker processes

Application

- `DATABASE_URL` (set by compose), `DATABASE_PASSWORD` (set by compose: replaces the password in `DATABASE_URL`),
  `DB_ECHO`, `STORAGE_PATH` (set by compose), `TEST_DATABASE_URL` (tests only)
- `FRONTEND_ORIGIN`, `FRONTEND_ORIGINS`: allowed browser origins for CORS (explicit origins; `*` is refused)
- `MAX_REQUEST_BYTES`: body limit for routes without their own (the notice check has a fixed 11 MB limit)
- `HSTS_SECONDS`: Strict-Transport-Security on https requests (0 = off)
- `LOG_FORMAT` (`json` in the images, `text` locally), `LOG_LEVEL`
- `LLM_PROVIDER`, `LLM_MODEL`, `OLLAMA_URL`, `LLM_TIMEOUT_SECONDS`, `LLM_NUM_CTX`
- `NVIDIA_API_KEY`, `NVIDIA_BASE_URL`, `NVIDIA_MODELS`, `NVIDIA_REQUESTS_PER_MINUTE`, `GOOGLE_API_KEY`
- `EMAIL_HOST`, `EMAIL_PORT`, `EMAIL_USERNAME`, `EMAIL_PASSWORD`
- `PLAYWRIGHT_HEADLESS`, `BROWSER_TIMEOUT_MS`, `TESSDATA_DIR`
- `IN_SERVICE_FORWARDING_BUFFER_DAYS`
- `REDIS_URL` (not used by the running services yet)

Scheduler

- `SCHEDULE_UTC_OFFSET`, `SCHEDULE_DAILY_JOB`, `SCHEDULE_SEND_DIGESTS`, `SCHEDULE_TRIAGE`, `SCHEDULE_DIGEST_SEND`

Inside a container `127.0.0.1` is the container itself: for an Ollama on the host set
`OLLAMA_URL=http://host.docker.internal:11434`, or use `LLM_PROVIDER=nvidia`.

## 3. Scheduled jobs

`scheduler` runs each job as its own process, one at a time, at `SCHEDULE_UTC_OFFSET` (default `+05:30`, IST):

| Job | Default | Command |
| --- | --- | --- |
| `daily_job` | `SCHEDULE_DAILY_JOB=07:00` | `scripts/daily_job.py`: discover, UPSC, triage, drafts, radar (and digest files when `send_digests` is off) |
| `send_digests` | `SCHEDULE_SEND_DIGESTS=mon 08:00` | `scripts/send_digests.py`; adds `--send` (email) only with `SCHEDULE_DIGEST_SEND=true` and SMTP settings, and emails only users with an active `ALERTS` consent |
| `triage` | `SCHEDULE_TRIAGE=` (off) | `scripts/triage_notices.py --apply`; the daily job already runs triage, so enable only for an extra pass |

Email consent: `--send` emails a user only while they have an active `ALERTS` consent record
(`backend/services/consent.py`); without one, or after it is withdrawn, the digest stays on the dashboard only.
There is no consent screen or unsubscribe link yet, so until there is, `SCHEDULE_DIGEST_SEND=true` emails no one.
The job's output names users by id, never by email address.

A value is `HH:MM` (daily) or `mon HH:MM` (weekly); empty turns the job off. A digest covers what changed since
the user's previous digest, so only one job writes digests: `send_digests` once a week while it is scheduled (the
scheduler then runs the daily job with `--skip digest`), otherwise the daily job's digest step. Runs missed while the container was
down are not caught up. The daily job keeps its own lock and its run log in `storage/logs/daily_job.jsonl`.
`docker compose stop` / `down` / an `up -d` recreate stop a running job cleanly: the scheduler (under `init: true`)
passes SIGTERM on, the daily job logs the run as `CRASHED`, rolls back its open step and releases its lock
(`stop_grace_period: 60s`).

Without the scheduler container, host cron works the same way:

```cron
# m h dom mon dow  (server clock in UTC: 01:30 UTC = 07:00 IST)
30 1 * * *  cd /srv/govcareeros && docker compose run --rm --no-deps scheduler python govcareeros/scripts/daily_job.py --skip digest
30 2 * * 1  cd /srv/govcareeros && docker compose run --rm --no-deps scheduler python govcareeros/scripts/send_digests.py
```

(and `docker compose up -d postgres api web` without `scheduler`). On Windows without Docker,
`scripts/install_schedule.ps1` registers the daily job with Task Scheduler as before.

## 4. Backup and restore (pg_dump)

```sh
# backup: custom format, compressed, consistent snapshot of a running database
docker compose exec -T postgres pg_dump -U govcareeros -d govcareeros -Fc > govcareeros-$(date +%F).dump

# documents and digests (the storage volume)
docker run --rm -v govcareeros_storage:/data -v "$PWD":/backup busybox tar czf /backup/storage-$(date +%F).tgz -C /data .
```

Keep copies off the server. Test a restore now and then.

```sh
docker compose stop api scheduler web
# restore into the existing database: drops and recreates every object in the dump
docker compose exec -T postgres pg_restore -U govcareeros -d govcareeros --clean --if-exists --no-owner < govcareeros-2026-10-01.dump
docker run --rm -v govcareeros_storage:/data -v "$PWD":/backup busybox tar xzf /backup/storage-2026-10-01.tgz -C /data
docker compose up -d       # migrate brings an older dump up to the current schema
```

The dump and the storage archive contain personal data (profiles, consent records): store them encrypted and
delete old ones on the retention schedule you have promised users.

## 5. Upgrading

```sh
docker compose exec -T postgres pg_dump -U govcareeros -d govcareeros -Fc > before-upgrade.dump
git pull
docker compose build
docker compose up -d       # migrate runs alembic upgrade head before api / scheduler start
docker compose logs migrate
```

Alembic by hand: `docker compose run --rm migrate alembic -c govcareeros/alembic.ini current` (or `history`,
`downgrade -1`). A downgrade can drop columns and their data; restore the dump instead when in doubt.

## 6. Minimal VPS

1. A small Ubuntu/Debian VM (2 vCPU, 4 GB RAM; more with `INSTALL_PLAYWRIGHT=1`), in an Indian region if the
   data must stay in India. Install Docker Engine and the compose plugin from Docker's repository.
2. Firewall: allow 22, 80, 443 only (`ufw allow OpenSSH; ufw allow 80,443/tcp; ufw enable`). Compose publishes
   the API and dashboard on 127.0.0.1 only; note that Docker's own published ports bypass ufw, which is why they
   are bound to 127.0.0.1.
3. Copy the project to `/srv/govcareeros`, create `.env` there (`chmod 600 .env`), with at least
   `POSTGRES_PASSWORD`, `FRONTEND_ORIGIN=https://app.example.org`, `NEXT_PUBLIC_API_URL=https://api.example.org`,
   `FORWARDED_ALLOW_IPS` (see below) and the LLM / email settings you use.
4. TLS reverse proxy on the host, e.g. Caddy (`/etc/caddy/Caddyfile`):

   ```
   app.example.org {
       reverse_proxy 127.0.0.1:3000
   }
   api.example.org {
       request_body {
           max_size 12MB
       }
       reverse_proxy 127.0.0.1:8080
   }
   ```

   Caddy connects through Docker's port mapping, so the API sees the Docker bridge gateway as the client; set
   `FORWARDED_ALLOW_IPS` to that address (`docker network inspect govcareeros_default`, "Gateway") or leave the
   compose default `*` as long as port 8080 is reachable only from the host. HSTS is then sent on every https
   response: by the API, and by the dashboard on requests the proxy marks `X-Forwarded-Proto: https` (Caddy and
   nginx `proxy_set_header X-Forwarded-Proto $scheme` do). The dashboard's production build also sends a
   Content-Security-Policy whose `connect-src` is the site and `NEXT_PUBLIC_API_URL` only.
5. `docker compose up -d --build`, then check `https://api.example.org/health` and the dashboard.
6. Daily backups (section 4) from cron, copied off the machine; `docker compose logs` for the JSON logs
   (tokens, passwords, API keys and the database password are redacted from log lines; treat the logs as
   private all the same).
