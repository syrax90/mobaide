import { describe, expect, it } from 'vitest';
import {
  ANTIGRAVITY_PREFS_DEFAULTS,
  CLAUDE_PREFS_DEFAULTS,
  CODEX_PREFS_DEFAULTS,
  type ClaudeLaunchPrefs,
  type LaunchPrefs,
} from '../../../stores/launchPrefsStore';
import { applyLaunchPrefs, launchPrefsFromState } from './launchPrefs';
import { INITIAL_WIZARD_STATE, type WizardMode, type WizardState } from './wizardState';

const NEVER_LAUNCHED: LaunchPrefs = {
  claude: null,
  autoScan: null,
  codex: null,
  antigravity: null,
  lastProjectName: null,
};

const claudePrefs = (over: Partial<ClaudeLaunchPrefs> = {}): ClaudeLaunchPrefs => ({
  model: null,
  effort: 'high',
  fastMode: false,
  ultracode: false,
  appendSystemPrompt: '',
  skipPermissions: false,
  noMemory: false,
  planMode: false,
  debug: false,
  focusMode: true,
  ...over,
});

/** Narrows the tagged snapshot so a Claude bucket can be fed back through `applyLaunchPrefs`. */
function claudeSnapshot(state: WizardState, mode: WizardMode): ClaudeLaunchPrefs {
  const snapshot = launchPrefsFromState(state, mode);
  if (snapshot === null || (snapshot.kind !== 'claude' && snapshot.kind !== 'autoScan')) {
    throw new Error(`expected a Claude bucket for ${mode}`);
  }
  return snapshot.prefs;
}

describe('applyLaunchPrefs', () => {
  it('leaves a never-launched bucket exactly as the defaults had it', () => {
    const seeded = applyLaunchPrefs(INITIAL_WIZARD_STATE, NEVER_LAUNCHED);

    expect(seeded).toEqual(INITIAL_WIZARD_STATE);
    // The reason every bucket is nullable: a fresh profile still gets Auto
    // Scan's skip-permissions default, which is the opposite of Claude's.
    expect(seeded.autoScan.skipPermissions).toBe(true);
  });

  it('restores each Claude bucket without bleeding into the other', () => {
    const claudeOnly = applyLaunchPrefs(INITIAL_WIZARD_STATE, {
      ...NEVER_LAUNCHED,
      claude: claudePrefs({ model: 'sonnet', skipPermissions: true, appendSystemPrompt: 'terse' }),
    });

    expect(claudeOnly.claude).toMatchObject({ model: 'sonnet', skipPermissions: true });
    expect(claudeOnly.autoScan).toEqual(INITIAL_WIZARD_STATE.autoScan);

    const scanOnly = applyLaunchPrefs(INITIAL_WIZARD_STATE, {
      ...NEVER_LAUNCHED,
      autoScan: claudePrefs({ skipPermissions: false, planMode: true }),
    });

    // A stored `false` has to beat the `true` default, or the flag could never
    // be turned off for good.
    expect(scanOnly.autoScan).toMatchObject({ skipPermissions: false, planMode: true });
    expect(scanOnly.claude).toEqual(INITIAL_WIZARD_STATE.claude);
  });

  it('restores Plan mode into Auto Scan only', () => {
    const seeded = applyLaunchPrefs(INITIAL_WIZARD_STATE, {
      ...NEVER_LAUNCHED,
      claude: claudePrefs({ planMode: true, noMemory: true }),
      autoScan: claudePrefs({ planMode: true }),
    });

    expect(seeded.autoScan.planMode).toBe(true);
    // Auto Scan is the only branch with a Plan mode control
    // (`AutoScanOptionsPanel`), so the Claude bucket takes the wizard default
    // however the blob was written — otherwise a hand-edited file could put
    // `plan_mode: true` on a Claude launch with nothing on screen to show for
    // it. Both mobile clients restore it the same way.
    expect(seeded.claude.planMode).toBe(INITIAL_WIZARD_STATE.claude.planMode);
    expect(seeded.claude.planMode).toBe(false);
    // Only that one field is held back: the rest of the blob still restores.
    expect(seeded.claude.noMemory).toBe(true);
  });

  it('clamps a restored effort the model can no longer serve', () => {
    const restore = (prefs: ClaudeLaunchPrefs) =>
      applyLaunchPrefs(INITIAL_WIZARD_STATE, { ...NEVER_LAUNCHED, claude: prefs }).claude.effort;

    expect(restore(claudePrefs({ model: 'haiku', effort: 'max' }))).toBe('high');
    expect(restore(claudePrefs({ model: 'sonnet', effort: 'max' }))).toBe('max');
    // Fast mode promotes the model to Opus, which widens the ladder back out.
    expect(restore(claudePrefs({ model: 'haiku', effort: 'max', fastMode: true }))).toBe('max');
  });

  it('cannot restore the Antigravity pair the server rejects', () => {
    const restore = (skipPermissions: boolean, sandbox: boolean) =>
      applyLaunchPrefs(INITIAL_WIZARD_STATE, {
        ...NEVER_LAUNCHED,
        antigravity: { model: 'Gemini 3.1 Pro (Low)', skipPermissions, sandbox },
      }).antigravity;

    expect(restore(true, true)).toMatchObject({ skipPermissions: true, sandbox: false });
    expect(restore(false, true)).toMatchObject({ skipPermissions: false, sandbox: true });
  });

  it('restores the Codex bucket including the null "agent default" effort', () => {
    const seeded = applyLaunchPrefs(INITIAL_WIZARD_STATE, {
      ...NEVER_LAUNCHED,
      codex: {
        model: 'gpt-5.4',
        sandbox: 'read-only',
        approval: 'never',
        fullAuto: true,
        reasoningEffort: null,
      },
    });

    expect(seeded.codex).toEqual({
      model: 'gpt-5.4',
      sandbox: 'read-only',
      approval: 'never',
      fullAuto: true,
      reasoningEffort: null,
    });
  });

  it('restores nothing into the per-session fields', () => {
    const base: WizardState = {
      ...INITIAL_WIZARD_STATE,
      autoScan: { ...INITIAL_WIZARD_STATE.autoScan, initialPrompt: 'seeded from the project' },
      antigravity: { ...INITIAL_WIZARD_STATE.antigravity, addDirsDraft: '/extra' },
      git: { worktree: true, branchName: 'wip' },
    };

    const seeded = applyLaunchPrefs(base, {
      claude: claudePrefs({ model: 'opus' }),
      autoScan: claudePrefs({ debug: true }),
      codex: null,
      antigravity: { model: null, skipPermissions: false, sandbox: true },
      lastProjectName: 'mobaide',
    });

    expect(seeded.autoScan.initialPrompt).toBe('seeded from the project');
    expect(seeded.antigravity.addDirsDraft).toBe('/extra');
    expect(seeded.git).toEqual({ worktree: true, branchName: 'wip' });
  });
});

describe('launchPrefsFromState', () => {
  const dialled: WizardState = {
    ...INITIAL_WIZARD_STATE,
    claude: { ...INITIAL_WIZARD_STATE.claude, model: 'opus', initialPrompt: 'ignored' },
    autoScan: { ...INITIAL_WIZARD_STATE.autoScan, debug: true, initialPrompt: 'scan this' },
    codex: { ...INITIAL_WIZARD_STATE.codex, model: 'gpt-5.4-mini', fullAuto: true },
    antigravity: {
      model: 'Gemini 3.5 Flash (High)',
      skipPermissions: true,
      sandbox: false,
      addDirsDraft: '/extra',
    },
  };

  it('files each mode under its own bucket', () => {
    expect(launchPrefsFromState(dialled, 'claude')).toEqual({
      kind: 'claude',
      prefs: claudePrefs({ model: 'opus' }),
    });
    expect(launchPrefsFromState(dialled, 'auto_scan')).toEqual({
      kind: 'autoScan',
      prefs: claudePrefs({ skipPermissions: true, debug: true }),
    });
    expect(launchPrefsFromState(dialled, 'codex')).toEqual({
      kind: 'codex',
      prefs: { ...INITIAL_WIZARD_STATE.codex, model: 'gpt-5.4-mini', fullAuto: true },
    });
    expect(launchPrefsFromState(dialled, 'antigravity')).toEqual({
      kind: 'antigravity',
      prefs: { model: 'Gemini 3.5 Flash (High)', skipPermissions: true, sandbox: false },
    });
  });

  it('leaves the per-session fields out of the snapshot', () => {
    // Listed field by field rather than spread, so a bucket field that is not a
    // reusable default cannot start being persisted by accident.
    expect(launchPrefsFromState(dialled, 'auto_scan')?.prefs).not.toHaveProperty('initialPrompt');
    expect(launchPrefsFromState(dialled, 'antigravity')?.prefs).not.toHaveProperty('addDirsDraft');
  });

  it('remembers nothing for the modes that carry no options', () => {
    expect(launchPrefsFromState(dialled, 'shell')).toBeNull();
    expect(launchPrefsFromState(dialled, 'custom_0123456789ab')).toBeNull();
  });

  it('snapshots the raw selection, not the effective one', () => {
    // Android's choice: turning Fast mode back off then returns the model the
    // user actually picked instead of the `opus` it implied.
    const state: WizardState = {
      ...INITIAL_WIZARD_STATE,
      claude: { ...INITIAL_WIZARD_STATE.claude, model: 'haiku', effort: 'max', fastMode: true },
    };

    expect(claudeSnapshot(state, 'claude')).toMatchObject({
      model: 'haiku',
      effort: 'max',
      fastMode: true,
    });
  });

  it('keeps a cleared append-system-prompt cleared through a round trip', () => {
    // The trap the pinned-snapshot design exists to avoid: rebuilding the prefs
    // from the request would hand back `undefined` here, because
    // `buildCreateRequest` drops a blank prompt on the way to the wire.
    const cleared = claudeSnapshot(INITIAL_WIZARD_STATE, 'claude');
    const base: WizardState = {
      ...INITIAL_WIZARD_STATE,
      claude: { ...INITIAL_WIZARD_STATE.claude, appendSystemPrompt: 'stale' },
    };

    const restored = applyLaunchPrefs(base, { ...NEVER_LAUNCHED, claude: cleared });

    expect(restored.claude.appendSystemPrompt).toBe('');
  });

  it('round-trips focus view off per bucket without bleeding into the other', () => {
    const state: WizardState = {
      ...INITIAL_WIZARD_STATE,
      claude: { ...INITIAL_WIZARD_STATE.claude, focusMode: false },
    };
    const claude = claudeSnapshot(state, 'claude');
    const autoScan = claudeSnapshot(state, 'auto_scan');

    expect(claude.focusMode).toBe(false);
    expect(autoScan.focusMode).toBe(true);

    const restored = applyLaunchPrefs(INITIAL_WIZARD_STATE, {
      ...NEVER_LAUNCHED,
      claude,
      autoScan: { ...autoScan, focusMode: false },
    });

    expect(restored.claude.focusMode).toBe(false);
    expect(restored.autoScan.focusMode).toBe(false);
  });
});

describe('the two sets of defaults', () => {
  // The store's constants are the mobile data-class defaults, reached only when
  // a stored blob omits a key; `INITIAL_WIZARD_STATE` supplies every value when
  // a bucket is absent or voided. Two separate concepts that have to keep
  // agreeing, and `stores/` cannot import `routes/` to say so — this test is
  // the only place both halves are visible at once.
  it('agree field for field with the wizard defaults', () => {
    expect(launchPrefsFromState(INITIAL_WIZARD_STATE, 'claude')?.prefs).toEqual(
      CLAUDE_PREFS_DEFAULTS,
    );
    expect(launchPrefsFromState(INITIAL_WIZARD_STATE, 'codex')?.prefs).toEqual(
      CODEX_PREFS_DEFAULTS,
    );
    expect(launchPrefsFromState(INITIAL_WIZARD_STATE, 'antigravity')?.prefs).toEqual(
      ANTIGRAVITY_PREFS_DEFAULTS,
    );
  });

  it('part company on the one field mobile cannot express', () => {
    const autoScan = launchPrefsFromState(INITIAL_WIZARD_STATE, 'auto_scan')?.prefs;

    // Android and iOS share one `ClaudeLaunchPrefs` type across both buckets,
    // so the store has a single set of Claude defaults with `skipPermissions:
    // false`, while the wizard turns it on for Auto Scan alone (a scan runs
    // unattended). Making the store's constant match this projection would
    // silently flip that flag for a Claude blob missing the key.
    expect(autoScan).not.toEqual(CLAUDE_PREFS_DEFAULTS);
    expect(autoScan).toEqual({ ...CLAUDE_PREFS_DEFAULTS, skipPermissions: true });
  });
});
