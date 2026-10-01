# certificate_cases.json

Research data for the planned certificate-validity rules (OBC non-creamy layer, EWS income & assets,
SC/ST, PwBD, ex-servicemen, domicile). It is reference material only: no engine code reads it yet.
Every entry is a real court decision or official document. The quotes were checked word for word
against the text of the cited source, as downloaded on 2026-10-01. The check ignores whitespace,
letter case and curly versus straight quote/dash characters, because PDF line breaks and typography
differ from source to source.

Eligibility decisions stay deterministic. These cases are meant to back test expectations and to
show which rule a court applied. They are not meant to be fed to an LLM.

## Top level

| key | meaning |
|---|---|
| `description` | one-line summary |
| `compiled_on` | date the sources were read (ISO) |
| `cases` | list of decided cases (below) |
| `rules` | general rules with their source (below) |

## `cases[]`

| field | meaning |
|---|---|
| `id` | stable slug: `<court>-<year>-<party>` |
| `court` | court or tribunal (bench size where stated) |
| `case_name` | parties. Where one judgment decides several candidates differently, each candidate is a separate case. |
| `case_number` | case number / neutral citation / reporter citation as stated; `null` if the source does not give one |
| `date` | date of the judgment (ISO). `null` when the fetched source did not state it (see `uncertain_reason`) |
| `citation_url` | where the text was read: the judgment itself, or a legal-news report of it |
| `source_kind` | `judgment` (judgment/order text read) or `news_report` (LiveLaw, SCC Times etc.; judgment PDF not read) |
| `certificate_type` | one of `obc_ncl`, `ews`, `sc_st`, `pwbd`, `ex_serviceman`, `domicile` |
| `recruitment` | examination / advertisement the dispute arose from |
| `facts.certificate_issue_dates` | issue dates of the certificate(s) the candidate held, ISO, as stated; empty if not stated |
| `facts.certificate_financial_year` | FY the certificate's income related to, e.g. `2019-20`; `null` if not stated |
| `facts.required_financial_year` | FY the rules/advertisement required; `null` if not stated |
| `facts.cut_off_date` | last date of application or other crucial date (ISO) |
| `facts.advertisement_date` | advertisement / notice / rules date (ISO) when stated |
| `facts.notes` | other facts the decision turned on |
| `rule_applied` | the rule, OM or precedent the court applied |
| `outcome` | `accepted` (certificate/claim upheld for the candidate) or `rejected` (category benefit denied) |
| `outcome_note` | the relief actually granted (e.g. accepted but no relief given because of delay) |
| `quote` | verbatim excerpt (25 words max) from `citation_url` supporting the outcome or the key fact |
| `uncertain` | `true` when some field could not be confirmed from the source read |
| `uncertain_reason` | present when `uncertain` is true |

## `rules[]`

| field | meaning |
|---|---|
| `id` | stable slug |
| `certificate_type` | as above, or `all`; `applies_to` lists several types when one rule covers more than one |
| `jurisdiction` | present only when the rule is not a Central Government rule (e.g. `Rajasthan`, `NCT of Delhi`) |
| `issuer` | DoPT, MHA, a State department, or a court where the rule is judge-made |
| `reference` | OM / notification number and date, or judgment and paragraph |
| `rule` | plain statement of the rule |
| `source_url` | official document URL (doptcirculars.nic.in, ncbc.nic.in), or the judgment that states or reproduces it |
| `source_kind` | `official_document` or `judgment` |
| `quote` | verbatim excerpt (25 words max) from `source_url` |
| `uncertain` / `uncertain_reason` | as above, e.g. the official text was read only as reproduced in a judgment |

## Known conflicts in the data

- OBC-NCL issue windows. Sakshi Arha (SC, 2025) and the Jharkhand HC batch enforce the cut-off strictly.
  Raghvendra Singh, Bharath Singarappa, Amjad Khan (Delhi HC) and Rajat Ghosh (Tripura HC) accept late
  or out-of-window certificates where OBC status already existed or the DoPT 08.10.2015 / 29.03.2023 OMs
  apply. A rule engine should report `UNKNOWN`/needs-review rather than `NOT_MET` in these situations.
- EWS financial year. The Supreme Court (Divya, Gaurav Singh, Poonam Dwivedi) treats a wrong FY as going
  to the root of eligibility. Kadam Kiran Sanjay (Delhi HC) accepted a preceding-FY certificate where the
  advertisement was ambiguous.
- PwBD temporary certificates. The DoPT O.M. dated 19.05.2023 (as reproduced) is against them. The CAT in
  Samit Kumar held that a valid certificate with an expiry date cannot be ignored.
