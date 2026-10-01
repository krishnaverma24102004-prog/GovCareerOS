/**
 * What changed between two profile versions.
 *
 * Every version stores its own copy of each row (new ids), so rows are compared by content: identical
 * rows are unchanged, rows with the same identity fields (e.g. degree + institution) but other differences
 * are "changed" with a field list, and the rest are added or removed.
 */
import type { ListSectionKey, ProfileFull } from "@/lib/types-profile-editor";

import { PERSONAL_FIELDS, PERSONAL_LABELS, SECTIONS, sectionRows, type SectionSpec } from "./sections";

export interface FieldChange {
  field: string;
  label: string;
  before: unknown;
  after: unknown;
}

export interface RowChange {
  label: string;
  fields: FieldChange[];
}

export interface SectionDiff {
  key: ListSectionKey;
  title: string;
  added: string[];
  removed: string[];
  changed: RowChange[];
}

export interface ProfileDiff {
  personal: FieldChange[];
  sections: SectionDiff[];
  empty: boolean;
}

const norm = (v: unknown) => (v == null ? "" : String(v).trim());

function signature(spec: SectionSpec, row: Record<string, unknown>, fields: string[] = spec.fields.map((f) => f.name)): string {
  return JSON.stringify(fields.map((f) => norm(row[f])));
}

function diffSection(spec: SectionSpec, before: Record<string, unknown>[], after: Record<string, unknown>[]): SectionDiff {
  const leftB = [...before];
  const leftA: Record<string, unknown>[] = [];
  for (const a of after) {
    const i = leftB.findIndex((b) => signature(spec, b) === signature(spec, a));
    if (i >= 0) leftB.splice(i, 1);
    else leftA.push(a);
  }
  const changed: RowChange[] = [];
  const added: string[] = [];
  for (const a of leftA) {
    const i = leftB.findIndex((b) => signature(spec, b, spec.identity) === signature(spec, a, spec.identity));
    if (i < 0) {
      added.push(spec.label(a));
      continue;
    }
    const [b] = leftB.splice(i, 1);
    changed.push({
      label: spec.label(a),
      fields: spec.fields
        .filter((f) => norm(a[f.name]) !== norm(b[f.name]))
        .map((f) => ({ field: f.name, label: f.label, before: b[f.name], after: a[f.name] })),
    });
  }
  return { key: spec.key, title: spec.title, added, removed: leftB.map(spec.label), changed };
}

export function diffProfiles(before: ProfileFull | null, after: ProfileFull): ProfileDiff {
  const pb = (before?.personal_info ?? {}) as Record<string, unknown>;
  const pa = (after.personal_info ?? {}) as Record<string, unknown>;
  const personal = PERSONAL_FIELDS.filter((k) => norm(pb[k]) !== norm(pa[k])).map((k) => ({
    field: k,
    label: PERSONAL_LABELS[k],
    before: pb[k],
    after: pa[k],
  }));
  const sections = SECTIONS.map((spec) => diffSection(spec, sectionRows(before, spec.key), sectionRows(after, spec.key))).filter(
    (d) => d.added.length || d.removed.length || d.changed.length,
  );
  return { personal, sections, empty: personal.length === 0 && sections.length === 0 };
}
