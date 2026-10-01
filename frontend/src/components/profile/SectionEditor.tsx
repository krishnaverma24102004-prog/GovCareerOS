"use client";
/** Editor for one repeating section (education, experience, ...): add, edit and remove rows. */
import { Field, FieldMessages, TextInput, type FieldErrors } from "./fields";
import { emptyRow, type FormRow, type SectionSpec } from "./sections";

export function SectionEditor({
  spec,
  rows,
  dirty,
  errors,
  onChange,
  onReset,
}: {
  spec: SectionSpec;
  rows: FormRow[];
  dirty: boolean;
  errors: FieldErrors;
  /** structural = rows were added or removed, so index-based API errors no longer line up. */
  onChange: (rows: FormRow[], structural: boolean) => void;
  onReset: () => void;
}) {
  const setField = (i: number, name: string, value: string) =>
    onChange(
      rows.map((r, j) => (j === i ? { ...r, [name]: value } : r)),
      false,
    );

  return (
    <fieldset className="rounded border border-slate-200 bg-white p-4">
      <legend className="flex items-center gap-2 px-1 font-medium">
        {spec.title}
        <span className="text-xs font-normal text-slate-500">({rows.length})</span>
        {dirty && <span className="rounded bg-amber-100 px-1.5 py-0.5 text-xs font-normal text-amber-800">edited</span>}
      </legend>
      <FieldMessages messages={errors[spec.key]} />

      {rows.length === 0 && <p className="text-sm text-slate-500">No {spec.title.toLowerCase()} added.</p>}

      <ol className="space-y-4">
        {rows.map((row, i) => (
          <li key={row._key} className="rounded border border-slate-100 bg-slate-50 p-3">
            <div className="mb-2 flex items-center justify-between">
              <span className="text-xs font-medium uppercase tracking-wide text-slate-500">
                {spec.singular} {i + 1}
              </span>
              <button
                type="button"
                onClick={() => onChange(rows.filter((_, j) => j !== i), true)}
                className="text-xs text-red-700 hover:underline"
              >
                Remove
              </button>
            </div>
            <FieldMessages messages={errors[`${spec.key}.${i}`]} />
            <div className="grid gap-3 md:grid-cols-2">
              {spec.fields.map((f) => {
                const errs = errors[`${spec.key}.${i}.${f.name}`];
                return (
                  <Field key={f.name} label={f.label} required={f.required} errors={errs} wide={f.wide}>
                    <TextInput kind={f.kind} value={row[f.name] ?? ""} suggest={f.suggest} errors={errs} onChange={(v) => setField(i, f.name, v)} />
                  </Field>
                );
              })}
            </div>
          </li>
        ))}
      </ol>

      <div className="mt-3 flex gap-3">
        <button
          type="button"
          onClick={() => onChange([...rows, emptyRow(spec)], true)}
          className="rounded border border-slate-300 px-3 py-1 text-sm hover:bg-slate-100"
        >
          Add {spec.singular}
        </button>
        {dirty && (
          <button type="button" onClick={onReset} className="text-sm text-slate-600 hover:underline">
            Undo changes to {spec.title.toLowerCase()}
          </button>
        )}
      </div>
    </fieldset>
  );
}
