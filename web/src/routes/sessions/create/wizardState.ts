/**
 * The create wizard's state, as one reducer over **per-mode buckets**.
 *
 * The alternative — a single react-hook-form with `shouldUnregister: true` —
 * was rejected: it makes leak-freedom a *rendering* property, so a field kept
 * mounted behind `hidden`, or a future tab layout, silently smuggles a Claude
 * option into a Codex request. Separate buckets plus a builder that switches on
 * mode makes the leak structurally impossible instead, gives Auto Scan the
 * fully independent Claude bucket it needs, and turns the cross-field interlocks
 * into reducer cases that test without a DOM.
 *
 * Mirrors `CreateSessionUiState` (Android) and `CreateSessionModel` (iOS).
 */
import { isCustomSessionKind } from '../../../protocol/wireSchema';
import type {
  CLAUDE_EFFORTS,
  CODEX_APPROVALS,
  CODEX_REASONING_EFFORTS,
  CODEX_SANDBOXES,
} from '../../../protocol/dto/sessionCreate';
import type { ProjectSummary } from '../../../api/projectRepository';

export type WizardStep = 'project' | 'mode' | 'config';

export const BUILT_IN_MODES = [
  'claude',
  'codex',
  'antigravity',
  'auto_scan',
  'shell',
] as const;
export type BuiltInMode = (typeof BUILT_IN_MODES)[number];
/** The wire-kind vocabulary, reused verbatim as the URL's `mode` token. */
export type WizardMode = BuiltInMode | `custom_${string}`;

export function parseMode(token: string): WizardMode | null {
  if ((BUILT_IN_MODES as readonly string[]).includes(token)) return token as BuiltInMode;
  return isCustomSessionKind(token) ? (token as WizardMode) : null;
}

export function isCustomMode(mode: WizardMode): boolean {
  return isCustomSessionKind(mode);
}

export type ClaudeEffort = (typeof CLAUDE_EFFORTS)[number];
export type CodexSandbox = (typeof CODEX_SANDBOXES)[number];
export type CodexApproval = (typeof CODEX_APPROVALS)[number];
export type CodexReasoningEffort = (typeof CODEX_REASONING_EFFORTS)[number];
export type CodexPreset = 'readOnly' | 'auto' | 'fullAccess';

export type ProjectSelection =
  | { kind: 'none' }
  | { kind: 'stored'; name: string }
  /** Already trimmed — `customPathDraft` keeps whatever the user is typing. */
  | { kind: 'customPath'; path: string };

export interface ClaudeBucket {
  model: string | null;
  effort: ClaudeEffort;
  fastMode: boolean;
  ultracode: boolean;
  appendSystemPrompt: string;
  skipPermissions: boolean;
  noMemory: boolean;
  planMode: boolean;
  initialPrompt: string;
}

export interface CodexBucket {
  model: string | null;
  sandbox: CodexSandbox;
  approval: CodexApproval;
  fullAuto: boolean;
  reasoningEffort: CodexReasoningEffort | null;
}

export interface AntigravityBucket {
  model: string | null;
  skipPermissions: boolean;
  sandbox: boolean;
  addDirsDraft: string;
}

export interface GitBucket {
  worktree: boolean;
  branchName: string;
}

export interface WizardState {
  /**
   * **Not the source of truth for a launch.** The URL is: this copy is reset by
   * every remount (reload, deep link, Back into the wizard) while the search
   * params survive, so `NewSession.submit()` overwrites it with the URL's
   * selection before the request builder sees it. Reading it directly is how
   * the Create button once became a silent no-op after any reload.
   */
  selection: ProjectSelection;
  customPathDraft: string;
  customPathError: string | null;
  claude: ClaudeBucket;
  /**
   * Auto Scan's own Claude options. Separate from `claude` on purpose — both
   * mobile clients have tests asserting no bleed, because the two are used for
   * very different things and `skipPermissions` defaults the opposite way.
   */
  autoScan: ClaudeBucket;
  codex: CodexBucket;
  antigravity: AntigravityBucket;
  git: GitBucket;
  /** The project the Auto Scan prompt was seeded for, so edits are not discarded. */
  autoScanPromptSeededFor: string | null;
}

const CLAUDE_DEFAULTS: ClaudeBucket = {
  model: null,
  effort: 'high',
  fastMode: false,
  ultracode: false,
  appendSystemPrompt: '',
  // Starts **off**, unlike the wire-absence default of `true`. Both mobile
  // clients do the same; see the comment on `claudeOptionsSchema`.
  skipPermissions: false,
  noMemory: false,
  planMode: false,
  initialPrompt: '',
};

export const INITIAL_WIZARD_STATE: WizardState = {
  selection: { kind: 'none' },
  customPathDraft: '',
  customPathError: null,
  claude: CLAUDE_DEFAULTS,
  autoScan: {
    ...CLAUDE_DEFAULTS,
    // Scans usually run unattended: leaving this off makes the scan stall at a
    // permission prompt and raise an attention notification instead.
    skipPermissions: true,
  },
  codex: {
    model: null,
    // The UI defaults, which are not the wire defaults (`approval` is `never` on
    // the wire). Every field is sent explicitly, so the difference is safe.
    sandbox: 'workspace-write',
    approval: 'on-request',
    fullAuto: false,
    reasoningEffort: 'high',
  },
  antigravity: { model: null, skipPermissions: false, sandbox: false, addDirsDraft: '' },
  git: { worktree: false, branchName: '' },
  autoScanPromptSeededFor: null,
};

export const CLAUDE_MODELS = ['opus', 'sonnet', 'haiku', 'fable'] as const;
export const CODEX_MODELS = [
  'gpt-5.5',
  'gpt-5.4',
  'gpt-5.4-mini',
  'gpt-5.3-codex-spark',
] as const;
export const ANTIGRAVITY_MODELS = [
  'Gemini 3.5 Flash (Medium)',
  'Gemini 3.5 Flash (High)',
  'Gemini 3.5 Flash (Low)',
  'Gemini 3.1 Pro (Low)',
  'Gemini 3.1 Pro (High)',
  'Claude Sonnet 4.6 (Thinking)',
  'Claude Opus 4.6 (Thinking)',
  'GPT-OSS 120B (Medium)',
] as const;

const WIDE_EFFORT_MODELS = new Set(['opus', 'fable', 'sonnet']);

/** `xhigh`/`max` only exist on the larger models (Android `claudeEffortsFor`). */
export function claudeEffortsFor(model: string | null): readonly ClaudeEffort[] {
  if (model === null || WIDE_EFFORT_MODELS.has(model)) {
    return ['low', 'medium', 'high', 'xhigh', 'max'];
  }
  return ['low', 'medium', 'high'];
}

/** Fast mode is Opus-only, so it decides the model rather than merely disabling the select. */
export function effectiveClaudeModel(bucket: ClaudeBucket): string | null {
  return bucket.fastMode ? 'opus' : bucket.model;
}

/**
 * Clamp only.
 *
 * iOS forces `"xhigh"` whenever Ultracode is on, but the agent omits `--effort`
 * entirely in that case (`core/claude_launcher.py`), so the override buys
 * nothing on the wire and loses the user's selection when Ultracode goes back
 * off. Android's behaviour — send what was selected — is the one to keep.
 */
export function effectiveClaudeEffort(bucket: ClaudeBucket): ClaudeEffort {
  const allowed = claudeEffortsFor(effectiveClaudeModel(bucket));
  return allowed.includes(bucket.effort) ? bucket.effort : 'high';
}

/**
 * Which preset button reads as pressed, or `null` for a hand-dialled Advanced
 * combination. `fullAuto` is checked first because it makes the other two moot.
 */
export function codexPreset(bucket: CodexBucket): CodexPreset | null {
  if (bucket.fullAuto) return 'fullAccess';
  if (bucket.approval !== 'on-request') return null;
  if (bucket.sandbox === 'read-only') return 'readOnly';
  if (bucket.sandbox === 'workspace-write') return 'auto';
  return null;
}

/** Split on newlines (any flavour) and commas, trim, drop blanks. iOS's version. */
export function parseAddDirs(draft: string): string[] {
  return draft
    .split(/[\n\r,]/)
    .map((entry) => entry.trim())
    .filter((entry) => entry !== '');
}

/**
 * The server rejects any `add_dirs` entry that looks like a CLI flag, because
 * `add_dirs: ["--dangerously-skip-permissions"]` would otherwise smuggle the
 * blocked flag past the mutual-exclusion check. Checked here so the wizard can
 * both explain it and refuse to submit — showing the message while still
 * sending the entry buys the user a friendly error *and* a raw 422.
 */
export function addDirsError(draft: string): string | null {
  return parseAddDirs(draft).some((dir) => dir.startsWith('-'))
    ? 'Directories must not start with "-"'
    : null;
}

const WINDOWS_ABSOLUTE = /^[A-Za-z]:[\\/]/;

/**
 * Portable absolute-path check, copied from iOS. Android uses JVM `Paths.get`,
 * whose answer depends on the *client's* OS rather than the agent's — a Windows
 * path typed on a phone would be rejected before the agent ever saw it.
 */
export function validateCustomPath(input: string): string | null {
  const trimmed = input.trim();
  if (trimmed === '') return 'Path is required';
  const absolute =
    trimmed.startsWith('/') || WINDOWS_ABSOLUTE.test(trimmed) || trimmed.startsWith('\\\\');
  return absolute ? null : 'Path must be absolute';
}

export type ClaudeBucketId = 'claude' | 'autoScan';

export type WizardAction =
  | { type: 'selectStoredProject'; project: ProjectSummary }
  | { type: 'setCustomPathDraft'; draft: string }
  | { type: 'setClaudeModel'; bucket: ClaudeBucketId; model: string | null }
  | { type: 'setClaudeEffort'; bucket: ClaudeBucketId; effort: ClaudeEffort }
  | {
      type: 'setClaudeFlag';
      bucket: ClaudeBucketId;
      flag: 'fastMode' | 'ultracode' | 'skipPermissions' | 'noMemory' | 'planMode';
      value: boolean;
    }
  | {
      type: 'setClaudeText';
      bucket: ClaudeBucketId;
      field: 'appendSystemPrompt' | 'initialPrompt';
      value: string;
    }
  | { type: 'setCodexPreset'; preset: CodexPreset }
  | { type: 'setCodexModel'; model: string | null }
  | { type: 'setCodexReasoningEffort'; effort: CodexReasoningEffort | null }
  | { type: 'setCodexSandbox'; sandbox: CodexSandbox }
  | { type: 'setCodexApproval'; approval: CodexApproval }
  | { type: 'setAntigravityModel'; model: string | null }
  | { type: 'setAntigravitySkipPermissions'; value: boolean }
  | { type: 'setAntigravitySandbox'; value: boolean }
  | { type: 'setAntigravityAddDirs'; draft: string }
  | { type: 'setWorktree'; value: boolean }
  | { type: 'setBranchName'; value: string }
  | { type: 'seedAutoScanPrompt'; projectName: string; prompt: string };

function withClaudeBucket(
  state: WizardState,
  id: ClaudeBucketId,
  update: (bucket: ClaudeBucket) => ClaudeBucket,
): WizardState {
  return id === 'claude'
    ? { ...state, claude: update(state.claude) }
    : { ...state, autoScan: update(state.autoScan) };
}

export function wizardReducer(state: WizardState, action: WizardAction): WizardState {
  switch (action.type) {
    case 'selectStoredProject':
      return {
        ...state,
        selection: { kind: 'stored', name: action.project.name },
        customPathDraft: '',
        customPathError: null,
        // A non-git project cannot host a worktree, and leaving a stale branch
        // name behind would send it on the next launch.
        git: action.project.isGit ? state.git : { worktree: false, branchName: '' },
      };

    case 'setCustomPathDraft': {
      const error = validateCustomPath(action.draft);
      return {
        ...state,
        customPathDraft: action.draft,
        customPathError: error,
        // The **trimmed** value is what becomes `cwd`; the raw draft stays in
        // the field so the caret does not jump while typing.
        selection: { kind: 'customPath', path: action.draft.trim() },
        git: { worktree: false, branchName: '' },
      };
    }

    case 'setClaudeModel':
      return withClaudeBucket(state, action.bucket, (bucket) => {
        const next = { ...bucket, model: action.model };
        // Dropping to a smaller model can strand an effort it does not support.
        return { ...next, effort: effectiveClaudeEffort(next) };
      });

    case 'setClaudeEffort':
      return withClaudeBucket(state, action.bucket, (bucket) => ({
        ...bucket,
        effort: action.effort,
      }));

    case 'setClaudeFlag':
      return withClaudeBucket(state, action.bucket, (bucket) => {
        const next = { ...bucket, [action.flag]: action.value };
        // Fast mode is Opus-only, so turning it on picks the model. Turning it
        // off leaves that choice in place rather than guessing what to restore.
        if (action.flag === 'fastMode' && action.value) next.model = 'opus';
        return next;
      });

    case 'setClaudeText':
      return withClaudeBucket(state, action.bucket, (bucket) => ({
        ...bucket,
        [action.field]: action.value,
      }));

    case 'setCodexPreset':
      switch (action.preset) {
        case 'readOnly':
          return {
            ...state,
            codex: {
              ...state.codex,
              sandbox: 'read-only',
              approval: 'on-request',
              fullAuto: false,
            },
          };
        case 'auto':
          return {
            ...state,
            codex: {
              ...state.codex,
              sandbox: 'workspace-write',
              approval: 'on-request',
              fullAuto: false,
            },
          };
        default:
          // Only `full_auto` moves. The agent omits `--sandbox` and
          // `--ask-for-approval` entirely under `--yolo`, so overwriting them
          // (as iOS does) changes nothing on the wire and destroys the values
          // the user set in Advanced.
          return { ...state, codex: { ...state.codex, fullAuto: true } };
      }

    case 'setCodexModel':
      return { ...state, codex: { ...state.codex, model: action.model } };

    case 'setCodexReasoningEffort':
      return { ...state, codex: { ...state.codex, reasoningEffort: action.effort } };

    case 'setCodexSandbox':
      return { ...state, codex: { ...state.codex, sandbox: action.sandbox } };

    case 'setCodexApproval':
      return { ...state, codex: { ...state.codex, approval: action.approval } };

    case 'setAntigravityModel':
      return { ...state, antigravity: { ...state.antigravity, model: action.model } };

    // The server 422s the pair, so the two switches exclude each other here as
    // well as being disabled in the UI while the other is on.
    case 'setAntigravitySkipPermissions':
      return {
        ...state,
        antigravity: {
          ...state.antigravity,
          skipPermissions: action.value,
          sandbox: action.value ? false : state.antigravity.sandbox,
        },
      };

    case 'setAntigravitySandbox':
      return {
        ...state,
        antigravity: {
          ...state.antigravity,
          sandbox: action.value,
          skipPermissions: action.value ? false : state.antigravity.skipPermissions,
        },
      };

    case 'setAntigravityAddDirs':
      return { ...state, antigravity: { ...state.antigravity, addDirsDraft: action.draft } };

    case 'setWorktree':
      return { ...state, git: { ...state.git, worktree: action.value } };

    case 'setBranchName':
      return { ...state, git: { ...state.git, branchName: action.value } };

    case 'seedAutoScanPrompt':
      // Seeded once per project: re-entering the step must not discard edits,
      // but changing project must re-seed from the new project's saved prompt.
      if (state.autoScanPromptSeededFor === action.projectName) return state;
      return {
        ...state,
        autoScanPromptSeededFor: action.projectName,
        autoScan: { ...state.autoScan, initialPrompt: action.prompt },
      };
  }
}
