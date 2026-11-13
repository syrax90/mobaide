import {
  chatSupport,
  cliModelDetails,
  cliModels,
  gitCapability,
  type AgentVersionInfo,
} from '../../../protocol/dto/agentVersion';
import type { WorktreeBases } from '../../../api/gitRepository';
import { customSessionTypeId } from '../../../protocol/wireSchema';
import type { ProjectSummary } from '../../../api/projectRepository';
import type { CustomAgentType } from '../../../api/customAgentTypeRepository';
import { AntigravityOptionsPanel } from './panels/AntigravityOptionsPanel';
import { AutoScanOptionsPanel } from './panels/AutoScanOptionsPanel';
import { ClaudeAdvancedPanel } from './panels/ClaudeAdvancedPanel';
import { ClaudeOptionsPanel } from './panels/ClaudeOptionsPanel';
import { CodexOptionsPanel } from './panels/CodexOptionsPanel';
import { OpenCodeOptionsPanel } from './panels/OpenCodeOptionsPanel';
import { CopilotOptionsPanel } from './panels/CopilotOptionsPanel';
import { CursorOptionsPanel } from './panels/CursorOptionsPanel';
import type { BaseBranchStatus } from './panels/BaseBranchPicker';
import { WorktreeSection } from './panels/WorktreeSection';
import { chatAgentKind, terminalUiOf, usesChatView, type ChatAgentKind } from './buildCreateRequest';
import { effectiveWorktreeBase } from './worktreeBase';
import {
  addDirsError,
  isCustomMode,
  type ProjectSelection,
  type WizardAction,
  type WizardMode,
  type WizardState,
} from './wizardState';

/** The base-branch list query, as the wizard sees it. */
export interface WorktreeBasesView {
  status: BaseBranchStatus;
  bases: WorktreeBases | undefined;
  retry: () => void;
}

interface ConfigStepProps {
  mode: WizardMode;
  state: WizardState;
  /** From the URL, not the reducer — the reducer's copy does not survive a remount. */
  selection: ProjectSelection;
  project: ProjectSummary | null;
  capabilities: AgentVersionInfo | undefined;
  customTypes: readonly CustomAgentType[];
  /** Absent until the wizard has a list query to show (tests, older callers). */
  worktreeBases?: WorktreeBasesView;
  submitting: boolean;
  error: string | null;
  dispatch: (action: WizardAction) => void;
  onSubmit: () => void;
  onBack: () => void;
  /** Open the "Continue a session" picker; shown only where the PC can resume. */
  onContinueSession?: () => void;
  /** Start OpenCode's provider sign-in in a terminal session (OpenCode only). */
  onConnectProvider?: () => void;
}

export function modeTitle(
  mode: WizardMode,
  customTypes: readonly CustomAgentType[],
): string {
  switch (mode) {
    case 'claude':
      return 'Claude code session';
    case 'codex':
      return 'Codex session';
    case 'antigravity':
      return 'Antigravity session';
    case 'opencode':
      return 'OpenCode session';
    case 'copilot':
      return 'GitHub Copilot session';
    case 'cursor':
      return 'Cursor session';
    case 'auto_scan':
      return 'Auto Scan mode';
    case 'shell':
      return 'Terminal session';
    default: {
      const id = customSessionTypeId(mode);
      return customTypes.find((type) => type.id === id)?.name ?? 'Terminal session';
    }
  }
}

function continueHint(kind: ChatAgentKind | null): string {
  switch (kind) {
    case 'codex':
      return 'Open a Codex session started on your PC, for example in a terminal.';
    case 'antigravity':
      return 'Open an Antigravity conversation started on your PC, for example with agy in a terminal.';
    case 'opencode':
      return 'Open an OpenCode session started on your PC, for example in a terminal.';
    case 'copilot':
      return 'Open a GitHub Copilot session started on your PC, for example in a terminal.';
    case 'cursor':
      return 'Open a Cursor chat session stored on your PC. Sessions from Cursor’s terminal UI are not listed.';
    default:
      return 'Open a Claude Code session started on your PC, for example in a terminal.';
  }
}

export function ConfigStep({
  mode,
  state,
  selection,
  project,
  capabilities,
  customTypes,
  worktreeBases,
  submitting,
  error,
  dispatch,
  onSubmit,
  onBack,
  onContinueSession,
  onConnectProvider,
}: ConfigStepProps) {
  const chatKind = chatAgentKind(mode);
  const chat = chatSupport(capabilities, chatKind ?? 'claude');
  const usesChat = chatKind !== null && usesChatView(terminalUiOf(state, chatKind), chat);
  const canResume = usesChat && chat.kind === 'available' && chat.resume;
  // A worktree needs a stored git project; a one-off path has no repo to branch.
  const showsGitOptions =
    selection.kind === 'stored' && project?.is_git === true && mode !== 'auto_scan';

  // Behind the capability: an older agent ignores `base_branch` and has no list route.
  const baseBranch =
    showsGitOptions && project !== null && gitCapability(capabilities, 'worktree_base')
      ? {
          status: worktreeBases?.status ?? 'loading',
          bases: worktreeBases?.bases,
          value: effectiveWorktreeBase(
            state.worktreeBasePick,
            project.name,
            worktreeBases?.status === 'ready' ? worktreeBases.bases : undefined,
          ),
          onChange: (ref: string | null) => {
            dispatch({ type: 'setWorktreeBase', pick: { projectName: project.name, ref } });
          },
          onRetry: () => {
            worktreeBases?.retry();
          },
        }
      : null;

  // The server 422s a flag-shaped directory, so refuse the launch rather than
  // showing the inline error and sending it anyway.
  const blocked =
    mode === 'antigravity' && addDirsError(state.antigravity.addDirsDraft) !== null;

  return (
    <div className="wizard-body">
      <h2>{modeTitle(mode, customTypes)}</h2>

      {canResume && onContinueSession !== undefined && (
        <div className="option-group resume-entry">
          <div className="resume-entry-text">
            <h3>Continue a session</h3>
            <p className="field-hint">{continueHint(chatKind)}</p>
          </div>
          <button
            type="button"
            className="button"
            disabled={submitting}
            onClick={onContinueSession}
          >
            Continue a session…
          </button>
        </div>
      )}

      {mode === 'claude' && (
        <ClaudeOptionsPanel
          bucket={state.claude}
          bucketId="claude"
          models={cliModels(capabilities, 'claude')}
          dispatch={dispatch}
          showTerminalOptions={!usesChat}
        />
      )}
      {mode === 'codex' && (
        <CodexOptionsPanel
          bucket={state.codex}
          models={cliModels(capabilities, 'codex')}
          modelDetails={cliModelDetails(capabilities, 'codex')}
          dispatch={dispatch}
          chat={chat}
        />
      )}
      {mode === 'antigravity' && (
        <AntigravityOptionsPanel
          bucket={state.antigravity}
          models={cliModels(capabilities, 'antigravity')}
          chat={chat}
          dispatch={dispatch}
        />
      )}
      {mode === 'opencode' && onConnectProvider !== undefined && (
        <div className="option-group resume-entry">
          <div className="resume-entry-text">
            <h3>Connect a provider</h3>
            <p className="field-hint">
              Sign in to a model provider, for example Anthropic, OpenAI or Google. OpenCode’s
              sign-in opens in a terminal and stays on your PC.
            </p>
          </div>
          <button
            type="button"
            className="button"
            disabled={submitting}
            onClick={onConnectProvider}
          >
            Connect a provider…
          </button>
        </div>
      )}
      {mode === 'opencode' && (
        <OpenCodeOptionsPanel bucket={state.opencode} chat={chat} dispatch={dispatch} />
      )}
      {mode === 'copilot' && (
        <CopilotOptionsPanel bucket={state.copilot} chat={chat} dispatch={dispatch} />
      )}
      {mode === 'cursor' && (
        <CursorOptionsPanel bucket={state.cursor} chat={chat} dispatch={dispatch} />
      )}
      {mode === 'auto_scan' && (
        <AutoScanOptionsPanel
          bucket={state.autoScan}
          models={cliModels(capabilities, 'claude')}
          dispatch={dispatch}
        />
      )}
      {(mode === 'shell' || isCustomMode(mode)) && (
        <p className="page-note">
          {isCustomMode(mode)
            ? 'Runs this agent type’s command in the chosen directory.'
            : 'Runs your login shell in the chosen directory.'}
        </p>
      )}

      {showsGitOptions && (
        <WorktreeSection git={state.git} dispatch={dispatch} baseBranch={baseBranch} />
      )}

      {mode === 'claude' && (
        <ClaudeAdvancedPanel
          bucket={state.claude}
          bucketId="claude"
          dispatch={dispatch}
          chat={chat}
        />
      )}

      {error !== null && <p className="field-error">{error}</p>}

      <div className="wizard-actions">
        <button
          type="button"
          className="button button-primary"
          disabled={submitting || blocked}
          onClick={onSubmit}
        >
          {submitting
            ? mode === 'auto_scan'
              ? 'Starting…'
              : 'Creating…'
            : mode === 'auto_scan'
              ? 'Start Auto Scan'
              : mode === 'shell'
                ? 'Start Terminal'
                : 'Create session'}
        </button>
        <button type="button" className="button" disabled={submitting} onClick={onBack}>
          Back
        </button>
      </div>
    </div>
  );
}
