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
import type { ProjectSummary } from '../../../api/projectRepository';
import type { ChatSupport } from '../../../protocol/dto/agentVersion';
import type { ChatStoredSession } from '../../../protocol/dto/chatSessions';
import {
  effectiveClaudeEffort,
  effectiveClaudeModel,
  isCustomMode,
  parseAddDirs,
  type AntigravityBucket,
  type ClaudeBucket,
  type CodexBucket,
  type OpenCodeBucket,
  type WizardMode,
  type WizardState,
} from './wizardState';

export type CreateSessionRequest = components['schemas']['CreateSessionRequest'];
export type StartScanRequest = components['schemas']['StartScanRequest'];
type ClaudeOptions = components['schemas']['ClaudeOptionsModel'];
type CodexOptions = components['schemas']['CodexOptionsModel'];
type AntigravityOptions = components['schemas']['AntigravityOptionsModel'];
type OpenCodeOptions = components['schemas']['OpenCodeOptionsModel'];

export interface BuildContext {
  state: WizardState;
  mode: WizardMode;
  /** The stored project the selection names, once resolved. The only source of `is_git`. */
  project: ProjectSummary | null;
  /** `capabilities.chat` for the mode's agent; absent reads as not known yet. */
  chat?: ChatSupport;
  /**
   * The resolved base branch (`effectiveWorktreeBase`), or `null`/absent for
   * the agent's default. Sent only when a worktree is really requested.
   */
  worktreeBase?: string | null;
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

export function claudeUsesChat(bucket: ClaudeBucket, chat: ChatSupport | undefined): boolean {
  return usesChatView(bucket.terminalUi, chat);
}

/** The agent kinds whose sessions can open in the chat view. */
export type ChatAgentKind = 'claude' | 'codex' | 'antigravity' | 'opencode';

export function chatAgentKind(mode: WizardMode | null): ChatAgentKind | null {
  return mode === 'claude' || mode === 'codex' || mode === 'antigravity' || mode === 'opencode'
    ? mode
    : null;
}

/** The kind's "Terminal UI" toggle. */
export function terminalUiOf(state: WizardState, kind: ChatAgentKind): boolean {
  switch (kind) {
    case 'claude':
      return state.claude.terminalUi;
    case 'codex':
      return state.codex.terminalUi;
    case 'antigravity':
      return state.antigravity.terminalUi;
    case 'opencode':
      return state.opencode.terminalUi;
  }
}

/** Blank-after-trim omits the key; otherwise the value goes untrimmed (a prompt's trailing newline is intentional). */
function blankToUndefined(value: string): string | undefined {
  return value.trim() === '' ? undefined : value;
}

interface Placement {
  projectName: string | undefined;
  cwd: string | undefined;
  /** Worktrees need a stored git project; a one-off path has no repo to branch. */
  gitEligible: boolean;
  worktree: boolean;
  branchName: string | undefined;
  baseBranch: string | null;
}

function placement({ state, project, worktreeBase }: BuildContext): Placement {
  const selection = state.selection;
  const gitEligible = selection.kind === 'stored' && project?.is_git === true;
  const worktree = gitEligible && state.git.worktree;
  return {
    projectName: selection.kind === 'stored' ? selection.name : undefined,
    cwd: selection.kind === 'customPath' ? selection.path : undefined,
    gitEligible,
    worktree,
    // Gated on git-eligibility alone, not on `worktree` — both mobile clients
    // send a branch name whenever the project could host one.
    branchName:
      gitEligible && state.git.branchName.trim() !== '' ? state.git.branchName : undefined,
    // Unlike the branch name, only with a worktree: the base means nothing else.
    baseBranch: worktree ? (worktreeBase ?? null) : null,
  };
}

function claudeOptions(
  bucket: ClaudeBucket,
  extras: { worktree: boolean; branchName: string | undefined; baseBranch: string | null },
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
    project_name: place.projectName,
    cwd: place.cwd,
    rows: 24,
    cols: 80,
  };

  if (mode === 'claude') {
    return {
      ...base,
      cli: 'claude',
      claude_options: claudeOptions(state.claude, {
        worktree: place.worktree,
        branchName: place.branchName,
        baseBranch: place.baseBranch,
      }),
      // Managed CLIs carry worktree intent inside their own options block; the
      // top-level flag is only read on the shell path.
      worktree: false,
      ui: claudeUsesChat(state.claude, context.chat) ? 'chat' : 'terminal',
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
 * it was recorded in. That folder is the selected project when the paths
 * match, else a plain `cwd`.
 */
export function buildResumeRequest(
  { state, project }: BuildContext,
  kind: ChatAgentKind,
  stored: ChatStoredSession,
): CreateSessionRequest {
  const sameFolder = project !== null && project.path === stored.cwd;
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
          : { cli: 'claude', claude_options: claudeOptions(state.claude, noWorktree) };
  return {
    project_name: sameFolder ? project.name : undefined,
    cwd: sameFolder ? undefined : stored.cwd,
    rows: 24,
    cols: 80,
    ...options,
    worktree: false,
    ui: 'chat',
    chat_resume_id: stored.session_id,
  };
}

/** `null` unless the mode is `auto_scan` against a stored project. */
export function buildStartScanRequest(context: BuildContext): StartScanRequest | null {
  const { state, mode } = context;
  if (mode !== 'auto_scan' || state.selection.kind !== 'stored') return null;
  return {
    project_name: state.selection.name,
    claude_options: {
      ...claudeOptions(state.autoScan, {
        // Hardcoded: the scanner branches a worktree per Linear issue so
        // concurrent issues stay isolated. No branch name — it names them.
        worktree: true,
        branchName: undefined,
        // The scanner picks its own start point; the wizard's picker never shows.
        baseBranch: null,
      }),
      initial_prompt: blankToUndefined(state.autoScan.initialPrompt),
    },
  };
}
