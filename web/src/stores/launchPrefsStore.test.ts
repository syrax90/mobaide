import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { stubLocalStorage } from '../test/localStorageStub';

// Before importing the store, so zustand's
// `createJSONStorage(() => window.localStorage)` captures the stand-in.
stubLocalStorage();

const { readLaunchPrefs, useLaunchPrefsStore } = await import('./launchPrefsStore');

const STORAGE_KEY = 'mobaide-launch-prefs';

const CLAUDE_BLOB = {
  model: 'sonnet',
  effort: 'max',
  fastMode: true,
  ultracode: true,
  appendSystemPrompt: 'be terse',
  skipPermissions: true,
  noMemory: true,
  planMode: true,
  debug: true,
  focusMode: true,
  terminalUi: true,
};

const CODEX_BLOB = {
  model: 'gpt-5.4',
  sandbox: 'read-only',
  approval: 'never',
  fullAuto: true,
  reasoningEffort: 'minimal',
  terminalUi: true,
} as const;

const ANTIGRAVITY_BLOB = { model: 'Gemini 3.1 Pro (High)', skipPermissions: true, sandbox: false };

const CLAUDE_DEFAULTS = {
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
  terminalUi: false,
} as const;

async function rehydrateWith(state: Record<string, unknown>) {
  window.localStorage.setItem(STORAGE_KEY, JSON.stringify({ state, version: 0 }));
  await useLaunchPrefsStore.persist.rehydrate();
  return readLaunchPrefs();
}

beforeEach(() => {
  // setState writes back through the persist middleware, so reset the
  // in-memory singleton first and clear storage after.
  useLaunchPrefsStore.setState({
    claude: null,
    autoScan: null,
    codex: null,
    antigravity: null,
    lastProjectName: null,
  });
  window.localStorage.clear();
});

afterEach(() => {
  vi.restoreAllMocks();
});

describe('hydration', () => {
  it('restores every stored bucket and leaves the untouched ones unlaunched', async () => {
    const prefs = await rehydrateWith({
      claude: CLAUDE_BLOB,
      codex: CODEX_BLOB,
      antigravity: ANTIGRAVITY_BLOB,
      lastProjectName: 'mobaide',
    });

    expect(prefs.claude).toEqual(CLAUDE_BLOB);
    expect(prefs.codex).toEqual(CODEX_BLOB);
    expect(prefs.antigravity).toEqual(ANTIGRAVITY_BLOB);
    expect(prefs.lastProjectName).toBe('mobaide');
    // `null`, not defaults: that is what keeps Auto Scan's skip-permissions
    // default of `true` alive for a profile that has never run a scan.
    expect(prefs.autoScan).toBeNull();
  });

  it('takes the mobile data-class default for a key the blob never carried', async () => {
    const prefs = await rehydrateWith({ claude: { model: 'haiku' }, codex: {}, antigravity: {} });

    expect(prefs.claude).toEqual({ ...CLAUDE_DEFAULTS, model: 'haiku' });
    expect(prefs.codex).toEqual({
      model: null,
      sandbox: 'workspace-write',
      approval: 'on-request',
      fullAuto: false,
      reasoningEffort: 'high',
      terminalUi: false,
    });
    expect(prefs.antigravity).toEqual({ model: null, skipPermissions: false, sandbox: false });
  });

  it('restores Codex Terminal UI off for a blob saved before the toggle existed', async () => {
    const legacy: Record<string, unknown> = { ...CODEX_BLOB };
    delete legacy.terminalUi;

    const prefs = await rehydrateWith({ codex: legacy });

    expect(prefs.codex).toEqual({ ...CODEX_BLOB, terminalUi: false });
  });

  it('voids the Codex bucket for a wrong-typed terminalUi', async () => {
    expect((await rehydrateWith({ codex: { ...CODEX_BLOB, terminalUi: 'on' } })).codex).toBeNull();
  });

  it('restores focus view on for a blob saved before the toggle existed', async () => {
    // CLAUDE_BLOB predates focusMode; a stored `false` must still win over the default.
    const prefs = await rehydrateWith({
      claude: CLAUDE_BLOB,
      autoScan: { ...CLAUDE_BLOB, focusMode: false },
    });

    expect(prefs.claude?.focusMode).toBe(true);
    expect(prefs.autoScan?.focusMode).toBe(false);
  });

  it('voids the bucket for a wrong-typed focusMode', async () => {
    expect((await rehydrateWith({ claude: { ...CLAUDE_BLOB, focusMode: 'on' } })).claude).toBeNull();
  });

  it('voids the whole bucket for a present but wrong-typed field', async () => {
    // A typed `@Serializable`/`Codable` decode fails outright rather than
    // salvaging the readable fields, and a hand-edited value must not reach the
    // wizard. Neighbouring buckets are unaffected.
    const prefs = await rehydrateWith({
      claude: { ...CLAUDE_BLOB, fastMode: 'yes' },
      autoScan: CLAUDE_BLOB,
      antigravity: { ...ANTIGRAVITY_BLOB, model: 7 },
    });

    expect(prefs.claude).toBeNull();
    expect(prefs.antigravity).toBeNull();
    expect(prefs.autoScan).toEqual(CLAUDE_BLOB);
  });

  it('voids the bucket for an enum value outside the wire vocabulary', async () => {
    const bad = async (state: Record<string, unknown>) => (await rehydrateWith(state)).codex;

    expect(
      (await rehydrateWith({ claude: { ...CLAUDE_BLOB, effort: 'insane' } })).claude,
    ).toBeNull();
    expect(await bad({ codex: { ...CODEX_BLOB, sandbox: 'yolo' } })).toBeNull();
    expect(await bad({ codex: { ...CODEX_BLOB, approval: 'maybe' } })).toBeNull();
    // `reasoningEffort` is deliberately NOT vocabulary-checked: the effort set
    // is agent-enumerated per model, so a persisted catalog effort like
    // `ultra` must restore rather than void the bucket. A non-string still voids.
    expect(await bad({ codex: { ...CODEX_BLOB, reasoningEffort: 'ultra' } })).toEqual({
      ...CODEX_BLOB,
      reasoningEffort: 'ultra',
    });
    expect(await bad({ codex: { ...CODEX_BLOB, reasoningEffort: 7 } })).toBeNull();
    // `null` is a real third state on web ("Agent default"), not a bad value.
    expect(await bad({ codex: { ...CODEX_BLOB, reasoningEffort: null } })).toEqual({
      ...CODEX_BLOB,
      reasoningEffort: null,
    });
  });

  it('rejects a bucket that is not a plain object', async () => {
    expect((await rehydrateWith({ claude: [CLAUDE_BLOB] })).claude).toBeNull();
    expect((await rehydrateWith({ claude: 'sonnet' })).claude).toBeNull();
    expect((await rehydrateWith({ codex: null })).codex).toBeNull();
  });

  it('keeps only a usable project name', async () => {
    expect((await rehydrateWith({ lastProjectName: 42 })).lastProjectName).toBeNull();
    expect((await rehydrateWith({ lastProjectName: '' })).lastProjectName).toBeNull();
  });

  it('drops keys the persisted envelope has no slot for', async () => {
    await rehydrateWith({ claude: CLAUDE_BLOB, worktree: true, lastAgentId: 'a1' });

    const state = useLaunchPrefsStore.getState();
    expect(state.claude).toEqual(CLAUDE_BLOB);
    expect('worktree' in state).toBe(false);
    expect('lastAgentId' in state).toBe(false);
  });

  it('keeps the store usable when storage holds malformed JSON', async () => {
    window.localStorage.setItem(STORAGE_KEY, '{not json');

    await useLaunchPrefsStore.persist.rehydrate();

    expect(readLaunchPrefs()).toEqual({
      claude: null,
      autoScan: null,
      codex: null,
      antigravity: null,
      lastProjectName: null,
    });
    useLaunchPrefsStore.getState().setLastProjectName('mobaide');
    expect(readLaunchPrefs().lastProjectName).toBe('mobaide');
  });
});

describe('writes', () => {
  it('files a snapshot under its own slot and reads it back through the sanitiser', async () => {
    useLaunchPrefsStore
      .getState()
      .setLaunchPrefs({ kind: 'autoScan', prefs: { ...CLAUDE_DEFAULTS, skipPermissions: false } });
    useLaunchPrefsStore
      .getState()
      .setLaunchPrefs({ kind: 'codex', prefs: { ...CODEX_BLOB, reasoningEffort: null } });
    const written = window.localStorage.getItem(STORAGE_KEY);

    // Round-trip what the wizard actually writes: a blank append-system-prompt,
    // a stored `false` for a flag that defaults to `true`, and a null reasoning
    // effort all have to survive the store's own read-side validation.
    useLaunchPrefsStore.setState({ autoScan: null, codex: null });
    window.localStorage.setItem(STORAGE_KEY, written ?? '');
    await useLaunchPrefsStore.persist.rehydrate();

    const prefs = readLaunchPrefs();
    expect(prefs.autoScan).toEqual({ ...CLAUDE_DEFAULTS, skipPermissions: false });
    expect(prefs.codex).toEqual({ ...CODEX_BLOB, reasoningEffort: null });
    expect(prefs.claude).toBeNull();
  });
});

describe('when storage refuses', () => {
  it('lets the failure out of the setter, so the call site has to catch it', () => {
    const prefs = { ...CLAUDE_DEFAULTS, model: 'sonnet' };
    vi.spyOn(window.localStorage, 'setItem').mockImplementation(() => {
      throw new Error('quota exceeded');
    });

    // Unlike `recentPaths`, which wraps its own writes, `persist` calls
    // `setItem` on every `set` unguarded. The wizard files these prefs from a
    // mutation's `onSuccess`, where TanStack routes a throw to `onError` — a
    // full private-mode or over-quota profile would report an already-created
    // session as a failed launch — which is what the try/catch there is for.
    expect(() => {
      useLaunchPrefsStore.getState().setLaunchPrefs({ kind: 'claude', prefs });
    }).toThrow('quota exceeded');
    // Catching it costs only durability: the in-memory half already landed.
    expect(readLaunchPrefs().claude).toEqual(prefs);
  });
});
