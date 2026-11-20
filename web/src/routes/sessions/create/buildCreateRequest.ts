/**
 * Turn wizard state into a launch request.
 *
 * Two guarantees live here, and both are structural rather than conventional:
 *
 * **No cross-mode leakage.** Each arm names only its own bucket, so a Codex
 * request cannot mention a Claude field even if the user filled that branch in
 * first. There is no configuration to get wrong.
 *
 * **Every boolean explicit.** The return type is the *generated*
 * `CreateSessionRequest`, whose option models declare `plan_mode`, `no_memory`,
 * `skip_permissions`, `fast_mode`, `ultracode`, `debug` and `worktree` as
 * required — so omitting one is a compile error. That matters most for `skip_permissions`,
 * whose wire-absence default is `true`: a dropped field would silently launch
 * with `--dangerously-skip-permissions`.
 */
import type { components } from '../../../api/generated/agent';
import type { WorkspaceSummary } from '../../../api/workspaceRepository';
import type { AutoScanCapability, ChatSupport } from '../../../protocol/dto/agentVersion';
import type { AutoScanFilter } from '../../../protocol/dto/workspaces';
import { isAutoScanAgentId } from '../../../autoscan/autoscanDisplay';
import type { ChatStoredSession } from '../../../protocol/dto/chatSessions';
import {
  effectiveClaudeEffort,
  effectiveClaudeModel,
  isCustomMode,
  parseAddDirs,
  type AntigravityBucket,
  type AutoScanSetup,
  type AutoScanUi,
  type ClaudeBucket,
  type CodexBucket,
  type OpenCodeBucket,
  type CopilotBucket,
  type CursorBucket,
  type GitHubFilterDraft,
  type JunieBucket,
  type PiBucket,
  type WizardMode,
  type WizardState,
} from './wizardState';

export type CreateSessionRequest = components['schemas']['CreateSessionRequest'];
export type StartScanRequest = components['schemas']['StartScanRequest'];
type ClaudeOptions = components['schemas']['ClaudeOptionsModel'];
type CodexOptions = components['schemas']['CodexOptionsModel'];
type AntigravityOptions = components['schemas']['AntigravityOptionsModel'];
type OpenCodeOptions = components['schemas']['OpenCodeOptionsModel'];
type CopilotOptions = components['schemas']['CopilotOptionsModel'];
type CursorOptions = components['schemas']['CursorOptionsModel'];
type JunieOptions = components['schemas']['JunieOptionsModel'];
type PiOptions = components['schemas']['PiOptionsModel'];
type LinearFilterWire = components['schemas']['AutoScanFilterModel'];
type GitHubFilterWire = components['schemas']['GitHubScanFilterModel'];
export type AutoScanTemplateSettings = components['schemas']['AutoScanTemplateSettings'];
type ClaudeScanOptions = components['schemas']['ClaudeScanOptionsModel'];
type CodexScanOptions = components['schemas']['CodexScanOptionsModel'];
type AntigravityScanOptions = components['schemas']['AntigravityScanOptionsModel'];

export interface BuildContext {
  state: WizardState;
  mode: WizardMode;
  /** The stored workspace the selection names, once resolved. The only source of `is_git`. */
  workspace: WorkspaceSummary | null;
  /** `capabilities.chat` for the mode's agent; absent reads as not known yet. */
  chat?: ChatSupport;
  /**
   * The resolved base branch (`effectiveWorktreeBase`), or `null`/absent for
   * the agent's default. Sent only when a worktree is really requested.
   */
  worktreeBase?: string | null;
  /**
   * `capabilities.autoscan`. Absent or `null` is an agent from before the
   * Auto Scan sources delta, which gets the legacy Linear + Claude request.
   */
  autoScan?: AutoScanCapability | null;
  /** `capabilities.chat` for the agent the Auto Scan wizard has chosen. */
  autoScanChat?: ChatSupport;
}

/**
 * The view an Auto Scan opens each issue's session in: the chat only when it
 * was chosen and the PC is known to have a chat view for the agent.
 */
export function autoScanUi(setup: AutoScanSetup, chat: ChatSupport | undefined): AutoScanUi {
  return setup.ui === 'chat' && chat?.kind === 'available' ? 'chat' : 'terminal';
}

/**
 * Whether a launch starts in the chat view: the Terminal UI toggle is off and
 * the PC is not known to lack chat support for the agent. While the probe is
 * still out the server decides; a known "unavailable" never sends `chat`.
 */
export function usesChatView(terminalUi: boolean, chat: ChatSupport | undefined): boolean {
  if (terminalUi) return false;
  const kind = chat?.kind ?? 'unknown';
  return kind === 'available' || kind === 'unknown';
}

/** Claude Code's Terminal UI, also implied by Code Mode, which runs only there. */
export function claudeTerminalUi(bucket: ClaudeBucket): boolean {
  return bucket.terminalUi || bucket.codeMode;
}

export function claudeUsesChat(bucket: ClaudeBucket, chat: ChatSupport | undefined): boolean {
  return usesChatView(claudeTerminalUi(bucket), chat);
}

/** The agent kinds whose sessions can open in the chat view. */
export type ChatAgentKind =
  | 'claude'
  | 'codex'
  | 'antigravity'
  | 'opencode'
  | 'copilot'
  | 'cursor'
  | 'junie'
  | 'pi';

export function chatAgentKind(mode: WizardMode | null): ChatAgentKind | null {
  return mode === 'claude' ||
    mode === 'codex' ||
    mode === 'antigravity' ||
    mode === 'opencode' ||
    mode === 'copilot' ||
    mode === 'cursor' ||
    mode === 'junie' ||
    mode === 'pi'
    ? mode
    : null;
}

/** Whether the kind's "Open sessions in" choice is Terminal. */
export function terminalUiOf(state: WizardState, kind: ChatAgentKind): boolean {
  switch (kind) {
    case 'claude':
      return claudeTerminalUi(state.claude);
    case 'codex':
      return state.codex.terminalUi;
    case 'antigravity':
      return state.antigravity.terminalUi;
    case 'opencode':
      return state.opencode.terminalUi;
    case 'copilot':
      return state.copilot.terminalUi;
    case 'cursor':
      return state.cursor.terminalUi;
    case 'junie':
      return state.junie.terminalUi;
    case 'pi':
      return state.pi.terminalUi;
  }
}

/** Blank-after-trim omits the key; otherwise the value goes untrimmed (a prompt's trailing newline is intentional). */
function blankToUndefined(value: string): string | undefined {
  return value.trim() === '' ? undefined : value;
}

interface Placement {
  workspaceName: string | undefined;
  cwd: string | undefined;
  /** Worktrees need a stored git workspace; a one-off path has no repo to branch. */
  gitEligible: boolean;
  worktree: boolean;
  branchName: string | undefined;
  baseBranch: string | null;
}

function placement({ state, workspace, worktreeBase }: BuildContext): Placement {
  const selection = state.selection;
  const gitEligible = selection.kind === 'stored' && workspace?.is_git === true;
  const worktree = gitEligible && state.git.worktree;
  return {
    workspaceName: selection.kind === 'stored' ? selection.name : undefined,
    cwd: selection.kind === 'customPath' ? selection.path : undefined,
    gitEligible,
    worktree,
    // Gated on git-eligibility alone, not on `worktree` — both mobile clients
    // send a branch name whenever the workspace could host one.
    branchName:
      gitEligible && state.git.branchName.trim() !== '' ? state.git.branchName : undefined,
    // Unlike the branch name, only with a worktree: the base means nothing else.
    baseBranch: worktree ? (worktreeBase ?? null) : null,
  };
}

/**
 * `codeMode` is passed by the caller: Claude Code has it in the terminal UI
 * only, and Auto Scan and Continue never send it.
 */
function claudeOptions(
  bucket: ClaudeBucket,
  extras: { worktree: boolean; branchName: string | undefined; baseBranch: string | null },
  codeMode = false,
): ClaudeOptions {
  return {
    plan_mode: bucket.planMode,
    no_memory: bucket.noMemory,
    skip_permissions: bucket.skipPermissions,
    worktree: extras.worktree,
    branch_name: extras.branchName,
    base_branch: extras.baseBranch,
    model: effectiveClaudeModel(bucket) ?? undefined,
    effort: effectiveClaudeEffort(bucket),
    fast_mode: bucket.fastMode,
    ultracode: bucket.ultracode,
    debug: bucket.debug,
    focus_mode: bucket.focusMode,
    code_mode: codeMode,
    append_system_prompt: blankToUndefined(bucket.appendSystemPrompt),
  };
}

function codexOptions(
  bucket: CodexBucket,
  extras: { worktree: boolean; branchName: string | undefined; baseBranch: string | null },
): CodexOptions {
  return {
    model: bucket.model ?? undefined,
    sandbox: bucket.sandbox,
    approval: bucket.approval,
    reasoning_effort: bucket.reasoningEffort,
    full_auto: bucket.fullAuto,
    worktree: extras.worktree,
    branch_name: extras.branchName,
    base_branch: extras.baseBranch,
  };
}

function antigravityOptions(
  bucket: AntigravityBucket,
  extras: { worktree: boolean; branchName: string | undefined; baseBranch: string | null },
): AntigravityOptions {
  return {
    model: bucket.model ?? undefined,
    skip_permissions: bucket.skipPermissions,
    sandbox: bucket.sandbox,
    add_dirs: parseAddDirs(bucket.addDirsDraft),
    worktree: extras.worktree,
    branch_name: extras.branchName,
    base_branch: extras.baseBranch,
  };
}

function opencodeOptions(
  bucket: OpenCodeBucket,
  extras: { worktree: boolean; branchName: string | undefined; baseBranch: string | null },
): OpenCodeOptions {
  return {
    auto_approve: bucket.autoApprove,
    connect: false,
    code_mode: bucket.codeMode,
    worktree: extras.worktree,
    branch_name: extras.branchName,
    base_branch: extras.baseBranch,
  };
}

function copilotOptions(
  bucket: CopilotBucket,
  extras: { worktree: boolean; branchName: string | undefined; baseBranch: string | null },
): CopilotOptions {
  return {
    allow_all: bucket.allowAll,
    worktree: extras.worktree,
    branch_name: extras.branchName,
    base_branch: extras.baseBranch,
  };
}

function cursorOptions(
  bucket: CursorBucket,
  extras: { worktree: boolean; branchName: string | undefined; baseBranch: string | null },
): CursorOptions {
  return {
    force: bucket.force,
    worktree: extras.worktree,
    branch_name: extras.branchName,
    base_branch: extras.baseBranch,
  };
}

function junieOptions(
  bucket: JunieBucket,
  extras: { worktree: boolean; branchName: string | undefined; baseBranch: string | null },
): JunieOptions {
  return {
    plan_mode: bucket.planMode,
    brave: bucket.brave,
    worktree: extras.worktree,
    branch_name: extras.branchName,
    base_branch: extras.baseBranch,
  };
}

function piOptions(
  bucket: PiBucket,
  extras: { worktree: boolean; branchName: string | undefined; baseBranch: string | null },
): PiOptions {
  return {
    code_mode: bucket.codeMode,
    worktree: extras.worktree,
    branch_name: extras.branchName,
    base_branch: extras.baseBranch,
  };
}

/** `null` for `auto_scan`, which starts a scan rather than creating a session. */
export function buildCreateRequest(context: BuildContext): CreateSessionRequest | null {
  const { state, mode } = context;
  if (state.selection.kind === 'none') return null;
  if (mode === 'auto_scan') return null;

  const place = placement(context);
  const base = {
    project_name: place.workspaceName,
    cwd: place.cwd,
    rows: 24,
    cols: 80,
  };

  if (mode === 'claude') {
    const chat = claudeUsesChat(state.claude, context.chat);
    return {
      ...base,
      cli: 'claude',
      claude_options: claudeOptions(
        state.claude,
        {
          worktree: place.worktree,
          branchName: place.branchName,
          baseBranch: place.baseBranch,
        },
        state.claude.codeMode && !chat,
      ),
      // Managed CLIs carry worktree intent inside their own options block; the
      // top-level flag is only read on the shell path.
      worktree: false,
      ui: chat ? 'chat' : 'terminal',
    };
  }

  if (mode === 'codex') {
    return {
      ...base,
      cli: 'codex',
      codex_options: codexOptions(state.codex, {
        worktree: place.worktree,
        branchName: place.branchName,
        baseBranch: place.baseBranch,
      }),
      worktree: false,
      ui: usesChatView(state.codex.terminalUi, context.chat) ? 'chat' : 'terminal',
    };
  }

  if (mode === 'antigravity') {
    return {
      ...base,
      cli: 'antigravity',
      antigravity_options: antigravityOptions(state.antigravity, {
        worktree: place.worktree,
        branchName: place.branchName,
        baseBranch: place.baseBranch,
      }),
      worktree: false,
      ui: usesChatView(state.antigravity.terminalUi, context.chat) ? 'chat' : 'terminal',
    };
  }

  if (mode === 'opencode') {
    return {
      ...base,
      cli: 'opencode',
      opencode_options: opencodeOptions(state.opencode, {
        worktree: place.worktree,
        branchName: place.branchName,
        baseBranch: place.baseBranch,
      }),
      worktree: false,
      ui: usesChatView(state.opencode.terminalUi, context.chat) ? 'chat' : 'terminal',
    };
  }

  if (mode === 'copilot') {
    return {
      ...base,
      cli: 'copilot',
      copilot_options: copilotOptions(state.copilot, {
        worktree: place.worktree,
        branchName: place.branchName,
        baseBranch: place.baseBranch,
      }),
      worktree: false,
      ui: usesChatView(state.copilot.terminalUi, context.chat) ? 'chat' : 'terminal',
    };
  }

  if (mode === 'cursor') {
    return {
      ...base,
      cli: 'cursor',
      cursor_options: cursorOptions(state.cursor, {
        worktree: place.worktree,
        branchName: place.branchName,
        baseBranch: place.baseBranch,
      }),
      worktree: false,
      ui: usesChatView(state.cursor.terminalUi, context.chat) ? 'chat' : 'terminal',
    };
  }

  if (mode === 'junie') {
    return {
      ...base,
      cli: 'junie',
      junie_options: junieOptions(state.junie, {
        worktree: place.worktree,
        branchName: place.branchName,
        baseBranch: place.baseBranch,
      }),
      worktree: false,
      ui: usesChatView(state.junie.terminalUi, context.chat) ? 'chat' : 'terminal',
    };
  }

  if (mode === 'pi') {
    return {
      ...base,
      cli: 'pi',
      pi_options: piOptions(state.pi, {
        worktree: place.worktree,
        branchName: place.branchName,
        baseBranch: place.baseBranch,
      }),
      worktree: false,
      ui: usesChatView(state.pi.terminalUi, context.chat) ? 'chat' : 'terminal',
    };
  }

  // Terminal and custom agent types share plain-shell semantics: no options
  // block, worktree intent at the top level, and `command` omitted so the agent
  // picks the login shell (it discards client hints on this path regardless).
  // A custom launch adds only the `cli` discriminator — the relay resolves the
  // stored command server-side, so it never leaves the account's registry.
  return {
    ...base,
    cli: isCustomMode(mode) ? mode : undefined,
    worktree: place.worktree,
    branch_name: place.branchName,
    base_branch: place.baseBranch,
  };
}

/**
 * Continue a session stored on the PC in a new chat session, with the agent's
 * current options but never a worktree: the conversation belongs to the folder
 * it was recorded in. That folder is the selected workspace when the paths
 * match, else a plain `cwd`.
 */
export function buildResumeRequest(
  { state, workspace }: BuildContext,
  kind: ChatAgentKind,
  stored: ChatStoredSession,
): CreateSessionRequest {
  const sameFolder = workspace !== null && workspace.path === stored.cwd;
  const noWorktree = { worktree: false, branchName: undefined, baseBranch: null };
  const options =
    kind === 'codex'
      ? { cli: 'codex', codex_options: codexOptions(state.codex, noWorktree) }
      : kind === 'antigravity'
        ? {
            cli: 'antigravity',
            antigravity_options: antigravityOptions(state.antigravity, noWorktree),
          }
        : kind === 'opencode'
          ? { cli: 'opencode', opencode_options: opencodeOptions(state.opencode, noWorktree) }
          : kind === 'copilot'
            ? { cli: 'copilot', copilot_options: copilotOptions(state.copilot, noWorktree) }
            : kind === 'cursor'
              ? { cli: 'cursor', cursor_options: cursorOptions(state.cursor, noWorktree) }
              : kind === 'junie'
                ? { cli: 'junie', junie_options: junieOptions(state.junie, noWorktree) }
                : kind === 'pi'
                  ? { cli: 'pi', pi_options: piOptions(state.pi, noWorktree) }
                  : { cli: 'claude', claude_options: claudeOptions(state.claude, noWorktree) };
  return {
    project_name: sameFolder ? workspace.name : undefined,
    cwd: sameFolder ? undefined : stored.cwd,
    rows: 24,
    cols: 80,
    ...options,
    worktree: false,
    ui: 'chat',
    chat_resume_id: stored.session_id,
  };
}

/**
 * A terminal session that runs OpenCode's own sign-in (`opencode auth login`)
 * in the chosen folder, so a model provider can be connected from here. It is
 * not an OpenCode session: no wizard option applies and nothing is remembered.
 */
export function buildConnectRequest(context: BuildContext): CreateSessionRequest | null {
  if (context.state.selection.kind === 'none') return null;
  const place = placement(context);
  return {
    project_name: place.workspaceName,
    cwd: place.cwd,
    rows: 24,
    cols: 80,
    cli: 'opencode',
    opencode_options: { auto_approve: false, connect: true, code_mode: true, worktree: false },
    worktree: false,
    ui: 'terminal',
  };
}

/** Claude Code's options for a scan start: the full launch model, worktree on. */
function scanClaudeOptions(bucket: ClaudeBucket): ClaudeOptions {
  return claudeOptions(bucket, {
    // Hardcoded: the scanner branches a worktree per issue so concurrent
    // issues stay isolated. No branch name — it names them.
    worktree: true,
    branchName: undefined,
    // The scanner picks its own start point; the wizard's picker never shows.
    baseBranch: null,
  });
}

/** Listed field by field, so no extra key a looser draft carries reaches the wire. */
function linearFilterWire(filter: AutoScanFilter): LinearFilterWire {
  return {
    state_names: [...filter.state_names],
    label_names: [...filter.label_names],
    active_state: filter.active_state,
    done_state: filter.done_state,
    removal_label: filter.removal_label,
  };
}

/** Trimmed, with a blank assignee or label to add sent as `null` (not set). */
function githubFilterWire(filter: GitHubFilterDraft): GitHubFilterWire {
  const assignee = filter.assignee.trim();
  const activeLabel = filter.activeLabel.trim();
  return {
    labels: filter.labels.map((label) => label.trim()),
    assignee: assignee === '' ? null : assignee,
    active_label: activeLabel === '' ? null : activeLabel,
    issue_types: filter.issueTypes.map((type) => type.trim()).filter((type) => type !== ''),
  };
}

/*
 * The options Auto Scan keeps for each agent (PROTOCOL.md, "Agent options"):
 * the session fields minus worktree, branch, prompt and sign-in. Every field
 * is listed, as in the session builders, so none falls back to a wire default.
 */
function claudeScanOptions(bucket: ClaudeBucket): ClaudeScanOptions {
  return {
    plan_mode: bucket.planMode,
    no_memory: bucket.noMemory,
    skip_permissions: bucket.skipPermissions,
    model: effectiveClaudeModel(bucket),
    effort: effectiveClaudeEffort(bucket),
    fast_mode: bucket.fastMode,
    ultracode: bucket.ultracode,
    append_system_prompt: blankToUndefined(bucket.appendSystemPrompt) ?? null,
    debug: bucket.debug,
    focus_mode: bucket.focusMode,
  };
}

function codexScanOptions(bucket: CodexBucket): CodexScanOptions {
  return {
    model: bucket.model,
    sandbox: bucket.sandbox,
    approval: bucket.approval,
    reasoning_effort: bucket.reasoningEffort,
    full_auto: bucket.fullAuto,
  };
}

function antigravityScanOptions(bucket: AntigravityBucket): AntigravityScanOptions {
  return {
    model: bucket.model,
    skip_permissions: bucket.skipPermissions,
    sandbox: bucket.sandbox,
    add_dirs: parseAddDirs(bucket.addDirsDraft),
  };
}

/**
 * The chosen non-Claude agent's options, under its wire key. Pi has none, and
 * Claude Code's go in `claude_options`, which the start and a template fill
 * differently.
 */
function scanAgentOptions(
  state: WizardState,
  cli: string,
): Pick<
  StartScanRequest,
  | 'codex_options'
  | 'antigravity_options'
  | 'opencode_options'
  | 'copilot_options'
  | 'cursor_options'
  | 'junie_options'
> {
  const agents = state.autoScanAgents;
  switch (cli) {
    case 'codex':
      return { codex_options: codexScanOptions(agents.codex) };
    case 'antigravity':
      return { antigravity_options: antigravityScanOptions(agents.antigravity) };
    case 'opencode':
      return { opencode_options: { auto_approve: agents.opencode.autoApprove } };
    case 'copilot':
      return { copilot_options: { allow_all: agents.copilot.allowAll } };
    case 'cursor':
      return { cursor_options: { force: agents.cursor.force } };
    case 'junie':
      return { junie_options: { plan_mode: agents.junie.planMode, brave: agents.junie.brave } };
    default:
      return {};
  }
}

/**
 * `null` unless the mode is `auto_scan` against a stored workspace — and, for
 * an agent with `capabilities.autoscan`, until a source and an agent are
 * chosen (the wizard keeps Start disabled until then).
 *
 * The newer request names every choice: `source`, `cli`, `ui`,
 * `interval_minutes`, only the chosen source's filter, only the chosen
 * agent's options, and `initial_prompt` at
 * the top level, where `""` clears the prompt the workspace saved. A start
 * without the agent's options would run it without asking, so they are
 * always sent.
 */
export function buildStartScanRequest(context: BuildContext): StartScanRequest | null {
  const { state, mode } = context;
  if (mode !== 'auto_scan' || state.selection.kind !== 'stored') return null;

  const capability = context.autoScan ?? null;
  if (capability === null) {
    return {
      project_name: state.selection.name,
      claude_options: {
        ...scanClaudeOptions(state.autoScan),
        initial_prompt: blankToUndefined(state.autoScan.initialPrompt),
      },
    };
  }

  const { source, cli, linearFilter, githubFilter } = state.autoScanSetup;
  if (source === null || cli === null || !isAutoScanAgentId(cli)) return null;
  return {
    project_name: state.selection.name,
    source,
    cli,
    ...(source === 'github'
      ? { github_filter: githubFilterWire(githubFilter) }
      : { linear_filter: linearFilterWire(linearFilter) }),
    initial_prompt: state.autoScan.initialPrompt,
    ui: autoScanUi(state.autoScanSetup, context.autoScanChat),
    interval_minutes: state.autoScanSetup.intervalMinutes,
    ...(cli === 'claude'
      ? { claude_options: scanClaudeOptions(state.autoScan) }
      : scanAgentOptions(state, cli)),
  };
}

/**
 * The wizard's current Auto Scan choices as a template's `settings`: the
 * source, the agent, the view, the time between scans, the prompt, both filters as they stand (a template may
 * hold a half-made filter), and only the chosen agent's options. Never the
 * Linear key. `applyAutoScanSettings` is the reverse.
 */
export function autoScanTemplateSettings(state: WizardState): AutoScanTemplateSettings {
  const { source, cli, linearFilter, githubFilter } = state.autoScanSetup;
  const agent = cli !== null && isAutoScanAgentId(cli) ? cli : null;
  return {
    source,
    cli: agent,
    ui: state.autoScanSetup.ui,
    interval_minutes: state.autoScanSetup.intervalMinutes,
    initial_prompt: state.autoScan.initialPrompt,
    linear_filter: linearFilterWire(linearFilter),
    github_filter: githubFilterWire(githubFilter),
    claude_options: agent === 'claude' ? claudeScanOptions(state.autoScan) : null,
    codex_options: null,
    antigravity_options: null,
    opencode_options: null,
    copilot_options: null,
    cursor_options: null,
    junie_options: null,
    ...(agent !== null && agent !== 'claude' ? scanAgentOptions(state, agent) : {}),
  };
}
