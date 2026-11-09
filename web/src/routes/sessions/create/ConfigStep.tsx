import {
  chatSupport,
  cliModelDetails,
  cliModels,
  type AgentVersionInfo,
} from '../../../protocol/dto/agentVersion';
import { customSessionTypeId } from '../../../protocol/wireSchema';
import type { ProjectSummary } from '../../../api/projectRepository';
import type { CustomAgentType } from '../../../api/customAgentTypeRepository';
import { AntigravityOptionsPanel } from './panels/AntigravityOptionsPanel';
import { AutoScanOptionsPanel } from './panels/AutoScanOptionsPanel';
import { ClaudeAdvancedPanel } from './panels/ClaudeAdvancedPanel';
import { ClaudeOptionsPanel } from './panels/ClaudeOptionsPanel';
import { CodexOptionsPanel } from './panels/CodexOptionsPanel';
import { WorktreeSection } from './panels/WorktreeSection';
import { chatAgentKind, usesChatView } from './buildCreateRequest';
import {
  addDirsError,
  isCustomMode,
  type ProjectSelection,
  type WizardAction,
  type WizardMode,
  type WizardState,
} from './wizardState';

interface ConfigStepProps {
  mode: WizardMode;
  state: WizardState;
  /** From the URL, not the reducer — the reducer's copy does not survive a remount. */
  selection: ProjectSelection;
  project: ProjectSummary | null;
  capabilities: AgentVersionInfo | undefined;
  customTypes: readonly CustomAgentType[];
  submitting: boolean;
  error: string | null;
  dispatch: (action: WizardAction) => void;
  onSubmit: () => void;
  onBack: () => void;
  /** Open the "Continue a session" picker; shown only where the PC can resume. */
  onContinueSession?: () => void;
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

export function ConfigStep({
  mode,
  state,
  selection,
  project,
  capabilities,
  customTypes,
  submitting,
  error,
  dispatch,
  onSubmit,
  onBack,
  onContinueSession,
}: ConfigStepProps) {
  const chatKind = chatAgentKind(mode);
  const chat = chatSupport(capabilities, chatKind ?? 'claude');
  const terminalUi = chatKind === 'codex' ? state.codex.terminalUi : state.claude.terminalUi;
  const usesChat = chatKind !== null && usesChatView(terminalUi, chat);
  const canResume = usesChat && chat.kind === 'available' && chat.resume;
  // A worktree needs a stored git project; a one-off path has no repo to branch.
  const showsGitOptions =
    selection.kind === 'stored' && project?.is_git === true && mode !== 'auto_scan';

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
            <p className="field-hint">
              {chatKind === 'codex'
                ? 'Open a Codex session started on your PC, for example in a terminal.'
                : 'Open a Claude Code session started on your PC, for example in a terminal.'}
            </p>
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
          dispatch={dispatch}
        />
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

      {showsGitOptions && <WorktreeSection git={state.git} dispatch={dispatch} />}

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
