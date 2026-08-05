import { describe, expect, it } from 'vitest';
import type { ProjectSummary } from '../../../api/projectRepository';
import {
  claudeEffortsFor,
  codexPreset,
  effectiveClaudeEffort,
  effectiveClaudeModel,
  INITIAL_WIZARD_STATE,
  parseAddDirs,
  parseMode,
  validateCustomPath,
  wizardReducer,
  type WizardAction,
  type WizardState,
} from './wizardState';

const project = (over: Partial<ProjectSummary> = {}): ProjectSummary => ({
  name: 'mobaide',
  path: '/srv/mobaide',
  order: 0,
  has_linear_key: false,
  is_git: true,
  path_exists: true,
  auto_scan_prompt: undefined,
  ...over,
});

const reduce = (actions: WizardAction[], from: WizardState = INITIAL_WIZARD_STATE): WizardState =>
  actions.reduce(wizardReducer, from);

describe('selectors', () => {
  it('offers the wide effort range only on the larger models', () => {
    expect(claudeEffortsFor('opus')).toContain('max');
    expect(claudeEffortsFor('fable')).toContain('max');
    expect(claudeEffortsFor('sonnet')).toContain('max');
    expect(claudeEffortsFor('haiku')).toEqual(['low', 'medium', 'high']);
    // The Default model caps at `high`, like Android and iOS: the model the
    // agent resolves it to is unknown, and `effectiveClaudeEffort` clamps
    // through this helper, so widening it would put an effort on the wire the
    // other two clients never send.
    expect(claudeEffortsFor(null)).toEqual(['low', 'medium', 'high']);
  });

  it('lets fast mode decide the model', () => {
    const bucket = { ...INITIAL_WIZARD_STATE.claude, model: 'haiku', fastMode: true };

    expect(effectiveClaudeModel(bucket)).toBe('opus');
    expect(effectiveClaudeModel({ ...bucket, fastMode: false })).toBe('haiku');
  });

  it('clamps an effort the effective model cannot serve', () => {
    const bucket = { ...INITIAL_WIZARD_STATE.claude, model: 'haiku', effort: 'max' as const };

    expect(effectiveClaudeEffort(bucket)).toBe('high');
    expect(effectiveClaudeEffort({ ...bucket, effort: 'low' })).toBe('low');
    // Fast mode promotes the model, which widens the range back out.
    expect(effectiveClaudeEffort({ ...bucket, fastMode: true })).toBe('max');
  });

  it('reads the pressed Codex preset, full access first', () => {
    const base = INITIAL_WIZARD_STATE.codex;

    expect(codexPreset({ ...base, sandbox: 'read-only' })).toBe('readOnly');
    expect(codexPreset(base)).toBe('auto');
    // Full auto makes the other two moot, whatever they say.
    expect(codexPreset({ ...base, sandbox: 'read-only', fullAuto: true })).toBe('fullAccess');
    expect(codexPreset({ ...base, approval: 'never' })).toBeNull();
    expect(codexPreset({ ...base, sandbox: 'danger-full-access' })).toBeNull();
  });

  it('splits added directories on any newline flavour and on commas', () => {
    expect(parseAddDirs(' /a ,\r\n/b\n\n , \r /c ')).toEqual(['/a', '/b', '/c']);
    expect(parseAddDirs('   ')).toEqual([]);
  });

  const PATHS: [string, string | null][] = [
    ['', 'Path is required'],
    ['   ', 'Path is required'],
    ['foo', 'Path must be absolute'],
    ['/a', null],
    ['C:/x', null],
    ['c:\\x', null],
    ['\\\\server\\share', null],
  ];

  it.each(PATHS)('validates %j', (input, expected) => {
    expect(validateCustomPath(input)).toBe(expected);
  });

  it('accepts the built-in modes and well-formed custom kinds only', () => {
    expect(parseMode('shell')).toBe('shell');
    expect(parseMode('custom_0123456789ab')).toBe('custom_0123456789ab');
    expect(parseMode('custom_0123456789AB')).toBeNull();
    expect(parseMode('custom_short')).toBeNull();
    expect(parseMode('gemini')).toBeNull();
  });
});

describe('wizardReducer', () => {
  it('clears the git bucket when the chosen project is not a repo', () => {
    const dialled = reduce([
      { type: 'setWorktree', value: true },
      { type: 'setBranchName', value: 'feature/x' },
    ]);

    expect(reduce([{ type: 'selectStoredProject', project: project() }], dialled).git).toEqual({
      worktree: true,
      branchName: 'feature/x',
    });
    expect(
      reduce([{ type: 'selectStoredProject', project: project({ is_git: false }) }], dialled).git,
    ).toEqual({ worktree: false, branchName: '' });
  });

  it('keeps the raw draft but selects the trimmed path', () => {
    const state = reduce([{ type: 'setCustomPathDraft', draft: '  /a  ' }]);

    expect(state.customPathDraft).toBe('  /a  ');
    expect(state.selection).toEqual({ kind: 'customPath', path: '/a' });
    expect(state.customPathError).toBeNull();
    expect(reduce([{ type: 'setCustomPathDraft', draft: 'foo' }]).customPathError).toBe(
      'Path must be absolute',
    );
  });

  it('picks Opus when fast mode goes on and leaves it when it goes off', () => {
    const on = reduce([
      { type: 'setClaudeModel', bucket: 'claude', model: 'haiku' },
      { type: 'setClaudeFlag', bucket: 'claude', flag: 'fastMode', value: true },
    ]);

    expect(on.claude.model).toBe('opus');
    expect(
      reduce([{ type: 'setClaudeFlag', bucket: 'claude', flag: 'fastMode', value: false }], on)
        .claude.model,
    ).toBe('opus');
  });

  it('clamps a stranded effort when the model shrinks', () => {
    const state = reduce([
      { type: 'setClaudeEffort', bucket: 'claude', effort: 'max' },
      { type: 'setClaudeModel', bucket: 'claude', model: 'haiku' },
    ]);

    expect(state.claude.effort).toBe('high');
  });

  it('never bleeds between the Claude and Auto Scan buckets', () => {
    const state = reduce([
      { type: 'setClaudeFlag', bucket: 'claude', flag: 'skipPermissions', value: true },
      { type: 'setClaudeText', bucket: 'autoScan', field: 'initialPrompt', value: 'scan' },
    ]);

    expect(state.autoScan.initialPrompt).toBe('scan');
    expect(state.claude.initialPrompt).toBe('');
    // Auto Scan starts with skip-permissions on; Claude starts with it off.
    expect(state.autoScan.skipPermissions).toBe(true);
    expect(INITIAL_WIZARD_STATE.claude.skipPermissions).toBe(false);
  });

  it('excludes Antigravity sandbox and skip-permissions in both directions', () => {
    const skipping = reduce([
      { type: 'setAntigravitySandbox', value: true },
      { type: 'setAntigravitySkipPermissions', value: true },
    ]);
    const sandboxed = reduce([{ type: 'setAntigravitySandbox', value: true }], skipping);

    expect(skipping.antigravity).toMatchObject({ skipPermissions: true, sandbox: false });
    expect(sandboxed.antigravity).toMatchObject({ skipPermissions: false, sandbox: true });
  });

  it('moves only full_auto for the full-access preset', () => {
    const advanced = reduce([
      { type: 'setCodexSandbox', sandbox: 'read-only' },
      { type: 'setCodexApproval', approval: 'untrusted' },
    ]);
    const full = reduce([{ type: 'setCodexPreset', preset: 'fullAccess' }], advanced);

    // The agent ignores both under --yolo, so overwriting them would only
    // destroy what the user dialled in when the toggle goes back off.
    expect(full.codex).toMatchObject({
      fullAuto: true,
      sandbox: 'read-only',
      approval: 'untrusted',
    });
    expect(reduce([{ type: 'setCodexPreset', preset: 'readOnly' }], full).codex).toMatchObject({
      fullAuto: false,
      sandbox: 'read-only',
      approval: 'on-request',
    });
  });

  it('seeds the Auto Scan prompt once per project', () => {
    const seeded = reduce([{ type: 'seedAutoScanPrompt', projectName: 'a', prompt: 'from a' }]);
    const edited = reduce(
      [{ type: 'setClaudeText', bucket: 'autoScan', field: 'initialPrompt', value: 'mine' }],
      seeded,
    );

    expect(
      reduce([{ type: 'seedAutoScanPrompt', projectName: 'a', prompt: 'from a' }], edited).autoScan
        .initialPrompt,
    ).toBe('mine');
    expect(
      reduce([{ type: 'seedAutoScanPrompt', projectName: 'b', prompt: 'from b' }], edited).autoScan
        .initialPrompt,
    ).toBe('from b');
  });
});
