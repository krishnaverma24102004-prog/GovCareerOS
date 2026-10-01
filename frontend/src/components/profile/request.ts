/**
 * PATCH /profile with the 422 body kept intact.
 *
 * lib/api.ts flattens FastAPI validation errors into one message string; the editor needs each
 * error's location so it can be shown next to the field it belongs to. This uses the same base URL,
 * token storage and 401 handling as lib/api.ts and differs only in what it keeps from an error body.
 */
import { API_URL, ApiError, getToken, setToken } from "@/lib/api";
import type { ProfileFull, ProfilePatch, ValidationIssue } from "@/lib/types-profile-editor";

export class ProfileSaveError extends ApiError {
  constructor(status: number, message: string, public issues: ValidationIssue[]) {
    super(status, message);
  }
}

function issuesOf(body: unknown): ValidationIssue[] {
  const d = (body as { detail?: unknown } | null)?.detail;
  if (!Array.isArray(d)) return [];
  return d
    .filter((e): e is { loc?: unknown; msg?: unknown; type?: unknown } => typeof e === "object" && e !== null)
    .map((e) => ({
      loc: Array.isArray(e.loc) ? (e.loc as (string | number)[]) : [],
      msg: typeof e.msg === "string" ? e.msg : JSON.stringify(e.msg),
      type: typeof e.type === "string" ? e.type : undefined,
    }));
}

export async function patchProfile(body: ProfilePatch): Promise<ProfileFull> {
  const headers: Record<string, string> = { "Content-Type": "application/json" };
  const token = getToken();
  if (token) headers["Authorization"] = `Bearer ${token}`;
  let res: Response;
  try {
    res = await fetch(`${API_URL}/profile`, { method: "PATCH", headers, body: JSON.stringify(body) });
  } catch {
    throw new ApiError(0, `cannot reach the API at ${API_URL}`);
  }
  const json = await res.json().catch(() => null);
  if (!res.ok) {
    if (res.status === 401) setToken(null);
    const issues = issuesOf(json);
    const d = (json as { detail?: unknown } | null)?.detail;
    const message =
      typeof d === "string"
        ? d
        : issues.length
          ? `The API rejected ${issues.length} field${issues.length === 1 ? "" : "s"} (HTTP ${res.status}); see the messages next to them.`
          : `${res.status} ${res.statusText}`;
    throw new ProfileSaveError(res.status, message, issues);
  }
  return json as ProfileFull;
}

/** "personal_info.date_of_birth" or "education.0.degree" for an issue, without the leading "body". */
export function issuePath(issue: ValidationIssue): string {
  const loc = issue.loc[0] === "body" ? issue.loc.slice(1) : issue.loc;
  return loc.join(".");
}
