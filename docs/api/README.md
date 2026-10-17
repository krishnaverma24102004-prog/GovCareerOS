# GovCareerOS verified-notice API (v1)

Official Indian government recruitment notices, each with the source that published it, the page it was found
on, its document, and (where a parser or reviewer has read the document) every requirement with its page and
quoted words. **The official notification prevails**: every response carries a `disclaimer` saying so; verify
every detail in the official notification before acting on it. GovCareerOS is not affiliated with any
government body.

- OpenAPI document of these endpoints only: `GET /api/v1/openapi.json` (no key needed).
  The app's interactive docs (`/docs`) group them under the `v1: ...` tags.
- Base URL in the examples: `http://localhost:8080` (local API). Replace with the deployed host.

## Authentication

Every request carries the organisation's key in `X-API-Key`. Keys look like `gco_<8 characters>_<secret>`.
A key is shown **once**, when it is issued; GovCareerOS keeps only its SHA-256 and its prefix
(`gco_<8 characters>`), so a lost key is revoked and a new one issued. The examples read the key from an
environment variable; never put a key in a URL, a repository or a log.

```sh
export GCO_API_KEY='...'          # the key you were given; nothing in this file is a real key
export GCO=http://localhost:8080
```

| Status | Meaning |
|--------|---------|
| 401 | missing, unknown or revoked key (or the organisation is deactivated) |
| 403 | the key lacks the endpoint's scope |
| 429 | rate limit exceeded; wait `Retry-After` seconds |
| 422 | invalid parameter or cursor |

### Scopes

| Scope | Endpoints |
|-------|-----------|
| `notices:read` | `GET /api/v1/notices`, `GET /api/v1/notices/{id}` |
| `changes:read` | `GET /api/v1/changes` |
| `verify:use` | `POST /api/v1/verify` |
| `webhooks:manage` | `/api/v1/webhooks` (register, list, remove, delivery log) |

### Rate limit and metering

Each key has a per-minute limit (default 60, set per key when it is issued), enforced as a token bucket: up to
the limit in a burst, refilled continuously. The bucket lives in the API process's memory: it resets when the
process restarts, and with several API worker processes each keeps its own bucket.

Every answered request is counted per key, UTC day and endpoint (`api_usage` table) for billing. Refused
requests (401, 403, 404, 422, 429, ...) are not counted.

## Notices

```sh
# Open SSC notices, 50 per page
curl -s -H "X-API-Key: $GCO_API_KEY" "$GCO/api/v1/notices?source=SSC&status=open&limit=50"

# Next page: pass next_cursor back unchanged
curl -s -H "X-API-Key: $GCO_API_KEY" "$GCO/api/v1/notices?source=SSC&status=open&limit=50&cursor=<next_cursor>"

# Haryana (state) corrigenda changed since a time (UTC when no offset is given)
curl -s -H "X-API-Key: $GCO_API_KEY" \
  "$GCO/api/v1/notices?jurisdiction=IN-HR&kind=CORRIGENDUM&updated_since=2026-10-01T00:00:00Z"
```

Filters (all optional, combined with AND):

| Parameter | Values |
|-----------|--------|
| `source` | source name, e.g. `SSC`, `UPSC`, `HPSC` |
| `jurisdiction` | `IN` (Union bodies) or a state code such as `IN-HR` |
| `kind` | `VACANCY`, `CORRIGENDUM`, `EXAM_NOTICE`, `RESULT`, ... |
| `status` | `open`: the listing's closing date is today or later (IST); `closed`: before today. Notices whose listing states no closing date have `status: "unknown"` and match neither filter |
| `updated_since` | ISO 8601 time; the notice record changed at or after it (a re-crawl that sees the notice again counts) |
| `cursor`, `limit` | pagination; `limit` 1-200, default 50 |

Pages are ordered by when GovCareerOS first saw each notice; a cursor stays valid while new notices arrive.
`next_cursor` is `null` on the last page.

```sh
curl -s -H "X-API-Key: $GCO_API_KEY" "$GCO/api/v1/notices/<notice id>"
```

The notice detail adds `vacancies` and `exams` parsed from the notice's document: each with its own
`attribution` (source, official page, document URL, retrieval time, page) and `requirements`, each requirement with
`page` and `quote` (the document's own words) where the parser kept them. Both lists are empty until the
document has been read. Advertisements that GovCareerOS ingests directly from a source's advertisement listing
(UPSC) are not notice rows yet and so are not in this list.

## Change feed

```sh
# From the beginning
curl -s -H "X-API-Key: $GCO_API_KEY" "$GCO/api/v1/changes?limit=100"

# Poll: keep next_cursor and pass it as since
curl -s -H "X-API-Key: $GCO_API_KEY" "$GCO/api/v1/changes?since=<next_cursor>"

# Only official changes
curl -s -H "X-API-Key: $GCO_API_KEY" "$GCO/api/v1/changes?since=<next_cursor>&type=notice.changed"
```

Events, in the order they were recorded:

- `notice.new`: a notice first seen on an official source page; `data.notice` is the notice as `/notices` returns it.
- `notice.changed`: an official change (corrigendum, extension, re-opened window, revised schedule, cancellation,
  ...) to a vacancy or exam. `data.changes` lists each changed field with its old and new value, and for each the
  document URL, page and quoted words (`old_evidence`, `new_evidence`); `data.lines` is the same for people.

`next_cursor` is always set (equal to `since` when nothing new arrived). Events are recorded by
`scripts/dispatch_webhooks.py` after ingestion; an event never appears behind a cursor you already hold.

## Verify

The public notice check, behind a key: is this notice text, link or PDF on an official source? The link is
analysed as a string and never fetched.

```sh
curl -s -H "X-API-Key: $GCO_API_KEY" -F "url=https://www.upsc.gov.in/sites/default/files/AdvtNo-12-2026-Engl-250926_0.pdf" \
  "$GCO/api/v1/verify"
curl -s -H "X-API-Key: $GCO_API_KEY" -F "file=@notice.pdf;type=application/pdf" "$GCO/api/v1/verify"
curl -s -H "X-API-Key: $GCO_API_KEY" --data-urlencode "text@pasted_notice.txt" "$GCO/api/v1/verify"
```

PDFs up to 10 MB; larger bodies are refused with 413 before they are read.

## Webhooks

```sh
# Register (https on a public host only); the response holds the signing secret, once
curl -s -H "X-API-Key: $GCO_API_KEY" -H "Content-Type: application/json" \
  -d '{"url": "https://hooks.example.com/govcareeros", "events": ["notice.new", "notice.changed"]}' \
  "$GCO/api/v1/webhooks"

curl -s -H "X-API-Key: $GCO_API_KEY" "$GCO/api/v1/webhooks"
curl -s -H "X-API-Key: $GCO_API_KEY" "$GCO/api/v1/webhooks/<webhook id>/deliveries"
curl -s -X DELETE -H "X-API-Key: $GCO_API_KEY" "$GCO/api/v1/webhooks/<webhook id>"
```

- Only `https` URLs on public host names or public addresses are accepted. Loopback, private, link-local and
  other non-public addresses, `localhost`, single-label and `.local`/`.internal` names, and URLs with a user name
  or password are refused. At every delivery the host is resolved again and the delivery is refused (status
  `REFUSED`, never retried) when it resolves to a non-public address; otherwise the connection is opened to one of
  the addresses just checked (TLS still verifies the certificate for your host name). Redirects are not followed.
- Only events recorded after registration are delivered. At most 10 active webhooks per organisation.
- Body: `{"id": <delivery id>, "type": "notice.changed", "seq": 42, "cursor": "...", "created_at": "...", "data": {...}}`;
  `data` is the same as the change feed's. Headers: `X-GovCareerOS-Event`, `X-GovCareerOS-Delivery`,
  `X-GovCareerOS-Signature`.
- Answer with any 2xx within 10 seconds (from connecting to your status line and headers; the body of your answer
  is not read). Anything else is retried after 1 min, 5 min, 30 min, 2 h and 6 h, then the delivery is marked
  `FAILED`. Retries can reorder deliveries; use `seq` to order them.

### Checking the signature

`X-GovCareerOS-Signature: t=<unix seconds>,v1=<hex>`, where `v1` is HMAC-SHA256 with the webhook's secret over
`<t>` + `.` + the raw request body. Compare in constant time and reject old timestamps (replays):

```python
import hashlib, hmac, time

def valid(secret: str, header: str, body: bytes, tolerance: int = 300) -> bool:
    fields = dict(p.split("=", 1) for p in header.split(","))
    t = int(fields["t"])
    expected = hmac.new(secret.encode(), f"{t}.".encode() + body, hashlib.sha256).hexdigest()
    return abs(time.time() - t) <= tolerance and hmac.compare_digest(fields["v1"], expected)
```

## Operations

Issuing keys (reviewer accounts only; bearer token from `/auth/login`):

```sh
curl -s -H "Authorization: Bearer $GCO_REVIEWER_TOKEN" -H "Content-Type: application/json" \
  -d '{"name": "Example Careers Pvt Ltd", "contact_email": "api@example.com"}' "$GCO/admin/api/orgs"
curl -s -H "Authorization: Bearer $GCO_REVIEWER_TOKEN" -H "Content-Type: application/json" \
  -d '{"name": "production", "scopes": ["notices:read", "changes:read"], "rate_limit_per_minute": 120}' \
  "$GCO/admin/api/orgs/<org id>/keys"
curl -s -X POST -H "Authorization: Bearer $GCO_REVIEWER_TOKEN" "$GCO/admin/api/keys/<key id>/revoke"
curl -s -H "Authorization: Bearer $GCO_REVIEWER_TOKEN" "$GCO/admin/api/orgs/<org id>/usage?since=2026-10-01"
```

Creating organisations and issuing or revoking keys is recorded in the reviewer audit log.

Events and webhook deliveries (after the daily job, and every few minutes while retries are pending):

```powershell
$env:PYTHONPATH="C:\Users\krishna"
python govcareeros/scripts/dispatch_webhooks.py                 # record events, queue and send deliveries
python govcareeros/scripts/dispatch_webhooks.py --collect-only  # only fill the change feed
```
