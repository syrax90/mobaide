import { cliModels, type AgentVersionInfo } from '../../../protocol/dto/agentVersion';
import { customSessionTypeId } from '../../../protocol/wireSchema';
import type { ProjectSummary } from '../../../api/projectRepository';
import type { CustomAgentType } from '../../../api/customAgentTypeRepository';
import { AntigravityOptionsPanel } from './panels/AntigravityOptionsPanel';
import { AutoScanOptionsPanel } from './panels/AutoScanOptionsPanel';
import { ClaudeOptionsPanel } from './panels/ClaudeOptionsPanel';
import { CodexOptionsPanel } from './panels/CodexOptionsPanel';
import { WorktreeSection } from './panels/WorktreeSection';
import {
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
}: ConfigStepProps) {
  // A worktree needs a stored git project; a one-off path has no repo to branch.
  const showsGitOptions =
    selection.kind === 'stored' && project?.isGit === true && mode !== 'auto_scan';

  return (
    <div className="wizard-body">
      <h2>{modeTitle(mode, customTypes)}</h2>

      {mode === 'claude' && (
        <ClaudeOptionsPanel
          bucket={state.claude}
          bucketId="claude"
          models={cliModels(capabilities, 'claude')}
          dispatch={dispatch}
        />
      )}
      {mode === 'codex' && (
        <CodexOptionsPanel
          bucket={state.codex}
          models={cliModels(capabilities, 'codex')}
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

      {error !== null && <p className="field-error">{error}</p>}

      <div className="wizard-actions">
        <button
          type="button"
          className="button button-primary"
          disabled={submitting}
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
