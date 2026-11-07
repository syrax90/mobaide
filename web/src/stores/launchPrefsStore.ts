/**
 * The launch options each kind of session was last **successfully** started
 * with, re-applied as the create wizard's starting point.
 *
 * Mirrors Android's `PreferencesStore` `last_used_*_options` blobs and iOS's
 * identical `UserDefaults` keys field for field, including the deliberate
 * omissions: worktree/branch, Antigravity `add_dirs` and the Auto Scan initial
 * prompt are per-session, not reusable defaults, so none of them are here.
 *
 * **Global, not per agent** — mobile's scoping, kept on purpose. A model, an
 * effort or an autonomy preset is a preference about how the user likes to
 * work, not a fact about a machine, so a second agent should inherit it rather
 * than start from defaults. It also means nothing has to be re-seeded when the
 * router keeps one wizard instance across a change of `:agentId`, which is the
 * hazard that forces `recentPaths` to read through an effect.
 *
 * Not purged at sign-out, again matching both mobile clients — unlike
 * `recentPaths`, which names directories on the user's workstation.
 */
import { create } from 'zustand';
import { persist } from 'zustand/middleware';
import {
  CLAUDE_EFFORTS,
  CODEX_APPROVALS,
  CODEX_SANDBOXES,
} from '../protocol/dto/sessionCreate';

type ClaudeEffort = (typeof CLAUDE_EFFORTS)[number];
type CodexSandbox = (typeof CODEX_SANDBOXES)[number];
type CodexApproval = (typeof CODEX_APPROVALS)[number];
/** Free-form like the wire field — see `isCodexReasoningEffort` below. */
type CodexReasoningEffort = string;

/** Android `ClaudeLaunchPrefs`, iOS `ClaudeLaunchPrefs`. Backs two independent buckets. */
export interface ClaudeLaunchPrefs {
  model: string | null;
  effort: ClaudeEffort;
  fastMode: boolean;
  ultracode: boolean;
  appendSystemPrompt: string;
  skipPermissions: boolean;
  noMemory: boolean;
  /** Only meaningful in the Auto Scan bucket, as on both mobile clients. */
  planMode: boolean;
  debug: boolean;
  focusMode: boolean;
  /** Claude Code's "Terminal UI" toggle; a blob from before it existed reads as off. */
  terminalUi: boolean;
}

/** Android `CodexLaunchPrefs`, iOS `CodexLaunchPrefs`. */
export interface CodexLaunchPrefs {
  model: string | null;
  sandbox: CodexSandbox;
  approval: CodexApproval;
  fullAuto: boolean;
  /**
   * Nullable here, a plain `String` on both mobile clients — the one shape
   * deviation. Web's control has a real third state (`null` renders "Agent
   * default"), so coercing it to `"high"` on the way to storage would destroy a
   * selection the user can actually make. Nothing about this blob crosses the
   * wire or a device boundary, so the wider type costs no interop.
   */
  reasoningEffort: CodexReasoningEffort | null;
}

/** Android `AntigravityLaunchPrefs`, iOS `AntigravityLaunchPrefs`. */
export interface AntigravityLaunchPrefs {
  model: string | null;
  skipPermissions: boolean;
  sandbox: boolean;
}

/**
 * The data half of the store. A `null` bucket means "never launched", which is
 * what keeps Auto Scan's `skipPermissions` default of `true` alive for a fresh
 * profile while still letting a stored `false` win.
 */
export interface LaunchPrefs {
  claude: ClaudeLaunchPrefs | null;
  autoScan: ClaudeLaunchPrefs | null;
  codex: CodexLaunchPrefs | null;
  antigravity: AntigravityLaunchPrefs | null;
  lastProjectName: string | null;
}

export type ClaudePrefsBucketId = 'claude' | 'autoScan';

/** One bucket's worth of options, tagged with the slot it belongs in. */
export type LaunchPrefsSnapshot =
  | { kind: ClaudePrefsBucketId; prefs: ClaudeLaunchPrefs }
  | { kind: 'codex'; prefs: CodexLaunchPrefs }
  | { kind: 'antigravity'; prefs: AntigravityLaunchPrefs };

interface LaunchPrefsState extends LaunchPrefs {
  setLaunchPrefs: (snapshot: LaunchPrefsSnapshot) => void;
  setLastProjectName: (name: string) => void;
}

/*
 * The three constants below are the **mobile data-class defaults**, reached
 * only when a stored blob omits a key — Kotlin's `encodeDefaults` /
 * `ignoreUnknownKeys` and Swift's `decodeIfPresent ?? default`. They are
 * deliberately *not* the wizard's UI defaults, which `INITIAL_WIZARD_STATE` in
 * `routes/sessions/create/wizardState.ts` owns and which supply every value
 * when a bucket is absent or voided. The two agree field for field except one:
 * Auto Scan's `skipPermissions` is `true` in the wizard (a scan runs
 * unattended) and `false` here, because Android and iOS share one
 * `ClaudeLaunchPrefs` type across both buckets. Keeping them separate is what
 * reproduces that asymmetry; `stores/` cannot import `routes/` in any case.
 * They are exported only so the guard that pins that relationship can live on
 * the routes side, where both halves are importable.
 */
export const CLAUDE_PREFS_DEFAULTS: ClaudeLaunchPrefs = {
  model: null,
  effort: 'high',
  fastMode: false,
  ultracode: false,
  appendSystemPrompt: '',
  skipPermissions: false,
  noMemory: false,
  planMode: false,
  debug: false,
  // On for both buckets, so a blob saved before the toggle existed restores focus view.
  focusMode: true,
  terminalUi: false,
};

export const CODEX_PREFS_DEFAULTS: CodexLaunchPrefs = {
  model: null,
  sandbox: 'workspace-write',
  approval: 'on-request',
  fullAuto: false,
  reasoningEffort: 'high',
};

export const ANTIGRAVITY_PREFS_DEFAULTS: AntigravityLaunchPrefs = {
  model: null,
  skipPermissions: false,
  sandbox: false,
};

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}

function isBoolean(value: unknown): value is boolean {
  return typeof value === 'boolean';
}

function isString(value: unknown): value is string {
  return typeof value === 'string';
}

function isNullableString(value: unknown): value is string | null {
  return value === null || typeof value === 'string';
}

function memberOf<T extends string>(vocabulary: readonly T[]) {
  return (value: unknown): value is T =>
    typeof value === 'string' && (vocabulary as readonly string[]).includes(value);
}

const isClaudeEffort = memberOf(CLAUDE_EFFORTS);
const isCodexSandbox = memberOf(CODEX_SANDBOXES);
const isCodexApproval = memberOf(CODEX_APPROVALS);

/**
 * Any string validates, not just `CODEX_REASONING_EFFORTS`: the vocabulary is
 * agent-enumerated per model (`capabilities.cli.codex.model_details`), so a
 * persisted catalog effort like `ultra` must restore rather than void the
 * bucket the next time the wizard opens.
 */
function isCodexReasoningEffort(value: unknown): value is CodexReasoningEffort | null {
  return isNullableString(value);
}

/**
 * One field of a stored blob: the saved value when it validates, the default
 * when the key is absent, `undefined` when it is present but wrong.
 *
 * The two halves are the two things the mobile decoders do. A missing key takes
 * the data-class default, matching Kotlin's `encodeDefaults`/`ignoreUnknownKeys`
 * and Swift's `decodeIfPresent ?? default`, so a blob written by an older build
 * still restores. A present-but-wrong value instead voids the **whole** bucket
 * (see the callers), because a typed `@Serializable` / `Codable` decode fails
 * outright rather than salvaging the readable fields — the same rule
 * `recentPaths.ts` already applies to its array members.
 *
 * `Object.hasOwn` rather than `in`, so a key inherited from `Object.prototype`
 * cannot stand in for one the blob never carried.
 */
function field<T>(
  source: Record<string, unknown>,
  key: string,
  fallback: T,
  accept: (value: unknown) => value is T,
): T | undefined {
  if (!Object.hasOwn(source, key)) return fallback;
  const raw = source[key];
  return accept(raw) ? raw : undefined;
}

function sanitizeClaudePrefs(raw: unknown): ClaudeLaunchPrefs | null {
  if (!isRecord(raw)) return null;
  const model = field(raw, 'model', CLAUDE_PREFS_DEFAULTS.model, isNullableString);
  const effort = field(raw, 'effort', CLAUDE_PREFS_DEFAULTS.effort, isClaudeEffort);
  const fastMode = field(raw, 'fastMode', CLAUDE_PREFS_DEFAULTS.fastMode, isBoolean);
  const ultracode = field(raw, 'ultracode', CLAUDE_PREFS_DEFAULTS.ultracode, isBoolean);
  const appendSystemPrompt = field(
    raw,
    'appendSystemPrompt',
    CLAUDE_PREFS_DEFAULTS.appendSystemPrompt,
    isString,
  );
  const skipPermissions = field(
    raw,
    'skipPermissions',
    CLAUDE_PREFS_DEFAULTS.skipPermissions,
    isBoolean,
  );
  const noMemory = field(raw, 'noMemory', CLAUDE_PREFS_DEFAULTS.noMemory, isBoolean);
  const planMode = field(raw, 'planMode', CLAUDE_PREFS_DEFAULTS.planMode, isBoolean);
  const debug = field(raw, 'debug', CLAUDE_PREFS_DEFAULTS.debug, isBoolean);
  const focusMode = field(raw, 'focusMode', CLAUDE_PREFS_DEFAULTS.focusMode, isBoolean);
  const terminalUi = field(raw, 'terminalUi', CLAUDE_PREFS_DEFAULTS.terminalUi, isBoolean);
  if (
    model === undefined ||
    effort === undefined ||
    fastMode === undefined ||
    ultracode === undefined ||
    appendSystemPrompt === undefined ||
    skipPermissions === undefined ||
    noMemory === undefined ||
    planMode === undefined ||
    debug === undefined ||
    focusMode === undefined ||
    terminalUi === undefined
  ) {
    return null;
  }
  return {
    model,
    effort,
    fastMode,
    ultracode,
    appendSystemPrompt,
    skipPermissions,
    noMemory,
    planMode,
    debug,
    focusMode,
    terminalUi,
  };
}

function sanitizeCodexPrefs(raw: unknown): CodexLaunchPrefs | null {
  if (!isRecord(raw)) return null;
  const model = field(raw, 'model', CODEX_PREFS_DEFAULTS.model, isNullableString);
  const sandbox = field(raw, 'sandbox', CODEX_PREFS_DEFAULTS.sandbox, isCodexSandbox);
  const approval = field(raw, 'approval', CODEX_PREFS_DEFAULTS.approval, isCodexApproval);
  const fullAuto = field(raw, 'fullAuto', CODEX_PREFS_DEFAULTS.fullAuto, isBoolean);
  const reasoningEffort = field(
    raw,
    'reasoningEffort',
    CODEX_PREFS_DEFAULTS.reasoningEffort,
    isCodexReasoningEffort,
  );
  if (
    model === undefined ||
    sandbox === undefined ||
    approval === undefined ||
    fullAuto === undefined ||
    reasoningEffort === undefined
  ) {
    return null;
  }
  return { model, sandbox, approval, fullAuto, reasoningEffort };
}

function sanitizeAntigravityPrefs(raw: unknown): AntigravityLaunchPrefs | null {
  if (!isRecord(raw)) return null;
  const model = field(raw, 'model', ANTIGRAVITY_PREFS_DEFAULTS.model, isNullableString);
  const skipPermissions = field(
    raw,
    'skipPermissions',
    ANTIGRAVITY_PREFS_DEFAULTS.skipPermissions,
    isBoolean,
  );
  const sandbox = field(raw, 'sandbox', ANTIGRAVITY_PREFS_DEFAULTS.sandbox, isBoolean);
  if (model === undefined || skipPermissions === undefined || sandbox === undefined) {
    return null;
  }
  // The interlock is re-applied where the bucket meets the wizard, not here:
  // storage should keep what was launched, and a stored pair is unreachable
  // anyway because the reducer excludes the two switches.
  return { model, skipPermissions, sandbox };
}

export const useLaunchPrefsStore = create<LaunchPrefsState>()(
  persist(
    (set) => ({
      claude: null,
      autoScan: null,
      codex: null,
      antigravity: null,
      lastProjectName: null,
      setLaunchPrefs: (snapshot) => {
        switch (snapshot.kind) {
          case 'claude':
            set({ claude: snapshot.prefs });
            return;
          case 'autoScan':
            set({ autoScan: snapshot.prefs });
            return;
          case 'codex':
            set({ codex: snapshot.prefs });
            return;
          case 'antigravity':
            set({ antigravity: snapshot.prefs });
        }
      },
      setLastProjectName: (lastProjectName) => {
        set({ lastProjectName });
      },
    }),
    {
      name: 'mobaide-launch-prefs',
      // Same untrusted-boundary rule as `themeStore` and `lastSessionStore`:
      // every field is read out by name and validated rather than spread in, so
      // a hand-edited value cannot reach the wizard and a retired key from an
      // older build is dropped instead of lingering.
      merge: (persisted, current) => {
        const saved = isRecord(persisted) ? persisted : {};
        const lastProjectName = saved.lastProjectName;
        return {
          ...current,
          claude: sanitizeClaudePrefs(saved.claude),
          autoScan: sanitizeClaudePrefs(saved.autoScan),
          codex: sanitizeCodexPrefs(saved.codex),
          antigravity: sanitizeAntigravityPrefs(saved.antigravity),
          lastProjectName:
            typeof lastProjectName === 'string' && lastProjectName !== ''
              ? lastProjectName
              : null,
        };
      },
    },
  ),
);

/** Non-reactive read, for the wizard's reducer initializer and prefill effect. */
export function readLaunchPrefs(): LaunchPrefs {
  const { claude, autoScan, codex, antigravity, lastProjectName } =
    useLaunchPrefsStore.getState();
  return { claude, autoScan, codex, antigravity, lastProjectName };
}
