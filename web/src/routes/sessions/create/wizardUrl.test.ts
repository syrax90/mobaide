import { describe, expect, it } from 'vitest';
import type { WorkspaceSummary } from '../../../api/workspaceRepository';
import {
  parseWizardLocation,
  resolveWizardLocation,
  type WizardLocation,
} from './wizardUrl';
import type { WizardMode } from './wizardState';

const CUSTOM: WizardMode = 'custom_0123456789ab';

const workspaces: WorkspaceSummary[] = [
  {
    name: 'mobaide',
    path: '/srv/mobaide',
    order: 0,
    has_linear_key: false,
    is_git: true,
    path_exists: true,
    auto_scan_prompt: undefined,
  },
];

const stored: WizardLocation['selection'] = { kind: 'stored', name: 'mobaide' };

const parse = (query: string): WizardLocation =>
  parseWizardLocation(new URLSearchParams(query));

const resolve = (
  requested: WizardLocation,
  over: { workspaces?: WorkspaceSummary[] | undefined; selectable?: boolean } = {},
): WizardLocation | null =>
  resolveWizardLocation({
    requested,
    workspaces: 'workspaces' in over ? over.workspaces : workspaces,
    isModeSelectable: () => over.selectable ?? true,
  });

describe('parseWizardLocation', () => {
  it('falls back to the first step for anything it cannot read', () => {
    expect(parse('')).toEqual({ step: 'workspace', mode: null, selection: { kind: 'none' } });
    expect(parse('step=nowhere&mode=gemini')).toEqual({
      step: 'workspace',
      mode: null,
      selection: { kind: 'none' },
    });
  });

  it('reads a stored workspace, a trimmed path, and prefers the workspace', () => {
    expect(parse('step=config&mode=claude&workspace=mobaide').selection).toEqual(stored);
    expect(parse('path=%20%2Ftmp%2Fx%20').selection).toEqual({
      kind: 'customPath',
      path: '/tmp/x',
    });
    // A hand-edited URL carrying both must resolve deterministically.
    expect(parse('workspace=mobaide&path=/tmp/x').selection).toEqual(stored);
    expect(parse('workspace=&path=').selection).toEqual({ kind: 'none' });
    // A bookmark from before the rename still names the old `project` param.
    expect(parse('step=config&mode=claude&project=mobaide').selection).toEqual(stored);
  });
});

describe('resolveWizardLocation', () => {
  it('clamps nothing while the workspace list is still loading', () => {
    // Without this, every deep-link reload would bounce to step one for the
    // duration of one request and lose the user's place.
    expect(
      resolve({ step: 'config', mode: 'claude', selection: { kind: 'stored', name: 'gone' } }, {
        workspaces: undefined,
      }),
    ).toBeNull();
  });

  it('sends an unknown workspace name back to the first step', () => {
    expect(
      resolve({ step: 'config', mode: 'claude', selection: { kind: 'stored', name: 'gone' } }),
    ).toEqual({ step: 'workspace', mode: null, selection: { kind: 'none' } });
  });

  it('keeps an invalid path so the inline error can explain the bounce', () => {
    const selection = { kind: 'customPath', path: 'relative' } as const;

    expect(resolve({ step: 'config', mode: 'claude', selection })).toEqual({
      step: 'workspace',
      mode: null,
      selection,
    });
  });

  it('drops a mode carried back to an earlier step', () => {
    expect(resolve({ step: 'workspace', mode: 'claude', selection: { kind: 'none' } })).toEqual({
      step: 'workspace',
      mode: null,
      selection: { kind: 'none' },
    });
    expect(resolve({ step: 'mode', mode: 'claude', selection: stored })).toEqual({
      step: 'mode',
      mode: null,
      selection: stored,
    });
  });

  it('sends a config deep link with no workspace back to the workspace step', () => {
    expect(resolve({ step: 'config', mode: 'claude', selection: { kind: 'none' } })).toEqual({
      step: 'workspace',
      mode: null,
      selection: { kind: 'none' },
    });
  });

  it('sends config back to the mode step when the mode cannot be launched', () => {
    expect(resolve({ step: 'config', mode: null, selection: stored })).toEqual({
      step: 'mode',
      mode: null,
      selection: stored,
    });
    expect(resolve({ step: 'config', mode: CUSTOM, selection: stored }, { selectable: false })).toEqual(
      { step: 'mode', mode: null, selection: stored },
    );
    // The caller keeps a vanished custom type selectable so the launch surfaces
    // the relay's 404 rather than degrading to a plain Terminal.
    expect(resolve({ step: 'config', mode: CUSTOM, selection: stored })).toBeNull();
  });
});
