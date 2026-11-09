import {
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
import type { BaseBranchStatus } from './panels/BaseBranchPicker';
import { WorktreeSection } from './panels/WorktreeSection';
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
  worktreeBases,
  submitting,
  error,
  dispatch,
  onSubmit,
  onBack,
}: ConfigStepProps) {
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
          modelDetails={cliModelDetails(capabilities, 'codex')}
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

      {showsGitOptions && (
        <WorktreeSection git={state.git} dispatch={dispatch} baseBranch={baseBranch} />
      )}

      {mode === 'claude' && (
        <ClaudeAdvancedPanel bucket={state.claude} bucketId="claude" dispatch={dispatch} />
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
