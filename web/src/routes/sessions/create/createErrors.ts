/**
 * Launch failures the wizard can act on, rather than just print.
 *
 * Ported from Android `CreateSessionViewModel`'s catch chain, in its order.
 * iOS's variants are looser — a substring search for the Codex marker, and a
 * detail extractor that falls back to the raw body — so Android's is the one
 * mirrored here.
 */
import { isApiError } from '../../../api/errors';

export type CreateFailureKind =
  | 'codexAuthRequired'
  | 'agentUpdateRequired'
  | 'customTypeDeleted'
  | 'generic';

export interface CreateFailure {
  kind: CreateFailureKind;
  message: string;
}

function asRecord(value: unknown): Record<string, unknown> | null {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : null;
}

function markerAt(body: Record<string, unknown> | null, marker: string): boolean {
  // `Object.hasOwn`, not a bare index: the body is wire data, and a marker name
  // colliding with an `Object.prototype` key would otherwise resolve to an
  // inherited function and read as truthy.
  return body !== null && Object.hasOwn(body, marker) && body[marker] === true;
}

/**
 * Whether an error body carries a marker.
 *
 * The same marker appears at two depths depending on the axis: the relay puts it
 * top-level, while the agent's own REST route raises `HTTPException` with a dict
 * `detail` and FastAPI nests it. A browser only ever takes the relay path, but
 * checking both costs nothing and keeps this honest against a direct-agent
 * deployment.
 *
 * `agent_update_required` is the exception — it is relay-authored only, and
 * `_ProxyRejection` exists specifically to keep it unwrapped.
 */
export function hasErrorMarker(
  body: unknown,
  marker: string,
  options?: { topLevelOnly?: boolean },
): boolean {
  const top = asRecord(body);
  if (markerAt(top, marker)) return true;
  if (options?.topLevelOnly === true) return false;
  return markerAt(asRecord(top?.detail), marker);
}

/** The `detail` string, or `null` when `detail` is absent or not a string. */
export function errorDetailString(body: unknown): string | null {
  const record = asRecord(body);
  if (record === null) return null;
  const detail = record.detail;
  if (typeof detail === 'string') return detail;
  const nested = asRecord(detail)?.detail;
  return typeof nested === 'string' ? nested : null;
}

function describe(error: unknown): string {
  if (isApiError(error)) return error.message;
  return error instanceof Error ? error.message : String(error);
}

export function classifyCreateError(
  error: unknown,
  request: { cli?: string | null },
): CreateFailure {
  const body = isApiError(error) ? error.body : undefined;

  if (hasErrorMarker(body, 'codex_auth_required')) {
    // Driven by the marker, not the status: the server answers 409, not 401, so
    // nothing in the auth layer can intercept it first.
    return {
      kind: 'codexAuthRequired',
      message: errorDetailString(body) ?? 'Codex is not signed in on this agent.',
    };
  }

  if (hasErrorMarker(body, 'agent_update_required', { topLevelOnly: true })) {
    return {
      kind: 'agentUpdateRequired',
      message: 'Update your agent to launch custom agent types',
    };
  }

  if (
    isApiError(error) &&
    error.status === 404 &&
    request.cli?.startsWith('custom_') === true &&
    errorDetailString(body) === 'Custom agent type not found'
  ) {
    // Keyed on the exact detail string, not the status alone: a custom launch
    // also 404s when the *project* was deleted concurrently, and that must fall
    // through to the generic message rather than blaming the agent type.
    return { kind: 'customTypeDeleted', message: 'This agent type was deleted' };
  }

  return { kind: 'generic', message: `Failed to create session: ${describe(error)}` };
}

/**
 * Auto Scan's start errors. iOS types these; Android swallows them into one
 * generic string, which tells the user nothing about what to do next.
 */
export function classifyAutoScanError(error: unknown): string {
  if (isApiError(error)) {
    if (error.status === 409) return 'A scan is already running for this project.';
    if (error.status === 429) return 'The agent is at its auto-scan limit.';
    if (error.status === 404) return error.message;
  }
  return `Failed to start Auto Scan: ${describe(error)}`;
}

export function codexLoginErrorMessage(error: unknown): string {
  const detail = isApiError(error) ? errorDetailString(error.body) : null;
  if (detail !== null) return detail;
  if (isApiError(error)) return `Login failed (HTTP ${String(error.status)})`;
  return describe(error);
}

/** Inline copy for the custom-agent-type editor: the server's own 409 text is already good. */
export function customTypeErrorMessage(error: unknown): string {
  const detail = isApiError(error) ? errorDetailString(error.body) : null;
  return detail ?? describe(error);
}
