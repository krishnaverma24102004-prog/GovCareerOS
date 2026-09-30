# GovCareerOS dashboard

Local, read-mostly dashboard for the FastAPI backend (Next.js App Router + TypeScript + Tailwind).
It holds no data of its own: every page shows what the API returns, and the only writes are the
profile edit form (a new immutable profile version), marking alerts read, and reviewer decisions.

| Page | What it shows |
| --- | --- |
| `/login` | Register / log in. The bearer token is kept in `sessionStorage` only. |
| `/profile` | Current version, every earlier version, and an edit form for the personal section (category, PwBD and disability %, ex-serviceman, Central Govt employee, citizenship, preferred locations, career interests). Saving PATCHes `/profile` and shows the new version number. |
| `/matches` | `GET /matches` for the latest profile version (resolved to its explicit id first), with eligibility badges and relevance 0-100. |
| `/vacancies/[id]` | Vacancy details, each field's provenance (page, quote, link to the official PDF at that page), active requirements, and the eligibility checks for your latest version with the reason for each. |
| `/alerts` | Your alerts, unread first; mark one or all read. |
| `/review` | Extraction drafts with the validator's per-field verdict (quote, page, match type). Reviewers can approve selected vacancies or reject the rest with a note; others see it read-only. |

## Run

```sh
# 1. backend (from the folder that contains govcareeros/)
$env:PYTHONPATH="C:\Users\krishna"
uvicorn govcareeros.backend.api.main:app --port 8080

# 2. dashboard
cd govcareeros/frontend
npm install
npm run dev          # http://localhost:3000
```

The API only accepts browser requests from `FRONTEND_ORIGIN` (default `http://localhost:3000`, set in
`.env` or the environment). Open the dashboard at exactly that origin: `http://127.0.0.1:3000` is a
different origin and is refused.

Point the dashboard at another API with `NEXT_PUBLIC_API_URL` (default `http://127.0.0.1:8080`), e.g.
in `frontend/.env.local`:

```
NEXT_PUBLIC_API_URL=http://127.0.0.1:8080
```

Reviewer rights are the `users.is_reviewer` flag; there is no UI to grant it.

## Checks

```sh
npx tsc --noEmit
npm run build
```
