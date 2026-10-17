"use client";
/**
 * Personal section, including the relaxation questions the eligibility engine needs. The whole section
 * is sent when it changes, because a sent section replaces the stored one.
 */
import type { Citizenship } from "@/lib/types";
import type { EsmBranch, EsmReleaseType, PersonalInfoFull } from "@/lib/types-profile-editor";

import { Field, FieldMessages, TextInput, inputClass, type FieldErrors } from "./fields";

const CITIZENSHIP: [Citizenship, string][] = [
  ["INDIAN_CITIZEN", "Citizen of India"],
  ["NEPAL_SUBJECT", "Subject of Nepal"],
  ["BHUTAN_SUBJECT", "Subject of Bhutan"],
  ["TIBETAN_REFUGEE_PRE_1962", "Tibetan refugee who came before 1 Jan 1962"],
  ["PIO_MIGRANT", "Person of Indian origin who migrated with intent to settle"],
  ["OTHER", "Other"],
];

const CATEGORIES = ["UR", "EWS", "OBC", "SC", "ST"];

const BRANCHES: [EsmBranch, string][] = [
  ["ARMY", "Army"],
  ["NAVY", "Navy"],
  ["AIR_FORCE", "Air Force"],
  ["TERRITORIAL_ARMY", "Territorial Army"],
];
// The first five are the release routes the notices' definition of "ex-serviceman" lists
const RELEASE_TYPES: [EsmReleaseType, string][] = [
  ["RETIRED_WITH_PENSION", "Retired / relieved / discharged after earning pension"],
  ["MEDICAL_DISABILITY_PENSION", "Relieved on medical grounds with medical/disability pension"],
  ["REDUCTION_IN_ESTABLISHMENT", "Released as a result of reduction in establishment"],
  ["COMPLETED_ENGAGEMENT_GRATUITY", "Released after the term of engagement, with gratuity"],
  ["RECRUIT_BOARDED_OUT_MEDICAL", "Ex-recruit boarded out on medical grounds with disability pension"],
  ["OWN_REQUEST_WITHOUT_PENSION", "Left at own request without pension"],
  ["DISMISSED", "Dismissed"],
  ["DISCHARGED_MISCONDUCT_INEFFICIENCY", "Discharged for misconduct or inefficiency"],
  ["STILL_SERVING", "Still serving (give the expected release date)"],
  ["OTHER", "Other"],
];

type Tri = "" | "yes" | "no";
const toTri = (v: boolean | null | undefined): Tri => (v == null ? "" : v ? "yes" : "no");
const fromTri = (v: string): boolean | null => (v === "" ? null : v === "yes");
const strOrNull = (v: string): string | null => (v.trim() === "" ? null : v);
const numOrRaw = (v: string): number | string | null => {
  if (v.trim() === "") return null;
  const n = Number(v);
  return Number.isFinite(n) ? n : v;
};

type BoolKey =
  | "is_pwbd"
  | "is_ex_serviceman"
  | "is_central_govt_employee"
  | "is_state_govt_employee"
  | "esm_civil_post_secured"
  | "esm_reservation_used"
  | "esm_undertaking_given"
  | "is_agniveer";
type ChoiceKey = "esm_service_branch" | "esm_release_type" | "esm_civil_post_group";

export function PersonalEditor({
  value,
  dirty,
  errors,
  onChange,
  onReset,
}: {
  value: PersonalInfoFull;
  dirty: boolean;
  errors: FieldErrors;
  onChange: (v: PersonalInfoFull) => void;
  onReset: () => void;
}) {
  const set = <K extends keyof PersonalInfoFull>(k: K, v: PersonalInfoFull[K]) => onChange({ ...value, [k]: v });
  const err = (k: keyof PersonalInfoFull) => errors[`personal_info.${k}`];

  const text = (k: keyof PersonalInfoFull, label: string, kind: "text" | "date" | "number" | "textarea" = "text", wide = false) => (
    <Field label={label} errors={err(k)} wide={wide}>
      <TextInput
        kind={kind}
        value={value[k] == null ? "" : String(value[k])}
        errors={err(k)}
        onChange={(v) => set(k, (kind === "number" ? numOrRaw(v) : strOrNull(v)) as never)}
      />
    </Field>
  );
  const question = (k: BoolKey, label: string) => (
    <Field label={label} errors={err(k)}>
      <select className={inputClass(!!err(k)?.length)} value={toTri(value[k])} onChange={(e) => set(k, fromTri(e.target.value))}>
        <option value="">Not answered</option>
        <option value="yes">Yes</option>
        <option value="no">No</option>
      </select>
    </Field>
  );

  const choice = (k: ChoiceKey, label: string, options: [string, string][]) => (
    <Field label={label} errors={err(k)}>
      <select className={inputClass(!!err(k)?.length)} value={value[k] ?? ""} onChange={(e) => set(k, (e.target.value || null) as never)}>
        <option value="">Not answered</option>
        {options.map(([v, l]) => (
          <option key={v} value={v}>
            {l}
          </option>
        ))}
      </select>
    </Field>
  );
  const esmShown =
    !!value.is_ex_serviceman || !!value.esm_release_type || !!value.esm_release_date || !!err("esm_release_date") || !!err("esm_release_type");

  return (
    <fieldset className="space-y-4 rounded border border-slate-200 bg-white p-4">
      <legend className="flex items-center gap-2 px-1 font-medium">
        Personal details
        {dirty && <span className="rounded bg-amber-100 px-1.5 py-0.5 text-xs font-normal text-amber-800">edited</span>}
      </legend>
      <FieldMessages messages={errors["personal_info"]} />
      <div className="grid gap-3 md:grid-cols-2">
        {text("full_name", "Full name")}
        {text("date_of_birth", "Date of birth", "date")}
        {text("gender", "Gender")}
        <Field label="Category" errors={err("category")}>
          <select className={inputClass(!!err("category")?.length)} value={value.category ?? ""} onChange={(e) => set("category", strOrNull(e.target.value))}>
            <option value="">Not answered</option>
            {CATEGORIES.map((c) => (
              <option key={c} value={c}>
                {c}
              </option>
            ))}
            {value.category && !CATEGORIES.includes(value.category) && <option value={value.category}>{value.category}</option>}
          </select>
        </Field>
        {text("nationality", "Nationality")}
        {text("contact", "Contact (phone / email)")}
        {text("address", "Address", "textarea", true)}
      </div>

      <div className="grid gap-3 border-t border-slate-200 pt-3 md:grid-cols-2">
        <p className="text-sm font-medium md:col-span-2">Relaxation questions</p>
        <Field label="Citizenship" errors={err("citizenship_status")}>
          <select
            className={inputClass(!!err("citizenship_status")?.length)}
            value={value.citizenship_status ?? ""}
            onChange={(e) => set("citizenship_status", (e.target.value || null) as Citizenship | null)}
          >
            <option value="">Not answered</option>
            {CITIZENSHIP.map(([v, l]) => (
              <option key={v} value={v}>
                {l}
              </option>
            ))}
          </select>
        </Field>
        {question("is_pwbd", "Person with benchmark disability (PwBD)?")}
        {(value.is_pwbd || value.disability_percentage != null || err("disability_percentage")) &&
          text("disability_percentage", "Disability percentage", "number")}
        {(value.is_pwbd || value.disability_types || err("disability_types")) &&
          text("disability_types", "Disability sub-category codes (e.g. OL or LV,HH)")}
        {question("is_ex_serviceman", "Ex-serviceman?")}
        {(value.is_ex_serviceman || value.armed_forces_service_months != null || err("armed_forces_service_months")) &&
          text("armed_forces_service_months", "Armed forces service (months; if empty, counted from the enrolment and release dates below)", "number")}
        {esmShown && (
          <>
            {choice("esm_service_branch", "Service branch", BRANCHES)}
            {text("esm_rank", "Rank")}
            {text("esm_enrolment_date", "Enrolled on", "date")}
            {text("esm_release_date", "Released on (or expected end of engagement if still serving)", "date")}
            {choice("esm_release_type", "How you left the Armed Forces", RELEASE_TYPES)}
            {question("esm_civil_post_secured", "Since release, hold a regular civil post under the Central Govt?")}
            {(value.esm_civil_post_secured || value.esm_reservation_used != null) && (
              <>
                {question("esm_reservation_used", "Was that post secured through the ex-servicemen reservation?")}
                {choice("esm_civil_post_group", "Group of that civil post", [["A", "Group A"], ["B", "Group B"], ["C", "Group C"], ["D", "Group D"]])}
                {question("esm_undertaking_given", "On joining, gave the employer a self-declaration of earlier applications (DoP&T OM 14.08.2014)?")}
              </>
            )}
          </>
        )}
        {question("is_agniveer", "Served as an Agniveer (Agnipath scheme)?")}
        {question("is_central_govt_employee", "Central Government employee?")}
        {(value.is_central_govt_employee || value.central_govt_service_start || err("central_govt_service_start")) &&
          text("central_govt_service_start", "Continuous Central Govt service since", "date")}
        {question("is_state_govt_employee", "State Government employee?")}
        {(value.is_central_govt_employee || value.is_state_govt_employee || value.current_pay_level != null ||
          err("current_pay_level")) && (
          <>
            {text("current_pay_level", "Pay-matrix level held on regular basis (not ACP/MACP)", "number")}
            {text("current_level_regular_since", "Regular service in that level since", "date")}
            {text("organisation_cadre", "Organisation / cadre")}
            {text("last_deputation_end", "Returned from last deputation on (if any)", "date")}
          </>
        )}
      </div>

      <div className="grid gap-3 border-t border-slate-200 pt-3 md:grid-cols-2">
        <p className="text-sm font-medium md:col-span-2">Preferences</p>
        {text("preferred_locations", "Preferred locations (comma separated)")}
        {text("career_interests", "Career interests (comma separated)")}
      </div>

      {dirty && (
        <button type="button" onClick={onReset} className="text-sm text-slate-600 hover:underline">
          Undo changes to personal details
        </button>
      )}
    </fieldset>
  );
}
