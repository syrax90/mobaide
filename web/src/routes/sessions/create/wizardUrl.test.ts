import { describe, expect, it } from 'vitest';
import type { ProjectSummary } from '../../../api/projectRepository';
import {
  parseWizardLocation,
  resolveWizardLocation,
  type WizardLocation,
} from './wizardUrl';
import type { WizardMode } from './wizardState';

const CUSTOM: WizardMode = 'custom_0123456789ab';

const projects: ProjectSummary[] = [
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
  over: { projects?: ProjectSummary[] | undefined; selectable?: boolean } = {},
): WizardLocation | null =>
  resolveWizardLocation({
    requested,
    projects: 'projects' in over ? over.projects : projects,
    isModeSelectable: () => over.selectable ?? true,
  });

describe('parseWizardLocation', () => {
  it('falls back to the first step for anything it cannot read', () => {
    expect(parse('')).toEqual({ step: 'project', mode: null, selection: { kind: 'none' } });
    expect(parse('step=nowhere&mode=gemini')).toEqual({
      step: 'project',
      mode: null,
      selection: { kind: 'none' },
    });
  });

  it('reads a stored project, a trimmed path, and prefers the project', () => {
    expect(parse('step=config&mode=claude&project=mobaide').selection).toEqual(stored);
    expect(parse('path=%20%2Ftmp%2Fx%20').selection).toEqual({
      kind: 'customPath',
      path: '/tmp/x',
    });
    // A hand-edited URL carrying both must resolve deterministically.
    expect(parse('project=mobaide&path=/tmp/x').selection).toEqual(stored);
    expect(parse('project=&path=').selection).toEqual({ kind: 'none' });
  });
});

describe('resolveWizardLocation', () => {
  it('clamps nothing while the project list is still loading', () => {
    // Without this, every deep-link reload would bounce to step one for the
    // duration of one request and lose the user's place.
    expect(
      resolve({ step: 'config', mode: 'claude', selection: { kind: 'stored', name: 'gone' } }, {
        projects: undefined,
      }),
    ).toBeNull();
  });

  it('sends an unknown project name back to the first step', () => {
    expect(
      resolve({ step: 'config', mode: 'claude', selection: { kind: 'stored', name: 'gone' } }),
    ).toEqual({ step: 'project', mode: null, selection: { kind: 'none' } });
  });

  it('keeps an invalid path so the inline error can explain the bounce', () => {
    const selection = { kind: 'customPath', path: 'relative' } as const;

    expect(resolve({ step: 'config', mode: 'claude', selection })).toEqual({
      step: 'project',
      mode: null,
      selection,
    });
  });

  it('drops a mode carried back to an earlier step', () => {
    expect(resolve({ step: 'project', mode: 'claude', selection: { kind: 'none' } })).toEqual({
      step: 'project',
      mode: null,
      selection: { kind: 'none' },
    });
    expect(resolve({ step: 'mode', mode: 'claude', selection: stored })).toEqual({
      step: 'mode',
      mode: null,
      selection: stored,
    });
  });

  it('sends a config deep link with no project back to the project step', () => {
    expect(resolve({ step: 'config', mode: 'claude', selection: { kind: 'none' } })).toEqual({
      step: 'project',
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
