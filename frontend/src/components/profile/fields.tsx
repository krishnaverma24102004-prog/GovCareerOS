"use client";
/** Form building blocks shared by the personal and repeating-section editors. */
import { useId, type ReactNode } from "react";

import type { FieldKind } from "./sections";

/** API validation messages keyed by path, e.g. "education.0.degree" or "personal_info.date_of_birth". */
export type FieldErrors = Record<string, string[]>;

export const inputClass = (bad: boolean) =>
  `w-full rounded border px-2 py-1 text-sm ${bad ? "border-red-400 bg-red-50" : "border-slate-300 bg-white"}`;

export function FieldMessages({ messages }: { messages?: string[] }) {
  if (!messages?.length) return null;
  return (
    <span className="mt-0.5 block text-xs text-red-700" role="alert">
      {messages.join("; ")}
    </span>
  );
}

export function Field({
  label,
  required,
  errors,
  wide,
  children,
}: {
  label: string;
  required?: boolean;
  errors?: string[];
  wide?: boolean;
  children: ReactNode;
}) {
  return (
    <label className={`block text-sm ${wide ? "md:col-span-2" : ""}`}>
      <span className="text-slate-700">
        {label}
        {required && <span className="text-red-600"> *</span>}
      </span>
      {children}
      <FieldMessages messages={errors} />
    </label>
  );
}

export function TextInput({
  kind,
  value,
  onChange,
  errors,
  suggest,
}: {
  kind: FieldKind;
  value: string;
  onChange: (v: string) => void;
  errors?: string[];
  suggest?: string[];
}) {
  const listId = useId();
  const bad = !!errors?.length;
  if (kind === "textarea") {
    return <textarea className={inputClass(bad)} rows={3} value={value} aria-invalid={bad} onChange={(e) => onChange(e.target.value)} />;
  }
  return (
    <>
      <input
        className={inputClass(bad)}
        type={kind === "number" ? "number" : kind === "date" ? "date" : kind === "url" ? "url" : "text"}
        step={kind === "number" ? "any" : undefined}
        value={value}
        aria-invalid={bad}
        list={suggest ? listId : undefined}
        onChange={(e) => onChange(e.target.value)}
      />
      {suggest && (
        <datalist id={listId}>
          {suggest.map((o) => (
            <option key={o} value={o} />
          ))}
        </datalist>
      )}
    </>
  );
}
