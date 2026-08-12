/**
 * The wizard's navigation state, in the URL.
 *
 * Only `step`, `mode` and the project selection live here. The option buckets
 * deliberately do not: there are around thirty of them, two are long free text
 * that would end up in browser history and any intermediary log, and keeping
 * them out means the no-leak invariant never depends on URL hygiene. The cost —
 * a reload inside `config` re-seeds the options from the last **successfully
 * launched** ones (`launchPrefs.ts`), or from the defaults where no launch of
 * that kind has happened, discarding whatever was being edited — is honest, and
 * is why the clamps below exist rather than a pretence of full restoration.
 */
import type { ProjectSummary } from '../../../api/projectRepository';
import {
  parseMode,
  validateCustomPath,
  type ProjectSelection,
  type WizardMode,
  type WizardStep,
} from './wizardState';

export interface WizardLocation {
  step: WizardStep;
  mode: WizardMode | null;
  selection: ProjectSelection;
}

const STEPS: readonly WizardStep[] = ['project', 'mode', 'config'];

export function parseWizardLocation(params: URLSearchParams): WizardLocation {
  const rawStep = params.get('step');
  const step: WizardStep =
    rawStep !== null && (STEPS as readonly string[]).includes(rawStep)
      ? (rawStep as WizardStep)
      : 'project';

  const rawMode = params.get('mode');
  const mode = rawMode === null ? null : parseMode(rawMode);

  const project = params.get('project');
  const path = params.get('path');
  // `project` wins: the two are mutually exclusive, and a hand-edited URL
  // carrying both should resolve deterministically.
  const selection: ProjectSelection =
    project !== null && project !== ''
      ? { kind: 'stored', name: project }
      : path !== null && path !== ''
        ? { kind: 'customPath', path: path.trim() }
        : { kind: 'none' };

  return { step, mode, selection };
}

export function serializeWizardLocation(location: WizardLocation): URLSearchParams {
  const params = new URLSearchParams();
  params.set('step', location.step);
  if (location.mode !== null) params.set('mode', location.mode);
  if (location.selection.kind === 'stored') params.set('project', location.selection.name);
  if (location.selection.kind === 'customPath') params.set('path', location.selection.path);
  return params;
}

export interface ResolveInput {
  requested: WizardLocation;
  /** `undefined` while the project list is still loading. */
  projects: readonly ProjectSummary[] | undefined;
  isModeSelectable: (mode: WizardMode) => boolean;
}

/**
 * `null` when the requested location is already valid; otherwise the location to
 * replace it with.
 */
export function resolveWizardLocation(input: ResolveInput): WizardLocation | null {
  const { requested, projects } = input;
  const selection = requested.selection;

  // Nothing can be validated against a list that has not arrived. Without this,
  // every reload of a deep link would bounce to step one for the duration of one
  // request and lose the user's place.
  if (projects === undefined) return null;

  if (selection.kind === 'stored') {
    const known = projects.some((project) => project.name === selection.name);
    if (!known) return { step: 'project', mode: null, selection: { kind: 'none' } };
  }

  if (selection.kind === 'customPath' && validateCustomPath(selection.path) !== null) {
    // Keep the text so the inline error can explain why we came back here.
    return { step: 'project', mode: null, selection };
  }

  if (requested.step === 'project') {
    return requested.mode === null ? null : { ...requested, mode: null };
  }

  if (selection.kind === 'none') {
    return { step: 'project', mode: null, selection: { kind: 'none' } };
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
