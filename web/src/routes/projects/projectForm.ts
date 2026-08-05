/**
 * Project form validation and the three-state update body.
 *
 * `PUT /api/projects/{name}` reads an absent key as "keep", `""` as "clear" and
 * a value as "replace" — for `linear_api_key` and `conda_env` only. `name` and
 * `path` have `min_length=1` server-side, so `""` there is a 422, not a clear;
 * `default_flags` clears with `{}`; `auto_scan_filter` has no clear state at all
 * and is always replaced whole.
 *
 * The patch is built by **diffing against the server snapshot**, not from a
 * dirty-field registry. React Hook Form's `dirtyFields` reports a field that was
 * edited and changed back, and misses one changed programmatically; the diff is
 * the thing the user actually asked for either way.
 */
import type { AutoScanFilter, ProjectInfo } from '../../protocol/dto/projects';
import type { ProjectUpdateBody, ProjectWriteBody } from '../../api/projectRepository';
import {
  flagsSignature,
  flagsToWire,
  flagsValid,
  wireFlagsSignature,
  type FlagEntry,
} from './flagEntry';

/** Android `VALID_NAME` / iOS `matchesValidName`. */
const VALID_NAME = /^[A-Za-z0-9 ._-]+$/;

export interface ProjectFormValues {
  name: string;
  path: string;
  condaEnv: string;
  /** Always blank when the form opens in edit mode — the server never echoes it. */
  linearApiKey: string;
  clearLinearKey: boolean;
}

export interface ProjectFormState extends ProjectFormValues {
  flags: FlagEntry[];
  filter: AutoScanFilter;
}

export function nameError(name: string): string | null {
  if (name.trim() === '') return 'Name is required';
  if (!VALID_NAME.test(name)) return 'Use letters, digits, spaces, _-.';
  return null;
}

export function pathError(path: string): string | null {
  return path.trim() === '' ? 'Path is required' : null;
}

/**
 * Filter validity is checked even when the section is hidden. The defaults
 * satisfy it, so an unkeyed project never trips over a form it cannot see.
 */
export function filterError(filter: AutoScanFilter): string | null {
  if (filter.label_names.length === 0) return 'Select at least one trigger label';
  if (filter.state_names.length === 0) return 'Select at least one trigger state';
  if (filter.active_state.trim() === '') return 'Active state is required';
  if (filter.done_state.trim() === '') return 'Done state is required';
  if (filter.removal_label.trim() === '') return 'Removal label is required';
  return null;
}

export function canSave(state: ProjectFormState): boolean {
  return (
    nameError(state.name) === null &&
    pathError(state.path) === null &&
    flagsValid(state.flags) &&
    filterError(state.filter) === null
  );
}

/**
 * The Auto Scan section only matters once the project has a key — existing and
 * not being cleared, or freshly typed. Android `showAutoScanFilterSection`.
 */
export function showsFilterSection(
  state: Pick<ProjectFormValues, 'linearApiKey' | 'clearLinearKey'>,
  editing: ProjectInfo | null,
): boolean {
  if (state.linearApiKey.trim() !== '') return true;
  return !state.clearLinearKey && editing?.has_linear_key === true;
}

function sameFilter(a: AutoScanFilter, b: AutoScanFilter): boolean {
  return (
    a.active_state === b.active_state &&
    a.done_state === b.done_state &&
    a.removal_label === b.removal_label &&
    a.state_names.length === b.state_names.length &&
    a.state_names.every((value, index) => value === b.state_names[index]) &&
    a.label_names.length === b.label_names.length &&
    a.label_names.every((value, index) => value === b.label_names[index])
  );
}

/** `POST /api/projects`. Blank optionals are omitted rather than sent empty. */
export function buildProjectWrite(state: ProjectFormState): ProjectWriteBody {
  const body: ProjectWriteBody = {
    name: state.name.trim(),
    path: state.path.trim(),
    // Required on create, and an empty map is meaningful: it is what "this
    // project passes no flags" looks like on the wire.
    default_flags: flagsToWire(state.flags),
    auto_scan_filter: state.filter,
  };
  const key = state.linearApiKey.trim();
  if (key !== '') body.linear_api_key = state.linearApiKey;
  const conda = state.condaEnv.trim();
  if (conda !== '') body.conda_env = conda;
  return body;
}

/**
 * `PUT /api/projects/{name}` — only the keys that changed.
 *
 * Built by inserting keys conditionally rather than assigning `undefined`:
 * `JSON.stringify` would drop an `undefined` anyway, but `Object.hasOwn` is what
 * the wire test asserts, and an explicitly-absent key is the whole contract.
 */
export function buildProjectUpdate(
  snapshot: ProjectInfo,
  state: ProjectFormState,
): ProjectUpdateBody {
  const body: ProjectUpdateBody = {};

  const name = state.name.trim();
  if (name !== snapshot.name) body.name = name;

  const path = state.path.trim();
  if (path !== snapshot.path) body.path = path;

  // Compared untrimmed, matching both mobile forms: an API key is opaque, and
  // trimming one the user pasted with a stray space would silently store a
  // different secret than the one they see.
  if (state.clearLinearKey) {
    body.linear_api_key = '';
  } else if (state.linearApiKey !== '') {
    body.linear_api_key = state.linearApiKey;
  }

  const conda = state.condaEnv.trim();
  const currentConda = snapshot.conda_env ?? '';
  if (conda !== currentConda) body.conda_env = conda;

  if (flagsSignature(state.flags) !== wireFlagsSignature(snapshot.default_flags)) {
    body.default_flags = flagsToWire(state.flags);
  }

  if (!sameFilter(state.filter, snapshot.auto_scan_filter)) {
    body.auto_scan_filter = state.filter;
  }

  return body;
}

/**
 * The option list a chip group renders: everything discovered from Linear, then
 * anything already selected that discovery did not return.
 *
 * Order-stable and discovery-first, which is iOS's shape. It matters twice: the
 * form is usable before (or entirely without) a successful discovery call, and
 * the arrays the filter sends follow this order rather than set-iteration order,
 * so saving twice with no edits produces the same bytes.
 */
export function orderedUnion(...groups: readonly (readonly string[])[]): string[] {
  const seen = new Set<string>();
  const result: string[] = [];
  for (const group of groups) {
    for (const value of group) {
      if (value !== '' && !seen.has(value)) {
        seen.add(value);
        result.push(value);
      }
    }
  }
  return result;
}

/** Linear's documented state types, as the single glyph both mobile clients show. */
export function stateTypeGlyph(type: string): string {
  switch (type) {
    case 'backlog':
      return '◴';
    case 'unstarted':
      return '○';
    case 'started':
      return '◐';
    case 'completed':
      return '●';
    case 'canceled':
      return '⊘';
    case 'triage':
      return '△';
    default:
      return '·';
  }
}
