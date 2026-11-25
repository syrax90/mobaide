import { useId } from 'react';
import {
  autoScanCapability,
  chatSupport,
  cliFeature,
  cliModelDetails,
  cliModels,
  gitCapability,
  type AgentVersionInfo,
} from '../../../protocol/dto/agentVersion';
import type { WorktreeBases } from '../../../api/gitRepository';
import { customSessionTypeId } from '../../../protocol/wireSchema';
import type { WorkspaceSummary } from '../../../api/workspaceRepository';
import type { CustomAgentType } from '../../../api/customAgentTypeRepository';
import { AntigravityOptionsPanel } from './panels/AntigravityOptionsPanel';
import { AutoScanOptionsPanel } from './panels/AutoScanOptionsPanel';
import { AutoScanPanel } from './panels/AutoScanPanel';
import { ClaudeAdvancedPanel } from './panels/ClaudeAdvancedPanel';
import { ClaudeOptionsPanel } from './panels/ClaudeOptionsPanel';
import { CodexOptionsPanel } from './panels/CodexOptionsPanel';
import { OpenCodeOptionsPanel } from './panels/OpenCodeOptionsPanel';
import { CopilotOptionsPanel } from './panels/CopilotOptionsPanel';
import { CursorOptionsPanel } from './panels/CursorOptionsPanel';
import { JunieOptionsPanel } from './panels/JunieOptionsPanel';
import { PiOptionsPanel } from './panels/PiOptionsPanel';
import { SessionViewField } from './panels/SessionViewField';
import type { BaseBranchStatus } from './panels/BaseBranchPicker';
import { WorktreeSection } from './panels/WorktreeSection';
import { chatAgentKind, terminalUiOf, usesChatView, type ChatAgentKind } from './buildCreateRequest';
import { autoScanAgentOptions, autoScanBlocker, autoScanSources } from './autoScanSetup';
import type { AutoScanSourcesView } from './useAutoScanSources';
import type { AutoScanTemplatesView } from './useAutoScanTemplates';
import { effectiveWorktreeBase } from './worktreeBase';
import {
  addDirsError,
  isCustomMode,
  type WorkspaceSelection,
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
  selection: WorkspaceSelection;
  workspace: WorkspaceSummary | null;
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
  /**
   * The agent probe has not answered yet. Auto Scan waits for it, because the
   * answer decides between the source-and-agent panel and the legacy one.
   */
  capabilitiesPending?: boolean;
  /** What the PC says about the chosen Auto Scan source (newer agents only). */
  autoScanSources?: AutoScanSourcesView;
  /** The Auto Scan templates saved on the PC (newer agents only). */
  autoScanTemplates?: AutoScanTemplatesView;
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
    case 'junie':
      return 'Junie session';
    case 'pi':
      return 'Pi session';
    case 'auto_scan':
      return 'Auto Scan';
    case 'shell':
      return 'Terminal session';
    default: {
      const id = customSessionTypeId(mode);
      return customTypes.find((type) => type.id === id)?.name ?? 'Terminal session';
    }
  }
}

/** The wizard action that stores a "Terminal" or "Chat" pick for an agent. */
function terminalUiAction(kind: ChatAgentKind, value: boolean): WizardAction {
  switch (kind) {
    case 'claude':
      return { type: 'setClaudeFlag', bucket: 'claude', flag: 'terminalUi', value };
    case 'codex':
      return { type: 'setCodexTerminalUi', value };
    case 'antigravity':
      return { type: 'setAntigravityTerminalUi', value };
    case 'opencode':
      return { type: 'setOpenCodeTerminalUi', value };
    case 'copilot':
      return { type: 'setCopilotTerminalUi', value };
    case 'cursor':
      return { type: 'setCursorTerminalUi', value };
    case 'junie':
      return { type: 'setJunieTerminalUi', value };
    case 'pi':
      return { type: 'setPiTerminalUi', value };
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
    case 'junie':
      return 'Open a Junie session stored on your PC, also one you started in Junie’s terminal UI.';
    case 'pi':
      return 'Open a Pi session started on your PC, for example in a terminal.';
    default:
      return 'Open a Claude Code session started on your PC, for example in a terminal.';
  }
}

export function ConfigStep({
  mode,
  state,
  selection,
  workspace,
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
  capabilitiesPending = false,
  autoScanSources: sourcesView,
  autoScanTemplates,
}: ConfigStepProps) {
  const startHintId = useId();
  const chatKind = chatAgentKind(mode);
  const chat = chatSupport(capabilities, chatKind ?? 'claude');
  const usesChat = chatKind !== null && usesChatView(terminalUiOf(state, chatKind), chat);
  const canResume = usesChat && chat.kind === 'available' && chat.resume;
  // A worktree needs a stored git workspace; a one-off path has no repo to branch.
  const showsGitOptions =
    selection.kind === 'stored' && workspace?.is_git === true && mode !== 'auto_scan';

  // Behind the capability: an older agent ignores `base_branch` and has no list route.
  const baseBranch =
    showsGitOptions && workspace !== null && gitCapability(capabilities, 'worktree_base')
      ? {
          status: worktreeBases?.status ?? 'loading',
          bases: worktreeBases?.bases,
          value: effectiveWorktreeBase(
            state.worktreeBasePick,
            workspace.name,
            worktreeBases?.status === 'ready' ? worktreeBases.bases : undefined,
          ),
          onChange: (ref: string | null) => {
            dispatch({ type: 'setWorktreeBase', pick: { workspaceName: workspace.name, ref } });
          },
          onRetry: () => {
            worktreeBases?.retry();
          },
        }
      : null;

  // Auto Scan has three layouts: the source-and-agent panel for an agent with
  // `capabilities.autoscan`, a wait while the probe has not answered, and the
  // legacy Linear + Claude panel for an older agent (or a failed probe).
  const scanCapability = mode === 'auto_scan' ? autoScanCapability(capabilities) : null;
  const scan =
    scanCapability !== null && sourcesView !== undefined && workspace !== null
      ? {
          workspace,
          sources: autoScanSources(scanCapability),
          agents: autoScanAgentOptions(scanCapability, capabilities),
          view: sourcesView,
        }
      : null;
  const scanPending = mode === 'auto_scan' && scan === null && capabilitiesPending;
  const scanBlocker =
    scan === null
      ? null
      : autoScanBlocker({
          setup: state.autoScanSetup,
          prompt: state.autoScan.initialPrompt,
          sources: scan.sources,
          agents: scan.agents,
          github: scan.view.github,
          hasLinearKey: scan.workspace.has_linear_key,
          antigravityAddDirs: state.autoScanAgents.antigravity.addDirsDraft,
        });

  // The server 422s a flag-shaped directory, so refuse the launch rather than
  // showing the inline error and sending it anyway.
  const blocked =
    (mode === 'antigravity' && addDirsError(state.antigravity.addDirsDraft) !== null) ||
    scanPending ||
    scanBlocker !== null;

  return (
    <div className="wizard-body">
      <h2>{modeTitle(mode, customTypes)}</h2>

      {chatKind !== null && (
        <SessionViewField
          chat={chat}
          agent={chatKind}
          terminalUi={terminalUiOf(state, chatKind)}
          onChange={(value) => {
            dispatch(terminalUiAction(chatKind, value));
          }}
        />
      )}

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
          autoReviewSupported={cliFeature(capabilities, 'codex', 'auto_review')}
          dispatch={dispatch}
        />
      )}
      {mode === 'antigravity' && (
        <AntigravityOptionsPanel
          bucket={state.antigravity}
          models={cliModels(capabilities, 'antigravity')}
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
        <OpenCodeOptionsPanel bucket={state.opencode} dispatch={dispatch} />
      )}
      {mode === 'copilot' && (
        <CopilotOptionsPanel bucket={state.copilot} dispatch={dispatch} />
      )}
      {mode === 'cursor' && (
        <CursorOptionsPanel bucket={state.cursor} dispatch={dispatch} />
      )}
      {mode === 'junie' && (
        <JunieOptionsPanel bucket={state.junie} dispatch={dispatch} />
      )}
      {mode === 'pi' && (
        <PiOptionsPanel bucket={state.pi} dispatch={dispatch} />
      )}
      {mode === 'auto_scan' &&
        (scan !== null ? (
          <AutoScanPanel
            workspace={scan.workspace}
            state={state}
            sources={scan.sources}
            agents={scan.agents}
            models={{
              claude: cliModels(capabilities, 'claude'),
              codex: cliModels(capabilities, 'codex'),
              codexDetails: cliModelDetails(capabilities, 'codex'),
              antigravity: cliModels(capabilities, 'antigravity'),
              codexAutoReview: cliFeature(capabilities, 'codex', 'auto_review'),
            }}
            sourcesView={scan.view}
            templates={autoScanTemplates}
            submitting={submitting}
            dispatch={dispatch}
          />
        ) : scanPending ? (
          <p className="page-note state-loading">Checking the agent on your PC…</p>
        ) : (
          <>
            {/* Only for an agent known to predate the delta, not a failed probe. */}
            {capabilities !== undefined && (
              <p className="page-note">
                Update Mobaide on your PC to scan GitHub issues and choose the agent.
              </p>
            )}
            <AutoScanOptionsPanel
              bucket={state.autoScan}
              models={cliModels(capabilities, 'claude')}
              dispatch={dispatch}
            />
          </>
        ))}
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
          aria-describedby={scanBlocker !== null ? startHintId : undefined}
          onClick={onSubmit}
        >
          {/* Not while the GitHub setup terminal opens: that is no start. */}
          {submitting && sourcesView?.githubSetupPending !== true
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
      {scanBlocker !== null && (
        <p className="field-hint" id={startHintId}>
          {scanBlocker}
        </p>
      )}
    </div>
  );
}
