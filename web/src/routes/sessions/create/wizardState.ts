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
import { CODEX_REASONING_EFFORTS } from '../../../protocol/dto/sessionCreate';
import type { CLAUDE_EFFORTS, CODEX_APPROVALS, CODEX_SANDBOXES } from '../../../protocol/dto/sessionCreate';
import type { CliModelDetail } from '../../../protocol/dto/agentVersion';
import type {
  AutoScanFilter,
  AutoScanSettings,
} from '../../../protocol/dto/workspaces';
import type { WorkspaceSummary } from '../../../api/workspaceRepository';
import { trimTrailingSeparator } from '../../../lib/pathCompletion';
import type { WorktreeBasePick } from './worktreeBase';

export type WizardStep = 'workspace' | 'mode' | 'config';

export const BUILT_IN_MODES = [
  'claude',
  'codex',
  'antigravity',
  'opencode',
  'copilot',
  'cursor',
  'junie',
  'pi',
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
/**
 * Free-form, matching the wire schema: the effort vocabulary is agent-enumerated
 * per model (`cliModelDetails`), so a closed union would strand advertised
 * values like `ultra`. `CODEX_REASONING_EFFORTS` remains the fallback UI list.
 */
export type CodexReasoningEffort = string;
export type CodexPreset = 'readOnly' | 'auto' | 'fullAccess';

export type WorkspaceSelection =
  | { kind: 'none' }
  | { kind: 'stored'; name: string }
  /** Already trimmed — `customPathDraft` keeps whatever the user is typing. */
  | { kind: 'customPath'; path: string };

export interface ClaudeBucket {
  model: string | null;
  effort: ClaudeEffort;
  fastMode: boolean;
  ultracode: boolean;
  debug: boolean;
  focusMode: boolean;
  appendSystemPrompt: string;
  skipPermissions: boolean;
  noMemory: boolean;
  planMode: boolean;
  initialPrompt: string;
  /** Claude Code only: the classic terminal instead of the chat view. */
  terminalUi: boolean;
  /**
   * Claude Code only: the experimental Code Mode. It runs in the terminal UI
   * only, so it also opens the session there.
   */
  codeMode: boolean;
}

export interface CodexBucket {
  model: string | null;
  sandbox: CodexSandbox;
  approval: CodexApproval;
  fullAuto: boolean;
  reasoningEffort: CodexReasoningEffort | null;
  /** The classic terminal instead of the chat view. */
  terminalUi: boolean;
}

export interface AntigravityBucket {
  model: string | null;
  skipPermissions: boolean;
  sandbox: boolean;
  addDirsDraft: string;
  /** The classic terminal instead of the chat view. */
  terminalUi: boolean;
}

export interface OpenCodeBucket {
  /** OpenCode's `--auto`: accept every permission request its rules do not deny. */
  autoApprove: boolean;
  /** The classic terminal instead of the chat view. */
  terminalUi: boolean;
  /** OpenCode 2's Code Mode (MCP tools called from JavaScript); on in OpenCode by default. */
  codeMode: boolean;
}

export interface CopilotBucket {
  /** Copilot's `--allow-all`: run tools and use paths and URLs without asking. */
  allowAll: boolean;
  /** The classic terminal instead of the chat view. */
  terminalUi: boolean;
}

export interface CursorBucket {
  /** Cursor's `--force` ("Run Everything"): run commands without asking. */
  force: boolean;
  /** The classic terminal instead of the chat view. */
  terminalUi: boolean;
}

export interface JunieBucket {
  /** Start in Junie's Plan mode: a plan to approve before any change. */
  planMode: boolean;
  /** Junie's `--brave`: run commands without asking, in this session only. */
  brave: boolean;
  /** The classic terminal instead of the chat view. */
  terminalUi: boolean;
}

/** Pi has no approval mode to choose: it runs its tools without asking. */
export interface PiBucket {
  /** The classic terminal instead of the chat view. */
  terminalUi: boolean;
  /** Pi's `codemode` tool: the model writes JavaScript that calls the other tools. */
  codeMode: boolean;
}

export interface GitBucket {
  worktree: boolean;
  branchName: string;
}

export type AutoScanSource = 'linear' | 'github';

/** How a scan opens each issue's session. */
export type AutoScanUi = 'terminal' | 'chat';

/** The wait between two scan cycles, in minutes, when nothing else is chosen. */
export const AUTO_SCAN_DEFAULT_INTERVAL_MINUTES = 5;
/** `interval_minutes` bounds on the wire. */
export const AUTO_SCAN_INTERVAL_MIN_MINUTES = 1;
export const AUTO_SCAN_INTERVAL_MAX_MINUTES = 1440;

function validInterval(value: number | null | undefined): number | null {
  if (typeof value !== 'number' || !Number.isInteger(value)) return null;
  return value >= AUTO_SCAN_INTERVAL_MIN_MINUTES && value <= AUTO_SCAN_INTERVAL_MAX_MINUTES
    ? value
    : null;
}

/** The GitHub filter as the wizard edits it: blank text is "not set". */
export interface GitHubFilterDraft {
  labels: string[];
  assignee: string;
  activeLabel: string;
  /** GitHub issue types to take; empty takes any type. */
  issueTypes: string[];
}

/**
 * What a scan runs on and with (Delta 2026-11-18), for an agent that has
 * `capabilities.autoscan`. Seeded once per workspace from the agent's saved
 * choices (`workspace.auto_scan`), which start empty: nothing here has a
 * default the user did not pick.
 */
export interface AutoScanSetup {
  /** The workspace whose saved choices seeded this slice, so edits survive re-entry. */
  seededFor: string | null;
  source: AutoScanSource | null;
  /** An agent kind from `capabilities.autoscan.clis`. */
  cli: string | null;
  /** Empty strings and lists are "not chosen yet"; `filterError` names what is missing. */
  linearFilter: AutoScanFilter;
  githubFilter: GitHubFilterDraft;
  /** Terminal or chat view for each issue's session; the wire's `ui`. */
  ui: AutoScanUi;
  /** Minutes between two scan cycles; the wire's `interval_minutes`. */
  intervalMinutes: number;
}

/**
 * The options Auto Scan runs each non-Claude agent with. Separate from the
 * normal-session buckets for the reason `autoScan` is separate from `claude`:
 * a scan runs unattended, so its "run without asking" switches start on, and
 * editing one here must never change a normal session's options. The
 * `terminalUi` fields are unused here: the view is `AutoScanSetup.ui`, one
 * choice for every agent. Pi has no options.
 */
export interface AutoScanAgentBuckets {
  codex: CodexBucket;
  antigravity: AntigravityBucket;
  opencode: OpenCodeBucket;
  copilot: CopilotBucket;
  cursor: CursorBucket;
  junie: JunieBucket;
}

/** Which copy of an agent's options an action edits: the normal session's, or Auto Scan's. */
export type AgentBucketTarget = 'session' | 'autoScan';

export const EMPTY_LINEAR_FILTER: AutoScanFilter = {
  state_names: [],
  label_names: [],
  active_state: '',
  done_state: '',
  removal_label: '',
};

export const EMPTY_GITHUB_FILTER: GitHubFilterDraft = {
  labels: [],
  assignee: '',
  activeLabel: '',
  issueTypes: [],
};

function githubFilterDraft(filter: AutoScanGitHubFilterInput): GitHubFilterDraft {
  return {
    labels: [...(filter.labels ?? [])],
    assignee: filter.assignee ?? '',
    activeLabel: filter.active_label ?? '',
    issueTypes: [...(filter.issue_types ?? [])],
  };
}

/** The Auto Scan part of the wizard that survives a trip to the GitHub setup terminal. */
export interface AutoScanDraft {
  setup: AutoScanSetup;
  bucket: ClaudeBucket;
  agents: AutoScanAgentBuckets;
}

export interface WizardState {
  /**
   * **Not the source of truth for a launch.** The URL is: this copy is reset by
   * every remount (reload, deep link, Back into the wizard) while the search
   * params survive, so `NewSession.submit()` overwrites it with the URL's
   * selection before the request builder sees it. Reading it directly is how
   * the Create button once became a silent no-op after any reload.
   */
  selection: WorkspaceSelection;
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
  opencode: OpenCodeBucket;
  copilot: CopilotBucket;
  cursor: CursorBucket;
  junie: JunieBucket;
  pi: PiBucket;
  git: GitBucket;
  /**
   * The base branch the user picked by hand, if any. Not persisted: the agent
   * remembers the last base per workspace and returns it as `last_used`.
   */
  worktreeBasePick: WorktreeBasePick | null;
  /** The workspace the Auto Scan prompt was seeded for, so edits are not discarded. */
  autoScanPromptSeededFor: string | null;
  autoScanSetup: AutoScanSetup;
  autoScanAgents: AutoScanAgentBuckets;
}

const CLAUDE_DEFAULTS: ClaudeBucket = {
  model: null,
  effort: 'high',
  fastMode: false,
  ultracode: false,
  debug: false,
  focusMode: true,
  appendSystemPrompt: '',
  // Starts **off**, unlike the wire-absence default of `true`. Both mobile
  // clients do the same; see the comment on `claudeOptionsSchema`.
  skipPermissions: false,
  noMemory: false,
  planMode: false,
  initialPrompt: '',
  terminalUi: false,
  codeMode: false,
};

/**
 * Auto Scan's agent defaults when nothing is saved: each agent runs without
 * asking, the same unattended default Claude's `skipPermissions` has in the
 * `autoScan` bucket, and the agent's own default when a start sends no
 * options (PROTOCOL.md, "Agent options").
 */
export const AUTO_SCAN_AGENT_DEFAULTS: AutoScanAgentBuckets = {
  codex: {
    model: null,
    sandbox: 'workspace-write',
    approval: 'never',
    fullAuto: true,
    reasoningEffort: 'high',
    terminalUi: false,
  },
  antigravity: {
    model: null,
    skipPermissions: true,
    sandbox: false,
    addDirsDraft: '',
    terminalUi: false,
  },
  // Code Mode is per launch: an Auto Scan never sends it.
  opencode: { autoApprove: true, terminalUi: false, codeMode: true },
  copilot: { allowAll: true, terminalUi: false },
  cursor: { force: true, terminalUi: false },
  junie: { planMode: false, brave: true, terminalUi: false },
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
    terminalUi: false,
  },
  antigravity: {
    model: null,
    skipPermissions: false,
    sandbox: false,
    addDirsDraft: '',
    terminalUi: false,
  },
  opencode: { autoApprove: false, terminalUi: false, codeMode: true },
  copilot: { allowAll: false, terminalUi: false },
  cursor: { force: false, terminalUi: false },
  junie: { planMode: false, brave: false, terminalUi: false },
  pi: { terminalUi: false, codeMode: false },
  git: { worktree: false, branchName: '' },
  worktreeBasePick: null,
  autoScanPromptSeededFor: null,
  autoScanSetup: {
    seededFor: null,
    source: null,
    cli: null,
    linearFilter: EMPTY_LINEAR_FILTER,
    githubFilter: EMPTY_GITHUB_FILTER,
    ui: 'chat',
    intervalMinutes: AUTO_SCAN_DEFAULT_INTERVAL_MINUTES,
  },
  autoScanAgents: AUTO_SCAN_AGENT_DEFAULTS,
};

export const CLAUDE_MODELS = ['opus', 'sonnet', 'haiku', 'fable'] as const;
export const CODEX_MODELS = [
  'gpt-5.6-sol',
  'gpt-5.6-terra',
  'gpt-5.6-luna',
  'gpt-5.5',
  'gpt-5.4',
  'gpt-5.4-mini',
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

/**
 * `xhigh`/`max` only exist on the larger models.
 *
 * The Default (`null`) model takes the **base** ladder, matching Android's
 * `claudeEffortsFor` and iOS's `claudeEfforts(for:)` — both key strictly on
 * `opus`/`fable`/`sonnet`. Whichever model the agent resolves Default to is
 * unknown here, and `effectiveClaudeEffort` clamps through this same helper, so
 * widening it would put an effort on the wire that the other two clients never
 * send.
 */
export function claudeEffortsFor(model: string | null): readonly ClaudeEffort[] {
  return model !== null && WIDE_EFFORT_MODELS.has(model)
    ? ['low', 'medium', 'high', 'xhigh', 'max']
    : ['low', 'medium', 'high'];
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
 * The effort options for the selected Codex model: the agent-advertised set
 * when `model_details` knows the model, else the static fallback list.
 *
 * The Default (`null`) model always takes the fallback — which model the agent
 * resolves it to is unknown here, so narrowing would be a guess. Mirrors
 * `claudeEffortsFor`'s posture on the null model.
 */
export function codexEffortsFor(
  details: readonly CliModelDetail[] | null,
  model: string | null,
): readonly string[] {
  if (model !== null) {
    const detail = details?.find((entry) => entry.id === model);
    if (detail !== undefined) return detail.efforts;
  }
  return CODEX_REASONING_EFFORTS;
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

/*
 * Auto Scan choices as they arrive from the agent: a workspace's saved
 * `auto_scan` (plus its `auto_scan_prompt`) or a template's `settings`. Every
 * field may be absent or `null`, which keeps what the wizard has.
 */
export interface AutoScanLinearFilterInput {
  state_names?: readonly string[] | null;
  label_names?: readonly string[] | null;
  active_state?: string | null;
  done_state?: string | null;
  removal_label?: string | null;
}

export interface AutoScanGitHubFilterInput {
  labels?: readonly string[] | null;
  assignee?: string | null;
  active_label?: string | null;
  issue_types?: readonly string[] | null;
}

export interface ClaudeScanOptionsInput {
  plan_mode?: boolean | null;
  no_memory?: boolean | null;
  skip_permissions?: boolean | null;
  model?: string | null;
  effort?: ClaudeEffort | null;
  fast_mode?: boolean | null;
  ultracode?: boolean | null;
  append_system_prompt?: string | null;
  debug?: boolean | null;
  focus_mode?: boolean | null;
}

export interface CodexScanOptionsInput {
  model?: string | null;
  sandbox?: CodexSandbox | null;
  approval?: CodexApproval | null;
  reasoning_effort?: string | null;
  full_auto?: boolean | null;
}

export interface AntigravityScanOptionsInput {
  model?: string | null;
  skip_permissions?: boolean | null;
  sandbox?: boolean | null;
  add_dirs?: readonly string[] | null;
}

export interface AutoScanSettingsInput {
  source?: string | null;
  cli?: string | null;
  ui?: string | null;
  interval_minutes?: number | null;
  initial_prompt?: string | null;
  linear_filter?: AutoScanLinearFilterInput | null;
  github_filter?: AutoScanGitHubFilterInput | null;
  claude_options?: ClaudeScanOptionsInput | null;
  codex_options?: CodexScanOptionsInput | null;
  antigravity_options?: AntigravityScanOptionsInput | null;
  opencode_options?: { auto_approve?: boolean | null } | null;
  copilot_options?: { allow_all?: boolean | null } | null;
  cursor_options?: { force?: boolean | null } | null;
  junie_options?: { plan_mode?: boolean | null; brave?: boolean | null } | null;
}

function linearFilterFromInput(filter: AutoScanLinearFilterInput): AutoScanFilter {
  return {
    state_names: [...(filter.state_names ?? [])],
    label_names: [...(filter.label_names ?? [])],
    active_state: filter.active_state ?? '',
    done_state: filter.done_state ?? '',
    removal_label: filter.removal_label ?? '',
  };
}

/*
 * One agent's options object is a full set: a field it lacks takes the Auto
 * Scan default, never what the bucket held before.
 */
function claudeFromScan(bucket: ClaudeBucket, options: ClaudeScanOptionsInput): ClaudeBucket {
  const next: ClaudeBucket = {
    ...bucket,
    model: options.model ?? null,
    effort: options.effort ?? 'high',
    fastMode: options.fast_mode ?? false,
    ultracode: options.ultracode ?? false,
    debug: options.debug ?? false,
    // `null` leaves the CLI's own view setting alone; the toggle keeps its state.
    focusMode: options.focus_mode ?? bucket.focusMode,
    appendSystemPrompt: options.append_system_prompt ?? '',
    skipPermissions: options.skip_permissions ?? true,
    noMemory: options.no_memory ?? false,
    planMode: options.plan_mode ?? false,
  };
  return { ...next, effort: effectiveClaudeEffort(next) };
}

function codexFromScan(options: CodexScanOptionsInput): CodexBucket {
  const defaults = AUTO_SCAN_AGENT_DEFAULTS.codex;
  return {
    model: options.model ?? null,
    sandbox: options.sandbox ?? defaults.sandbox,
    approval: options.approval ?? defaults.approval,
    fullAuto: options.full_auto ?? defaults.fullAuto,
    // Three-state: `null` is "Agent default", only an absent field defaults.
    reasoningEffort:
      options.reasoning_effort === undefined ? defaults.reasoningEffort : options.reasoning_effort,
    terminalUi: false,
  };
}

function antigravityFromScan(options: AntigravityScanOptionsInput): AntigravityBucket {
  const skipPermissions =
    options.skip_permissions ?? AUTO_SCAN_AGENT_DEFAULTS.antigravity.skipPermissions;
  return {
    model: options.model ?? null,
    skipPermissions,
    // The server 422s the pair, so the interlock is re-applied on the way in.
    sandbox: (options.sandbox ?? false) && !skipPermissions,
    addDirsDraft: (options.add_dirs ?? []).join('\n'),
    terminalUi: false,
  };
}

/**
 * Apply saved Auto Scan choices — a workspace's last start or a template — to
 * the wizard. Each field that is present and not `null` replaces what the
 * wizard has; the rest stay. The one path both seeding and templates take, so
 * the two cannot read the same settings differently.
 */
export function applyAutoScanSettings(
  state: WizardState,
  settings: AutoScanSettingsInput,
): WizardState {
  const setup = state.autoScanSetup;
  const agents = state.autoScanAgents;
  const source =
    settings.source === 'linear' || settings.source === 'github' ? settings.source : null;
  const cli = settings.cli?.trim() ?? '';
  const ui = settings.ui === 'terminal' || settings.ui === 'chat' ? settings.ui : null;
  const interval = validInterval(settings.interval_minutes);
  const prompt = settings.initial_prompt;
  const claude =
    prompt === undefined || prompt === null
      ? state.autoScan
      : { ...state.autoScan, initialPrompt: prompt };
  const { opencode_options: opencode, copilot_options: copilot } = settings;
  const { cursor_options: cursor, junie_options: junie } = settings;
  return {
    ...state,
    autoScan: settings.claude_options != null ? claudeFromScan(claude, settings.claude_options) : claude,
    autoScanSetup: {
      ...setup,
      source: source ?? setup.source,
      cli: cli !== '' ? cli : setup.cli,
      ui: ui ?? setup.ui,
      intervalMinutes: interval ?? setup.intervalMinutes,
      linearFilter:
        settings.linear_filter != null
          ? linearFilterFromInput(settings.linear_filter)
          : setup.linearFilter,
      githubFilter:
        settings.github_filter != null
          ? githubFilterDraft(settings.github_filter)
          : setup.githubFilter,
    },
    autoScanAgents: {
      codex: settings.codex_options != null ? codexFromScan(settings.codex_options) : agents.codex,
      antigravity:
        settings.antigravity_options != null
          ? antigravityFromScan(settings.antigravity_options)
          : agents.antigravity,
      opencode:
        opencode != null
          ? {
              ...AUTO_SCAN_AGENT_DEFAULTS.opencode,
              autoApprove: opencode.auto_approve ?? AUTO_SCAN_AGENT_DEFAULTS.opencode.autoApprove,
            }
          : agents.opencode,
      copilot:
        copilot != null
          ? {
              allowAll: copilot.allow_all ?? AUTO_SCAN_AGENT_DEFAULTS.copilot.allowAll,
              terminalUi: false,
            }
          : agents.copilot,
      cursor:
        cursor != null
          ? { force: cursor.force ?? AUTO_SCAN_AGENT_DEFAULTS.cursor.force, terminalUi: false }
          : agents.cursor,
      junie:
        junie != null
          ? {
              planMode: junie.plan_mode ?? AUTO_SCAN_AGENT_DEFAULTS.junie.planMode,
              brave: junie.brave ?? AUTO_SCAN_AGENT_DEFAULTS.junie.brave,
              terminalUi: false,
            }
          : agents.junie,
    },
  };
}

export type WizardAction =
  | { type: 'selectStoredWorkspace'; workspace: WorkspaceSummary }
  | { type: 'setCustomPathDraft'; draft: string }
  | { type: 'syncCustomPathFromUrl'; path: string | null }
  | { type: 'setClaudeModel'; bucket: ClaudeBucketId; model: string | null }
  | { type: 'setClaudeEffort'; bucket: ClaudeBucketId; effort: ClaudeEffort }
  | {
      type: 'setClaudeFlag';
      bucket: ClaudeBucketId;
      flag:
        | 'fastMode'
        | 'ultracode'
        | 'debug'
        | 'focusMode'
        | 'skipPermissions'
        | 'noMemory'
        | 'planMode'
        | 'terminalUi'
        | 'codeMode';
      value: boolean;
    }
  | {
      type: 'setClaudeText';
      bucket: ClaudeBucketId;
      field: 'appendSystemPrompt' | 'initialPrompt';
      value: string;
    }
  /*
   * The agent option actions edit the normal session's bucket, or Auto Scan's
   * copy with `target: 'autoScan'` — the panels are shared, the values never.
   */
  | { type: 'setCodexPreset'; preset: CodexPreset; target?: AgentBucketTarget }
  | {
      type: 'setCodexModel';
      model: string | null;
      detail?: CliModelDetail | null;
      target?: AgentBucketTarget;
    }
  | { type: 'reconcileCodexEffort'; details: readonly CliModelDetail[] | null }
  | {
      type: 'setCodexReasoningEffort';
      effort: CodexReasoningEffort | null;
      target?: AgentBucketTarget;
    }
  | { type: 'setCodexSandbox'; sandbox: CodexSandbox; target?: AgentBucketTarget }
  | { type: 'setCodexApproval'; approval: CodexApproval; target?: AgentBucketTarget }
  | { type: 'setCodexTerminalUi'; value: boolean }
  | { type: 'setAntigravityModel'; model: string | null; target?: AgentBucketTarget }
  | { type: 'setAntigravitySkipPermissions'; value: boolean; target?: AgentBucketTarget }
  | { type: 'setAntigravitySandbox'; value: boolean; target?: AgentBucketTarget }
  | { type: 'setAntigravityAddDirs'; draft: string; target?: AgentBucketTarget }
  | { type: 'setAntigravityTerminalUi'; value: boolean }
  | { type: 'setOpenCodeAutoApprove'; value: boolean; target?: AgentBucketTarget }
  | { type: 'setOpenCodeTerminalUi'; value: boolean }
  | { type: 'setOpenCodeCodeMode'; value: boolean }
  | { type: 'setCopilotAllowAll'; value: boolean; target?: AgentBucketTarget }
  | { type: 'setCopilotTerminalUi'; value: boolean }
  | { type: 'setCursorForce'; value: boolean; target?: AgentBucketTarget }
  | { type: 'setCursorTerminalUi'; value: boolean }
  | { type: 'setJuniePlanMode'; value: boolean; target?: AgentBucketTarget }
  | { type: 'setJunieBrave'; value: boolean; target?: AgentBucketTarget }
  | { type: 'setJunieTerminalUi'; value: boolean }
  | { type: 'setPiTerminalUi'; value: boolean }
  | { type: 'setPiCodeMode'; value: boolean }
  | { type: 'setWorktree'; value: boolean }
  | { type: 'setBranchName'; value: string }
  | { type: 'setWorktreeBase'; pick: WorktreeBasePick }
  | { type: 'seedAutoScanPrompt'; seedKey: string; prompt: string }
  | { type: 'seedAutoScanSetup'; seedKey: string; saved: AutoScanSettings | undefined }
  | { type: 'restoreAutoScanDraft'; seedKey: string; draft: AutoScanDraft }
  | { type: 'applyAutoScanSettings'; settings: AutoScanSettingsInput }
  | { type: 'setAutoScanSource'; source: AutoScanSource }
  | { type: 'setAutoScanCli'; cli: string }
  | { type: 'setAutoScanUi'; ui: AutoScanUi }
  | { type: 'setAutoScanInterval'; minutes: number }
  | { type: 'setAutoScanLinearFilter'; filter: AutoScanFilter }
  | { type: 'setAutoScanGitHubFilter'; filter: GitHubFilterDraft };

function withClaudeBucket(
  state: WizardState,
  id: ClaudeBucketId,
  update: (bucket: ClaudeBucket) => ClaudeBucket,
): WizardState {
  return id === 'claude'
    ? { ...state, claude: update(state.claude) }
    : { ...state, autoScan: update(state.autoScan) };
}

/** `update` applied to the normal session's bucket of `kind`, or to Auto Scan's copy. */
function withAgentBucket<K extends keyof AutoScanAgentBuckets>(
  state: WizardState,
  kind: K,
  target: AgentBucketTarget | undefined,
  update: (bucket: AutoScanAgentBuckets[K]) => AutoScanAgentBuckets[K],
): WizardState {
  if (target === 'autoScan') {
    return {
      ...state,
      autoScanAgents: { ...state.autoScanAgents, [kind]: update(state.autoScanAgents[kind]) },
    };
  }
  const session: AutoScanAgentBuckets = state;
  return { ...state, [kind]: update(session[kind]) };
}

/**
 * The Auto Scan seed identity: workspace names repeat across agents, and the
 * wizard stays mounted when the route switches to another agent.
 */
export function autoScanSeedKey(agentId: string, workspaceName: string): string {
  return JSON.stringify([agentId, workspaceName]);
}

/**
 * Re-default a Codex effort the selected model cannot serve. No `model_details`
 * → possibly a pre-catalog build whose wire schema still closes over the
 * static vocabulary, so anything outside it drops to `high`. A model without a
 * catalog entry (or the null Default model) keeps the effort; the null "Agent
 * default" effort always stands.
 */
function reconcileCodexBucket(
  bucket: CodexBucket,
  details: readonly CliModelDetail[] | null,
): CodexBucket {
  const effort = bucket.reasoningEffort;
  if (effort === null) return bucket;
  if (details === null) {
    if ((CODEX_REASONING_EFFORTS as readonly string[]).includes(effort)) return bucket;
    return { ...bucket, reasoningEffort: 'high' };
  }
  const model = bucket.model;
  const detail = model !== null ? details.find((entry) => entry.id === model) : undefined;
  if (detail === undefined || detail.efforts.includes(effort)) return bucket;
  const next =
    detail.defaultEffort ??
    (detail.efforts.includes('high') ? 'high' : (detail.efforts[0] ?? effort));
  return { ...bucket, reasoningEffort: next };
}

export function wizardReducer(state: WizardState, action: WizardAction): WizardState {
  switch (action.type) {
    case 'selectStoredWorkspace':
      return {
        ...state,
        selection: { kind: 'stored', name: action.workspace.name },
        customPathDraft: '',
        customPathError: null,
        // A non-git workspace cannot host a worktree, and leaving a stale branch
        // name behind would send it on the next launch.
        git: action.workspace.is_git ? state.git : { worktree: false, branchName: '' },
        worktreeBasePick:
          state.worktreeBasePick?.workspaceName === action.workspace.name
            ? state.worktreeBasePick
            : null,
      };

    case 'setCustomPathDraft': {
      const error = validateCustomPath(action.draft);
      return {
        ...state,
        customPathDraft: action.draft,
        customPathError: error,
        // The **trimmed** value is what becomes `cwd` — without the trailing
        // separator a picked folder suggestion leaves — and the raw draft stays in
        // the field so the caret does not jump while typing.
        selection: { kind: 'customPath', path: trimTrailingSeparator(action.draft.trim()) },
        git: { worktree: false, branchName: '' },
        worktreeBasePick: null,
      };
    }

    // Distinct from `setCustomPathDraft`: this reconciles *from* the URL after a
    // history navigation, so a null path (the entry selected a stored workspace,
    // or nothing) must clear a stale draft rather than leave it enabling
    // Continue for a directory this entry never named. Clearing must not raise
    // "Path is required" — the field is optional and nobody typed in it.
    case 'syncCustomPathFromUrl':
      if (action.path === null) {
        return state.customPathDraft === ''
          ? state
          : { ...state, customPathDraft: '', customPathError: null };
      }
      return {
        ...state,
        customPathDraft: action.path,
        customPathError: validateCustomPath(action.path),
        selection: { kind: 'customPath', path: action.path },
      };

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
      return withAgentBucket(state, 'codex', action.target, (codex) => {
        switch (action.preset) {
          case 'readOnly':
            return { ...codex, sandbox: 'read-only', approval: 'on-request', fullAuto: false };
          case 'auto':
            return { ...codex, sandbox: 'workspace-write', approval: 'on-request', fullAuto: false };
          default:
            // Only `full_auto` moves. The agent omits `--sandbox` and
            // `--ask-for-approval` entirely under `--yolo`, so overwriting them
            // (as iOS does) changes nothing on the wire and destroys the values
            // the user set in Advanced.
            return { ...codex, fullAuto: true };
        }
      });

    case 'setCodexModel':
      return withAgentBucket(state, 'codex', action.target, (codex) => {
        const detail = action.detail ?? null;
        let effort = codex.reasoningEffort;
        // Switching to a model with a known effort set can strand an effort it
        // does not support — re-default to the catalog's per-model default (the
        // Claude clamp pattern with the target upgraded from hardcoded 'high').
        // The null "Agent default" effort is always kept, as is everything when
        // the new model's capabilities are unknown.
        if (detail !== null && effort !== null && !detail.efforts.includes(effort)) {
          effort =
            detail.defaultEffort ??
            (detail.efforts.includes('high') ? 'high' : (detail.efforts[0] ?? effort));
        }
        return { ...codex, model: action.model, reasoningEffort: effort };
      });

    case 'setCodexReasoningEffort':
      return withAgentBucket(state, 'codex', action.target, (codex) => ({
        ...codex,
        reasoningEffort: action.effort,
      }));

    // Dispatched when the capabilities probe resolves, reconciling a restored
    // effort against what this agent can actually accept — in both copies.
    case 'reconcileCodexEffort': {
      const codex = reconcileCodexBucket(state.codex, action.details);
      const scanCodex = reconcileCodexBucket(state.autoScanAgents.codex, action.details);
      if (codex === state.codex && scanCodex === state.autoScanAgents.codex) return state;
      return { ...state, codex, autoScanAgents: { ...state.autoScanAgents, codex: scanCodex } };
    }

    case 'setCodexSandbox':
      return withAgentBucket(state, 'codex', action.target, (codex) => ({
        ...codex,
        sandbox: action.sandbox,
      }));

    case 'setCodexApproval':
      return withAgentBucket(state, 'codex', action.target, (codex) => ({
        ...codex,
        approval: action.approval,
      }));

    case 'setCodexTerminalUi':
      return { ...state, codex: { ...state.codex, terminalUi: action.value } };

    case 'setAntigravityModel':
      return withAgentBucket(state, 'antigravity', action.target, (antigravity) => ({
        ...antigravity,
        model: action.model,
      }));

    // The server 422s the pair, so the two switches exclude each other here as
    // well as being disabled in the UI while the other is on.
    case 'setAntigravitySkipPermissions':
      return withAgentBucket(state, 'antigravity', action.target, (antigravity) => ({
        ...antigravity,
        skipPermissions: action.value,
        sandbox: action.value ? false : antigravity.sandbox,
      }));

    case 'setAntigravitySandbox':
      return withAgentBucket(state, 'antigravity', action.target, (antigravity) => ({
        ...antigravity,
        sandbox: action.value,
        skipPermissions: action.value ? false : antigravity.skipPermissions,
      }));

    case 'setAntigravityAddDirs':
      return withAgentBucket(state, 'antigravity', action.target, (antigravity) => ({
        ...antigravity,
        addDirsDraft: action.draft,
      }));

    case 'setAntigravityTerminalUi':
      return { ...state, antigravity: { ...state.antigravity, terminalUi: action.value } };

    case 'setOpenCodeAutoApprove':
      return withAgentBucket(state, 'opencode', action.target, (opencode) => ({
        ...opencode,
        autoApprove: action.value,
      }));

    case 'setOpenCodeTerminalUi':
      return { ...state, opencode: { ...state.opencode, terminalUi: action.value } };

    case 'setOpenCodeCodeMode':
      return { ...state, opencode: { ...state.opencode, codeMode: action.value } };

    case 'setCopilotAllowAll':
      return withAgentBucket(state, 'copilot', action.target, (copilot) => ({
        ...copilot,
        allowAll: action.value,
      }));

    case 'setCopilotTerminalUi':
      return { ...state, copilot: { ...state.copilot, terminalUi: action.value } };

    case 'setCursorForce':
      return withAgentBucket(state, 'cursor', action.target, (cursor) => ({
        ...cursor,
        force: action.value,
      }));

    case 'setCursorTerminalUi':
      return { ...state, cursor: { ...state.cursor, terminalUi: action.value } };

    case 'setJuniePlanMode':
      return withAgentBucket(state, 'junie', action.target, (junie) => ({
        ...junie,
        planMode: action.value,
      }));

    case 'setJunieBrave':
      return withAgentBucket(state, 'junie', action.target, (junie) => ({
        ...junie,
        brave: action.value,
      }));

    case 'setJunieTerminalUi':
      return { ...state, junie: { ...state.junie, terminalUi: action.value } };

    case 'setPiTerminalUi':
      return { ...state, pi: { ...state.pi, terminalUi: action.value } };

    case 'setPiCodeMode':
      return { ...state, pi: { ...state.pi, codeMode: action.value } };

    case 'setWorktree':
      return { ...state, git: { ...state.git, worktree: action.value } };

    case 'setBranchName':
      return { ...state, git: { ...state.git, branchName: action.value } };

    case 'setWorktreeBase':
      return { ...state, worktreeBasePick: action.pick };

    case 'seedAutoScanPrompt':
      // Seeded once per agent and workspace (`autoScanSeedKey`): re-entering the
      // step must not discard edits, but another agent or workspace re-seeds.
      if (state.autoScanPromptSeededFor === action.seedKey) return state;
      return {
        ...applyAutoScanSettings(state, { initial_prompt: action.prompt }),
        autoScanPromptSeededFor: action.seedKey,
      };

    // Same once-per-workspace rule as the prompt. A choice the agent never saved
    // stays unset — no legacy filter defaults, no default source or agent — and
    // an agent whose options were never saved gets the Auto Scan defaults.
    case 'seedAutoScanSetup':
      if (state.autoScanSetup.seededFor === action.seedKey) return state;
      return applyAutoScanSettings(
        {
          ...state,
          autoScanSetup: {
            seededFor: action.seedKey,
            source: null,
            cli: null,
            linearFilter: EMPTY_LINEAR_FILTER,
            githubFilter: EMPTY_GITHUB_FILTER,
            ui: 'chat',
            intervalMinutes: AUTO_SCAN_DEFAULT_INTERVAL_MINUTES,
          },
          autoScanAgents: AUTO_SCAN_AGENT_DEFAULTS,
        },
        action.saved ?? {},
      );

    // The draft parked before the GitHub setup terminal opened. It marks both
    // seeds as done, so the saved choices cannot overwrite it afterwards.
    case 'restoreAutoScanDraft':
      return {
        ...state,
        autoScan: action.draft.bucket,
        autoScanPromptSeededFor: action.seedKey,
        autoScanSetup: { ...action.draft.setup, seededFor: action.seedKey },
        autoScanAgents: action.draft.agents,
      };

    case 'applyAutoScanSettings':
      return applyAutoScanSettings(state, action.settings);

    case 'setAutoScanSource':
      return { ...state, autoScanSetup: { ...state.autoScanSetup, source: action.source } };

    case 'setAutoScanCli':
      return { ...state, autoScanSetup: { ...state.autoScanSetup, cli: action.cli } };

    case 'setAutoScanUi':
      return { ...state, autoScanSetup: { ...state.autoScanSetup, ui: action.ui } };

    case 'setAutoScanInterval': {
      const minutes = validInterval(action.minutes);
      if (minutes === null) return state;
      return { ...state, autoScanSetup: { ...state.autoScanSetup, intervalMinutes: minutes } };
    }

    case 'setAutoScanLinearFilter':
      return { ...state, autoScanSetup: { ...state.autoScanSetup, linearFilter: action.filter } };

    case 'setAutoScanGitHubFilter':
      return { ...state, autoScanSetup: { ...state.autoScanSetup, githubFilter: action.filter } };
  }
}
