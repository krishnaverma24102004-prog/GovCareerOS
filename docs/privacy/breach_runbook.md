# Personal data breach runbook (DPDP Act 2023 s.8(6), DPDP Rules 2025)

A personal data breach is any unauthorised processing, accidental disclosure, acquisition, sharing, use, alteration,
destruction or loss of access to personal data that compromises its confidentiality, integrity or availability
(DPDP Act s.2(u)). Lost laptop with the database, leaked `.env` (and so `FIELD_ENCRYPTION_KEY`), a wrong user seeing
another user's profile, a backup copied somewhere it should not be: all count. When in doubt, treat it as a breach.

The Act requires intimation to the **Data Protection Board of India** and to **each affected Data Principal**. Under
the DPDP Rules 2025 the Board gets a first intimation without delay and a detailed report **within 72 hours** of
becoming aware (or longer if the Board allows on request). Check the current notified text of the Rules and the
Board's prescribed form/portal before filing; this runbook does not replace it.

## Roles

| Role | Who (fill in) | Does |
|---|---|---|
| Incident lead | Owner / operator of the deployment | Runs this runbook, decides it is a breach, owns the clock |
| Technical responder | Whoever administers the server and database | Contains, preserves evidence, rotates keys, fixes |
| Grievance officer | `PRIVACY_GRIEVANCE_CONTACT` | Writes to affected people, answers their questions |
| Scribe | Anyone not doing the above | Keeps the timeline (UTC and IST) in the incident log |

On a one-person deployment one person holds every role; the steps and the clock are the same.

## Clock

T0 = the moment anyone on the team becomes aware of a possible breach. Write it down first.

| By | Step |
|---|---|
| T0 + 1 h | Contain; preserve evidence; open the incident log |
| T0 + 6 h | Scope: which people, which data items, encrypted or not |
| Without delay (aim T0 + 24 h) | First intimation to the Board; intimation to each affected person |
| T0 + 72 h | Detailed report to the Board |
| After | Remediation, lessons, update this runbook and data_inventory.md |

## 1. Contain (technical responder)

- Stop the leak: take the exposed service off the network, revoke shared links, block the account or IP.
- Revoke all login tokens if accounts may be compromised:
  `UPDATE auth_tokens SET revoked_at = now() WHERE revoked_at IS NULL;`
- Rotate any exposed secret (database password, `NVIDIA_API_KEY`, SMTP password).
- If `FIELD_ENCRYPTION_KEY` may be exposed, treat category/caste and disability values as disclosed, generate a new
  key and re-encrypt (decrypt with the old key and write with the new one, the same way the migration
  `alembic/versions/e3b7a91c5d20_dpdp_encryption_and_erasure.py` does; there is no key-rotation script yet).
- Do not delete logs, database rows or files that show what happened. Copy them (read-only) to the incident folder.

## 2. Scope (incident lead + technical responder)

Answer, in the incident log:

- What happened, when it started, when it was found, when it was stopped.
- Which people: list user ids (not names) from the database.
- Which data items, using `docs/privacy/data_inventory.md`. Note whether the encrypted columns were exposed with or
  without the key; ciphertext without the key is a lower risk but is still reported.
- Likely consequences for the people (e.g. caste or disability disclosed, identity theft risk from DOB + address).
- `GET /privacy/export` for an affected user shows exactly what was held about them.

## 3. Intimate the Board (incident lead)

First intimation, without delay: description of the breach (nature, extent, timing, location) and its likely impact.

Detailed report within 72 hours:

1. Updated and detailed description of the breach and how it happened.
2. The facts, circumstances and reasons that led to it.
3. Measures taken or proposed to mitigate the risk.
4. Any findings about the person who caused it.
5. Remedial measures to stop it recurring.
6. A report on the intimations given to the affected people.

Keep a copy of everything filed and the acknowledgement in the incident folder.

## 4. Intimate each affected person (grievance officer)

Without delay, by the email on their account (send manually; the app does not send breach mail), in plain language,
English and Hindi:

- what happened, when, and which of their data items were involved;
- the likely consequences for them;
- what we have done and are doing about it;
- what they can do to protect themselves (e.g. watch for misuse of their certificates; change reused passwords);
- the grievance officer's contact, and that they may complain to the Data Protection Board of India.

Never say the harm "cannot" happen; say what is known and what is not.

## 5. Afterwards

- Root-cause note in the incident folder; fix, test, deploy.
- Update `docs/privacy/data_inventory.md` if data or storage changed.
- Keep the incident folder at least as long as the logs the Rules require to be kept (at least one year).
