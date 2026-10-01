/**
 * Which parts of the official notification each relevance component reads, as citations.
 *
 * The relevance engine (backend/relevance/scoring.py + service.py) reads these vacancy inputs:
 *   education  - EDUCATION requirements, DESIRABLE qualifications
 *   skills     - DUTIES, DESIRABLE, EDUCATION and EXPERIENCE requirements
 *   experience - EXPERIENCE requirements, DUTIES and the post name
 *   domain     - post name, department, ministry, DUTIES
 *   location   - headquarters
 *   role       - post name
 * Requirements carry a page in the notification; vacancy fields carry their own provenance
 * (document URL, page, section, quote). Nothing here is inferred beyond what the backend stored.
 */
import type { Provenance, VacancyDetail } from "@/lib/types";
import type { OfficialCitation } from "@/lib/types-matches-relevance";

const REQUIREMENT_TYPES: Record<string, string[]> = {
  education: ["EDUCATION", "DESIRABLE"],
  skills: ["DUTIES", "DESIRABLE", "EDUCATION", "EXPERIENCE"],
  experience: ["EXPERIENCE", "DUTIES"],
  domain: ["DUTIES"],
  location: [],
  role: [],
};

const VACANCY_FIELDS: Record<string, string[]> = {
  education: [],
  skills: [],
  experience: ["post_name"],
  domain: ["post_name", "department", "ministry"],
  location: ["headquarters"],
  role: ["post_name"],
};

/** PDF viewers open at a page with the #page=N fragment. */
export function pageLink(url: string | null, page: number | string | null): string | null {
  if (!url) return null;
  return page ? `${url}#page=${page}` : url;
}

/**
 * Requirements do not carry their own document URL. They come from the vacancy's notification, so
 * link to notification_url; if that is missing but every field provenance points at one single
 * document, that is the document the requirements were parsed from.
 */
function requirementDocumentUrl(v: VacancyDetail): string | null {
  if (v.notification_url) return v.notification_url;
  const urls = new Set(v.provenance.map((p) => p.document_url).filter((u): u is string => !!u));
  return urls.size === 1 ? [...urls][0] : null;
}

function shorten(text: string, max = 220): string {
  const t = text.replace(/\s+/g, " ").trim();
  return t.length > max ? `${t.slice(0, max - 1)}…` : t;
}

function fromProvenance(p: Provenance, fallbackUrl: string | null): OfficialCitation {
  return {
    key: `field-${p.field_name}-${p.raw_document_id}`,
    label: p.field_name.replace(/_/g, " "),
    text: shorten(p.excerpt || p.value || ""),
    page: p.page,
    section: p.section,
    url: p.document_url ?? fallbackUrl,
  };
}

export function citationsFor(component: string, v: VacancyDetail): OfficialCitation[] {
  const reqUrl = requirementDocumentUrl(v);
  const types = REQUIREMENT_TYPES[component] ?? [];
  const fields = VACANCY_FIELDS[component] ?? [];
  const reqs: OfficialCitation[] = v.requirements
    .filter((r) => types.includes(r.req_type))
    .sort((a, b) => types.indexOf(a.req_type) - types.indexOf(b.req_type))
    .map((r) => ({
      key: `req-${r.id}`,
      label: r.req_type.toLowerCase() + (r.is_mandatory ? "" : " (desirable)"),
      text: shorten(r.value),
      page: r.source_page,
      section: r.source_section,
      url: reqUrl,
    }));
  const provs = v.provenance.filter((p) => fields.includes(p.field_name)).map((p) => fromProvenance(p, v.notification_url));
  return [...reqs, ...provs];
}
