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
import {
  effectiveClaudeEffort,
  effectiveClaudeModel,
  isCustomMode,
  parseAddDirs,
  type ClaudeBucket,
  type WizardMode,
  type WizardState,
} from './wizardState';

export type CreateSessionRequest = components['schemas']['CreateSessionRequest'];
export type StartScanRequest = components['schemas']['StartScanRequest'];
type ClaudeOptions = components['schemas']['ClaudeOptionsModel'];

export interface BuildContext {
  state: WizardState;
  mode: WizardMode;
  /** The stored project the selection names, once resolved. The only source of `is_git`. */
  project: ProjectSummary | null;
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
}

function placement({ state, project }: BuildContext): Placement {
  const selection = state.selection;
  const gitEligible = selection.kind === 'stored' && project?.is_git === true;
  return {
    projectName: selection.kind === 'stored' ? selection.name : undefined,
    cwd: selection.kind === 'customPath' ? selection.path : undefined,
    gitEligible,
    worktree: gitEligible && state.git.worktree,
    // Gated on git-eligibility alone, not on `worktree` — both mobile clients
    // send a branch name whenever the project could host one.
    branchName:
      gitEligible && state.git.branchName.trim() !== '' ? state.git.branchName : undefined,
  };
}

function claudeOptions(
  bucket: ClaudeBucket,
  extras: { worktree: boolean; branchName: string | undefined },
): ClaudeOptions {
  return {
    plan_mode: bucket.planMode,
    no_memory: bucket.noMemory,
    skip_permissions: bucket.skipPermissions,
    worktree: extras.worktree,
    branch_name: extras.branchName,
    model: effectiveClaudeModel(bucket) ?? undefined,
    effort: effectiveClaudeEffort(bucket),
    fast_mode: bucket.fastMode,
    ultracode: bucket.ultracode,
    debug: bucket.debug,
    focus_mode: bucket.focusMode,
    append_system_prompt: blankToUndefined(bucket.appendSystemPrompt),
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
      }),
      // Managed CLIs carry worktree intent inside their own options block; the
      // top-level flag is only read on the shell path.
      worktree: false,
    };
  }

  if (mode === 'codex') {
    return {
      ...base,
      cli: 'codex',
      codex_options: {
        model: state.codex.model ?? undefined,
        sandbox: state.codex.sandbox,
        approval: state.codex.approval,
        reasoning_effort: state.codex.reasoningEffort,
        full_auto: state.codex.fullAuto,
        worktree: place.worktree,
        branch_name: place.branchName,
      },
      worktree: false,
    };
  }

  if (mode === 'antigravity') {
    return {
      ...base,
      cli: 'antigravity',
      antigravity_options: {
        model: state.antigravity.model ?? undefined,
        skip_permissions: state.antigravity.skipPermissions,
        sandbox: state.antigravity.sandbox,
        add_dirs: parseAddDirs(state.antigravity.addDirsDraft),
        worktree: place.worktree,
        branch_name: place.branchName,
      },
      worktree: false,
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
      }),
      initial_prompt: blankToUndefined(state.autoScan.initialPrompt),
    },
  };
}
