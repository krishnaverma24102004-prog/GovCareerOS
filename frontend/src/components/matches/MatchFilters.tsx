"use client";
/**
 * Client-side filters over the /matches list: eligibility status (with counts) and deadline window.
 * Filtering never re-ranks: the backend order (eligibility first, then relevance) is kept.
 */
import type { Match } from "@/lib/types";
import type { DeadlineWindow, EligibilityFilter } from "@/lib/types-matches-relevance";

const STATUSES: [EligibilityFilter, string][] = [
  ["ALL", "All"],
  ["ELIGIBLE", "Eligible"],
  ["CONDITIONAL", "Conditional"],
  ["MANUAL_REVIEW", "Manual review"],
  ["INELIGIBLE", "Ineligible"],
];

const WINDOWS: [DeadlineWindow, string][] = [
  ["any", "Any deadline"],
  ["7", "Closing within 7 days"],
  ["30", "Closing within 30 days"],
  ["90", "Closing within 90 days"],
  ["none", "No deadline stated"],
  ["closed", "Already closed"],
];

const DAY_MS = 24 * 60 * 60 * 1000;

export function inDeadlineWindow(m: Match, w: DeadlineWindow, now: number): boolean {
  if (w === "any") return true;
  if (w === "closed") return !m.open_for_applications;
  if (w === "none") return !m.application_deadline;
  if (!m.application_deadline || !m.open_for_applications) return false;
  const t = new Date(m.application_deadline).getTime();
  return !isNaN(t) && t >= now && t - now <= Number(w) * DAY_MS;
}

export function matchesStatus(m: Match, s: EligibilityFilter): boolean {
  return s === "ALL" || m.eligibility_status === s;
}

export function MatchFilters({
  matches,
  status,
  onStatus,
  deadline,
  onDeadline,
  includeClosed,
  now,
}: {
  matches: Match[];
  status: EligibilityFilter;
  onStatus: (s: EligibilityFilter) => void;
  deadline: DeadlineWindow;
  onDeadline: (w: DeadlineWindow) => void;
  includeClosed: boolean;
  now: number;
}) {
  // Status counts respect the deadline window so the numbers match what a click would show
  const inWindow = matches.filter((m) => inDeadlineWindow(m, deadline, now));
  const count = (s: EligibilityFilter) => inWindow.filter((m) => matchesStatus(m, s)).length;
  return (
    <div className="mb-3 flex flex-wrap items-center gap-3">
      <div className="flex flex-wrap gap-1" role="group" aria-label="Filter by eligibility status">
        {STATUSES.map(([value, label]) => (
          <button
            key={value}
            onClick={() => onStatus(value)}
            aria-pressed={status === value}
            className={`rounded-full border px-3 py-1 text-xs ${
              status === value ? "border-blue-700 bg-blue-700 text-white" : "border-slate-300 bg-white text-slate-700 hover:bg-slate-50"
            }`}
          >
            {label} <span className="tabular-nums opacity-80">{count(value)}</span>
          </button>
        ))}
      </div>
      <label className="flex items-center gap-2 text-sm">
        <span className="text-slate-600">Deadline</span>
        <select
          value={deadline}
          onChange={(e) => onDeadline(e.target.value as DeadlineWindow)}
          className="rounded border border-slate-300 bg-white px-2 py-1 text-sm"
        >
          {WINDOWS.filter(([v]) => v !== "closed" || includeClosed).map(([v, label]) => (
            <option key={v} value={v}>
              {label}
            </option>
          ))}
        </select>
      </label>
    </div>
  );
}
