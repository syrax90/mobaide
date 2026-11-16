/**
 * The wizard's navigation state, in the URL.
 *
 * Only `step`, `mode` and the workspace selection live here. The option buckets
 * deliberately do not: there are around thirty of them, two are long free text
 * that would end up in browser history and any intermediary log, and keeping
 * them out means the no-leak invariant never depends on URL hygiene. The cost —
 * a reload inside `config` re-seeds the options from the last **successfully
 * launched** ones (`launchPrefs.ts`), or from the defaults where no launch of
 * that kind has happened, discarding whatever was being edited — is honest, and
 * is why the clamps below exist rather than a pretence of full restoration.
 */
import type { WorkspaceSummary } from '../../../api/workspaceRepository';
import {
  parseMode,
  validateCustomPath,
  type WorkspaceSelection,
  type WizardMode,
  type WizardStep,
} from './wizardState';

export interface WizardLocation {
  step: WizardStep;
  mode: WizardMode | null;
  selection: WorkspaceSelection;
}

const STEPS: readonly WizardStep[] = ['workspace', 'mode', 'config'];

export function parseWizardLocation(params: URLSearchParams): WizardLocation {
  const rawStep = params.get('step');
  const step: WizardStep =
    rawStep !== null && (STEPS as readonly string[]).includes(rawStep)
      ? (rawStep as WizardStep)
      : 'workspace';

  const rawMode = params.get('mode');
  const mode = rawMode === null ? null : parseMode(rawMode);

  // `project` is the pre-rename name, read so old bookmarks still resolve.
  const workspace = params.get('workspace') ?? params.get('project');
  const path = params.get('path');
  // `workspace` wins: the two are mutually exclusive, and a hand-edited URL
  // carrying both should resolve deterministically.
  const selection: WorkspaceSelection =
    workspace !== null && workspace !== ''
      ? { kind: 'stored', name: workspace }
      : path !== null && path !== ''
        ? { kind: 'customPath', path: path.trim() }
        : { kind: 'none' };

  return { step, mode, selection };
}

export function serializeWizardLocation(location: WizardLocation): URLSearchParams {
  const params = new URLSearchParams();
  params.set('step', location.step);
  if (location.mode !== null) params.set('mode', location.mode);
  if (location.selection.kind === 'stored') params.set('workspace', location.selection.name);
  if (location.selection.kind === 'customPath') params.set('path', location.selection.path);
  return params;
}

export interface ResolveInput {
  requested: WizardLocation;
  /** `undefined` while the workspace list is still loading. */
  workspaces: readonly WorkspaceSummary[] | undefined;
  isModeSelectable: (mode: WizardMode) => boolean;
}

/**
 * `null` when the requested location is already valid; otherwise the location to
 * replace it with.
 */
export function resolveWizardLocation(input: ResolveInput): WizardLocation | null {
  const { requested, workspaces } = input;
  const selection = requested.selection;

  // Nothing can be validated against a list that has not arrived. Without this,
  // every reload of a deep link would bounce to step one for the duration of one
  // request and lose the user's place.
  if (workspaces === undefined) return null;

  if (selection.kind === 'stored') {
    const known = workspaces.some((workspace) => workspace.name === selection.name);
    if (!known) return { step: 'workspace', mode: null, selection: { kind: 'none' } };
  }

  if (selection.kind === 'customPath' && validateCustomPath(selection.path) !== null) {
    // Keep the text so the inline error can explain why we came back here.
    return { step: 'workspace', mode: null, selection };
  }

  if (requested.step === 'workspace') {
    return requested.mode === null ? null : { ...requested, mode: null };
  }

  if (selection.kind === 'none') {
    return { step: 'workspace', mode: null, selection: { kind: 'none' } };
  }

  if (requested.step === 'mode') {
    return requested.mode === null ? null : { ...requested, mode: null };
  }

  // step === 'config'
  if (requested.mode === null || !input.isModeSelectable(requested.mode)) {
    return { step: 'mode', mode: null, selection };
  }
  return null;
}
