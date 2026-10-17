# Personal data inventory (DPDP Act 2023)

What GovCareerOS holds about a person, why, where, under which consent purpose, and what happens on withdrawal and
erasure. Purposes and their notice text: `backend/privacy/notice.py` (served at `GET /privacy/notice`). When a table
or column that holds personal data is added, update this file, the notice (new `NOTICE_VERSION` if the items change),
and `backend/privacy/rights.py` (export and erase). `tests/privacy/test_privacy.py` fails if a table keyed by
`users.id` / `profile_versions.id` is neither erased nor listed as retained.

## Data items

| Data | Where (table.column) | Purpose | At rest | On withdrawal | On erasure |
|---|---|---|---|---|---|
| Email, name, password hash | `users` | Account (contract), ALERTS (email) | Password: scrypt hash | Kept (account) | Email/name replaced, hash voided; anonymised row kept |
| Login sessions | `auth_tokens` (token hash, expiry) | Account security | SHA-256 of token | Kept | Deleted |
| Date of birth | `personal_info.date_of_birth` | ELIGIBILITY_CHECK | Plain | Not processed; kept until erased or cleared | Deleted |
| Category / caste | `personal_info.category` | ELIGIBILITY_CHECK | **AES-256-GCM** | Not processed | Deleted |
| Disability (PwBD flag, %, types) | `personal_info.is_pwbd`, `.disability_percentage`, `.disability_types` | ELIGIBILITY_CHECK | **AES-256-GCM** | Not processed | Deleted |
| Reservation / benefit certificates | `reservation_certificates` (`kind` **AES-256-GCM**; dates, authority plain) | ELIGIBILITY_CHECK | Mixed | Not processed | Deleted |
| Ex-serviceman and government service details | `personal_info.is_ex_serviceman`, `armed_forces_service_months`, `is_central_govt_employee`, `central_govt_service_start`, `is_state_govt_employee`, `current_pay_level`, `current_level_regular_since`, `organisation_cadre`, `last_deputation_end` | ELIGIBILITY_CHECK | Plain | Not processed | Deleted |
| Gender, citizenship, nationality | `personal_info` | ELIGIBILITY_CHECK | Plain | Not processed | Deleted |
| Contact, address, preferred locations, career interests | `personal_info` | RESUME, ALERTS | Plain | Kept | Deleted |
| Education, experience, projects, skills, certifications, languages, achievements | per-section tables, one row set per `profile_versions` row | ELIGIBILITY_CHECK, RESUME | Plain | Kept | Deleted |
| Uploaded documents (path only) | `user_documents` | OPERATOR_ASSISTED_APPLICATION | Plain | Kept | Rows deleted |
| Claims and their evidence | `profile_facts`, `profile_evidence` | ELIGIBILITY_CHECK, RESUME | `profile_facts.value` **AES-256-GCM** (a claim can name a certificate kind or category); evidence plain | Kept | Deleted |
| Eligibility results and evidence | `eligibility_results`, `eligibility_evidence`, `exam_eligibility_results`, `exam_eligibility_evidence` | ELIGIBILITY_CHECK | Evidence `requirement`, `profile_evidence`, `rule` **AES-256-GCM** (they quote DOB, category, PwBD %); verdicts, check names, outcomes, pages, requirement ids plain | Kept as history; no new runs | Deleted |
| Relevance scores, skill gaps | `match_scores`, `skill_gaps` | ELIGIBILITY_CHECK | `skill_gaps.requirement`, `requirement_quote`, `profile_has`, `next_step` **AES-256-GCM**; scores plain | Kept; no new runs | Deleted |
| Alerts and digests | `user_alerts`, `digest_runs` + files `storage/digests/<user_id>/` | ALERTS | `user_alerts.body` **AES-256-GCM** (an age-headroom alert names the category limit); titles, digest rows and digest files plain | Kept | Rows and folder deleted |
| Applications tracked | `applications`, `application_status_history` | OPERATOR_ASSISTED_APPLICATION / self-tracking | Plain | Kept | Deleted |
| Resumes | `resumes`, `resume_claims` + files `storage/resumes/<user_id>/` | RESUME | Plain | Kept | Rows and folder deleted |
| Study plans | `study_plans`, `study_plan_items` | Exam preparation | Plain | Kept | Deleted |
| Consent records | `consent_records` | Proof of consent (s.6(10)) | Plain | `withdrawn_at` set; row kept | **Retained** (see below) |

Profile versions are immutable: correcting data (`PATCH /profile`) writes a new version, and older versions stay
until erasure so earlier eligibility results remain reproducible. Clearing a field never needs consent.

## Encryption at rest

- Columns marked AES-256-GCM are encrypted in the application (`backend/privacy/crypto.py`) before they reach the
  database; for those columns the database, its backups and its logs only see `enc:v1:...` ciphertext. That covers
  the source values (category, PwBD flag / % / types, certificate kind) and every stored text that quotes them:
  profile claims, eligibility and exam-eligibility evidence, skill gaps and alert bodies
  (`tests/privacy/test_privacy.py` scans every text value of a person's rows for plaintext). The API returns
  plaintext to the account owner.
- Key: `FIELD_ENCRYPTION_KEY` (32 bytes, urlsafe base64) from the environment / `.env`, a `SecretStr` that is never
  printed or logged. Losing it makes those columns unreadable; store a copy offline, separately from backups.
- Existing plaintext, in the source columns and in the quoting texts, was encrypted by migration `e3b7a91c5d20`; it
  refuses to run without a key when such values exist.
- Because the values are encrypted, SQL cannot filter or sort on them.

What field encryption does not hide, so a dump without the key is still treated as exposing these:

- Links and verdicts: `eligibility_evidence.requirement_id` / `skill_gaps.requirement_id` point at the vacancy
  requirement a check was judged against, and for a category-specific age limit or reservation that official row
  names the category; ELIGIBLE / INELIGIBLE verdicts on reserved posts hint at it too.
- Files on disk: digests (`storage/digests/<user_id>/`) and resumes (`storage/resumes/<user_id>/`) are plaintext
  (they are what the person reads); they rely on disk and backup protection.
- Fields that are not encrypted at all: date of birth, gender, citizenship/nationality, ex-serviceman and service
  details, certificate dates and issuing authority, and free text the person types (change summaries, application
  notes, evidence descriptions).

## Withdrawal (`DELETE /privacy/consents/{purpose}`)

ELIGIBILITY_CHECK withdrawal is enforced in `backend/privacy/enforcement.py`: profile saves that would store (or
carry over) category, DOB, gender, citizenship/nationality, disability, certificates or service details return 403
`CONSENT_REQUIRED`; every eligibility computation (`engine.load_profile_snapshot`, so API runs, radar, digest, gaps,
relevance, resume builder) refuses; the radar and digest skip the person; the application checklist refuses. Stored data and past
results are kept and remain visible and exportable to the person; nothing is deleted silently. To delete, the person
clears the fields or erases the account.

ALERTS consent is required for every alert and digest: without it the radar still keeps eligibility results current
(under ELIGIBILITY_CHECK) but writes no alert, `digest.run_all` / `scripts/send_digests.py` write and send no digest,
`POST /digest/generate` and `digest.send_run` refuse with 403 `CONSENT_REQUIRED`.

RESUME consent is required to build a resume (`backend/resume/builder.py`, 403 `CONSENT_REQUIRED` otherwise); a
resume quotes only the items the RESUME purpose lists (name and contact, education, experience, skills/projects), so
date of birth and nationality are never put on it. A resume tailored to a vacancy also needs ELIGIBILITY_CHECK.

OPERATOR_ASSISTED_APPLICATION consent is recorded and withdrawable; no flow on this branch processes data under it
(the operator console must check it before it does).

## Under 18

If the date of birth shows the person is under 18, consent must name the parent / lawful guardian who gave it
(`consent_records.guardian_name`). Without it, saving that data, giving consent and eligibility runs all refuse with
`GUARDIAN_CONSENT_REQUIRED`. The guardian's identity is recorded as stated, not verified.

## Erasure (`POST /privacy/erase`, `scripts/privacy_erase.py`)

1. Request: the account is deactivated at once, every login token revoked, every consent withdrawn;
   `erasure_due_at` = now + `ERASURE_GRACE_DAYS` (default 30). The person can cancel within the grace period through
   the grievance officer (`privacy_erase.py cancel <email>`).
2. Hard erase, run daily: `python govcareeros/scripts/privacy_erase.py run` deletes every row listed above as
   "Deleted" and the per-user storage folders, and replaces the `users` row with an anonymised stub
   (`erased-<id>@erased.invalid`, no name, unusable password, `erased_at` set).

### Retained after erasure (exceptions)

| What | Why |
|---|---|
| `consent_records` for the person | Proof that consent was given and withdrawn (the Data Fiduciary bears that burden, s.6(10)); linked only to the anonymised stub |
| `review_audit_log`, `profile_facts.reviewer_id`, `extraction_drafts.reviewer_id`, `recruitment_notices.reviewer_id`, `consent_records.captured_by_id` | The person acting as reviewer or operator on other records; audit of decisions about official data, not data about them |
| Accounts with `users.legal_hold_reason` set | A legal obligation (court order, pending dispute, statutory retention) requires keeping the data; `privacy_erase.py hold/release`. The hard erase skips them and reports them |
| Database backups | Erased data remains in backups until they rotate out; restore procedures must re-run `privacy_erase.py run` |
