# GovCareerOS

GovCareerOS is a local-first career operating system for Indian government jobs and government exams. It
collects recruitment notices from official sources, reads their documents, and checks a candidate's profile
against each requirement with a deterministic eligibility engine that cites the page and the quoted words of the
official notification. LLMs are used only to draft extractions from public notice text, and those drafts wait for
a human reviewer. **The official notification always prevails**: verify every detail there before acting on it.
GovCareerOS is not affiliated with, or endorsed by, any government body.

## Key features

- **Source acquisition.** A registry of 93 candidate official sources (Union ministries, recruitment
  commissions, defence/CAPF, PSU banks, autonomous bodies, state PSCs), each enabled only after a probe finds
  recruitment content on its page. 27 listing adapters (`backend/sources/adapters/`), dedicated SSC and UPSC
  pipelines, and a generic notice discoverer for the rest. Pages that need JavaScript are rendered with
  Playwright (Chromium); PDFs are read with PyMuPDF, with Tesseract OCR for scanned pages. Documents are stored by
  SHA-256 content hash so re-crawls do not duplicate them, and notices are classified (vacancy, corrigendum,
  exam notice, result, ...) before ingestion.
- **Deterministic eligibility.** `backend/eligibility/` evaluates age (with category relaxations), education,
  experience, PwBD, ex-serviceman, certificate validity and deputation rules. Every verdict carries the official
  document, page and quote it was judged against, and results are keyed by an input fingerprint so they are
  recomputed when the notice, the profile or the engine changes.
- **LLM drafts behind a reviewer console.** The LLM locates a value, a verbatim quote and a page; the stored
  value is re-verified against the document text and stays a draft until a reviewer accepts it (`/review`).
  Providers: a local Ollama server or the NVIDIA API (`backend/llm/provider.py`). Only public notice text is sent
  to a provider, never profile data.
- **Candidate dashboard.** Profile and evidence-backed claims, matches, vacancy and exam detail, skill gaps,
  alerts, weekly digest, exam preparation (including previous-year papers), applications tracking, apply-ready
  checklists, resume builder, deputation search, a public "is this notice official?" check (`/verify`) and an
  operator console for assisted applications.
- **B2B verified-notice API (v1).** Organisation API keys with scopes (`notices:read`, `changes:read`,
  `verify:use`, `webhooks:manage`), per-key token-bucket rate limits, usage metering, a cursor-based change feed
  of new and officially changed notices, and HMAC-signed webhooks with retries and SSRF guards. See
  [docs/api](docs/api/README.md).
- **Privacy (DPDP Act 2023).** Purpose-bound consent records with a versioned notice, consent enforcement on
  processing, data export, erasure with a grace period, AES-256-GCM field encryption for category, disability and
  every stored text that quotes them, and secrets redacted from logs. See [docs/privacy](docs/privacy/).

## Architecture

The containerised stack in `docker-compose.yml`:

```
                 browser
                    |
        +-----------+-----------+
        |                       |
   web (Next.js)  ------>  api (FastAPI)  <---- scheduler (daily job, digests, triage)
   127.0.0.1:3000         127.0.0.1:8080            |
                               |                    |
                               +------ postgres ----+
                               |   (17, no public port)
                       migrate (alembic upgrade head, runs once before api/scheduler)
```

| Service | What it does |
| --- | --- |
| `postgres` | PostgreSQL 17, data in the `pgdata` volume |
| `migrate` | One-shot `alembic upgrade head`; `api` and `scheduler` start only after it succeeds |
| `api` | FastAPI (uvicorn) with health check `GET /health` and interactive docs at `/docs` |
| `scheduler` | `scripts/scheduler.py`: daily acquisition job, weekly digest, optional extra triage |
| `web` | Next.js standalone dashboard |

`api` and `scheduler` share the `storage` volume (documents, digests, run logs).

## Tech stack

- Backend: Python 3.12, FastAPI, uvicorn, Pydantic / pydantic-settings, SQLAlchemy 2 (async) with asyncpg,
  Alembic, httpx
- Data: PostgreSQL (SQLite via aiosqlite in tests)
- Documents: Playwright, PyMuPDF, Tesseract OCR (optional), python-docx (resume export), pycryptodomex (field
  encryption)
- LLM: Ollama (local) or the NVIDIA API, over HTTP
- Frontend: Next.js 16, React 19, TypeScript, Tailwind CSS 4
- Ops: Docker, Docker Compose, Windows Task Scheduler for the local daily job

## Quick start

Configuration comes from a `.env` file in the project root (git-ignored); `.env.example` lists the names and
`backend/config/settings.py` holds the defaults. Commonly needed: `DATABASE_URL`, `FIELD_ENCRYPTION_KEY`,
`LLM_PROVIDER`, `LLM_MODEL`, `OLLAMA_URL` or `NVIDIA_API_KEY`, `EMAIL_*`, and for compose `POSTGRES_PASSWORD`.

The code imports itself as `govcareeros.backend...`, so clone into a folder named `govcareeros` and put its
**parent** directory on `PYTHONPATH`.

### Local (Windows)

```powershell
git clone https://github.com/krishnaverma24102004-prog/GovCareerOS.git govcareeros
cd govcareeros
python -m venv .venv; .\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
python -m playwright install chromium          # only for browser-rendered sources
Copy-Item .env.example .env                    # then fill in the values

# Database: a native PostgreSQL, or the development container (host port 5433)
docker compose -f docker/docker-compose.yml up -d postgres

cd ..
$env:PYTHONPATH = (Get-Location).Path
alembic -c govcareeros/alembic.ini upgrade head
uvicorn govcareeros.backend.api.main:app --port 8080

# second terminal
cd govcareeros\frontend
npm install
npm run dev                                    # http://localhost:3000
```

Run the daily job by hand with `python govcareeros/scripts/daily_job.py`, or register it with Task Scheduler:
`powershell -ExecutionPolicy Bypass -File govcareeros\scripts\install_schedule.ps1`.

### Docker Compose

Needs Docker Engine with Compose v2.24 or later.

```sh
cd govcareeros
# .env next to docker-compose.yml: at least POSTGRES_PASSWORD
docker compose config --quiet          # check the file and the variables
docker compose up -d --build
docker compose ps                      # migrate: exited (0); api, web: healthy
docker compose logs -f api scheduler
```

Open http://localhost:3000; the API is at http://localhost:8080 (`/health`, `/docs`). Build arguments
`INSTALL_OCR=1` (compose default) and `INSTALL_PLAYWRIGHT=1` (about 500 MB more) add Tesseract and Chromium.
Backups, upgrades, scheduling and a minimal VPS setup behind a TLS reverse proxy are in
[docs/deploy](docs/deploy/README.md).

## Running tests

About 795 test functions in 85 files under `tests/`, using fixtures captured from real official notices.

```powershell
$env:PYTHONPATH = "<parent directory of govcareeros>"
cd govcareeros
pytest
pytest tests/eligibility                        # one area
```

Database tests run on in-memory SQLite always, and again on PostgreSQL when `TEST_DATABASE_URL` points at a
reachable database (otherwise those variants are skipped). The markers `postgres` and `live` are declared in
`pyproject.toml` but no test uses them yet. Frontend type check: `cd frontend; npm run typecheck`.

## Project layout

```
backend/
  api/            FastAPI app and routers (auth, profile, vacancies, exams, eligibility, review console,
                  verify, privacy, admin, operator, v1 B2B API, ...)
  sources/        source registry, listing adapters, SSC/UPSC pipelines, notice classifier, URL guard
  acquisition/    fetch strategy (HTTP or browser)
  documents/      PDF text extraction and OCR
  parsing/        notice and advertisement parsers
  extraction/     LLM requirement extraction and quote validation
  eligibility/    deterministic eligibility engines and rules
  llm/            LLM provider abstraction (Ollama, NVIDIA)
  privacy/        consent notice, enforcement, export/erasure, field encryption
  services/       ingestion, API keys, webhooks, consent, digests and other domain services
  models/, schemas/, repositories/, database/, config/   persistence and settings
alembic/          database migrations
frontend/         Next.js dashboard (src/app holds the pages)
scripts/          daily job, scheduler, source discovery, triage, webhooks dispatch, digests, privacy erasure
tests/            pytest suite and captured fixtures
docker/           development database compose file
docs/             api, deploy and privacy documentation
```

## Documentation

- [docs/api/README.md](docs/api/README.md): verified-notice API (authentication, scopes, change feed, webhooks)
- [docs/deploy/README.md](docs/deploy/README.md): compose stack, environment variables, jobs, backups
- [docs/privacy/data_inventory.md](docs/privacy/data_inventory.md): personal data inventory, encryption, retention
- [docs/privacy/breach_runbook.md](docs/privacy/breach_runbook.md): breach response runbook

## Disclaimer

GovCareerOS provides information extracted from official government publications and an automated reading of
them. It is not affiliated with any government body, does not guarantee eligibility or selection, and can be
wrong: the official notification prevails in every case. Check the original notice before applying.

## License

No license has been chosen yet.
