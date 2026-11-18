import { useId } from 'react';
import { CheckboxField } from '../../../../components/form/CheckboxField';
import { TextAreaField } from '../../../../components/form/TextAreaField';
import type { WorkspaceSummary } from '../../../../api/workspaceRepository';
import {
  AUTO_SCAN_SOURCE_TITLES,
  type AutoScanAgentOption,
} from '../autoScanSetup';
import type { AutoScanSourcesView } from '../useAutoScanSources';
import type { AutoScanSource, WizardAction, WizardState } from '../wizardState';
import { ClaudeAdvancedPanel } from './ClaudeAdvancedPanel';
import { ClaudeOptionsPanel } from './ClaudeOptionsPanel';
import { GitHubSourcePanel } from './GitHubSourcePanel';
import { LinearSourcePanel } from './LinearSourcePanel';

const SOURCE_HINTS: Readonly<Record<AutoScanSource, string>> = {
  github: 'Open issues of this workspace’s GitHub repository, read with the GitHub CLI (gh) on the PC.',
  linear: 'Issues of your Linear team, read with this workspace’s Linear API key.',
};

interface AutoScanPanelProps {
  workspace: WorkspaceSummary;
  state: WizardState;
  sources: readonly AutoScanSource[];
  agents: readonly AutoScanAgentOption[];
  claudeModels: readonly string[] | null;
  sourcesView: AutoScanSourcesView;
  submitting: boolean;
  dispatch: (action: WizardAction) => void;
}

/**
 * Auto Scan for an agent with `capabilities.autoscan`: where to find issues,
 * the source's filter, which agent runs each issue and with which options,
 * and the first prompt. Nothing is pre-chosen except what this workspace
 * chose last time (`workspace.auto_scan`).
 */
export function AutoScanPanel({
  workspace,
  state,
  sources,
  agents,
  claudeModels,
  sourcesView,
  submitting,
  dispatch,
}: AutoScanPanelProps) {
  const sourceName = useId();
  const agentName = useId();
  const setup = state.autoScanSetup;

  return (
    <>
      {/* The Linear filter section carries its own worktree note. */}
      {setup.source !== 'linear' && (
        <p className="page-note">
          Each issue runs in its own git worktree, so its changes stay isolated from your
          working copy.
        </p>
      )}

      <div className="option-group">
        <fieldset className="choice-group">
          <legend>Issue source</legend>
          {sources.map((source) => (
            <label key={source} className="radio-row">
              <input
                type="radio"
                name={sourceName}
                checked={setup.source === source}
                onChange={() => {
                  dispatch({ type: 'setAutoScanSource', source });
                }}
              />
              <span>
                <strong>{AUTO_SCAN_SOURCE_TITLES[source]}</strong>
                <small>{SOURCE_HINTS[source]}</small>
              </span>
            </label>
          ))}
        </fieldset>
      </div>

      {setup.source === 'github' && (
        <GitHubSourcePanel
          filter={setup.githubFilter}
          sources={sourcesView}
          submitting={submitting}
          dispatch={dispatch}
        />
      )}
      {setup.source === 'linear' && (
        <LinearSourcePanel
          hasLinearKey={workspace.has_linear_key}
          filter={setup.linearFilter}
          sources={sourcesView}
          dispatch={dispatch}
        />
      )}

      <div className="option-group">
        <fieldset className="choice-group">
          <legend>AI agent</legend>
          <p className="field-hint">The agent that works on each issue, in a session of its own.</p>
          {agents.map((agent) => (
            <label
              key={agent.id}
              className={`radio-row${agent.available ? '' : ' radio-row-disabled'}`}
            >
              <input
                type="radio"
                name={agentName}
                checked={setup.cli === agent.id}
                disabled={!agent.available}
                onChange={() => {
                  dispatch({ type: 'setAutoScanCli', cli: agent.id });
                }}
              />
              <span>
                <strong>{agent.name}</strong>
                {!agent.available && <small>Not installed on the PC</small>}
              </span>
            </label>
          ))}
        </fieldset>
      </div>

      {setup.cli === 'claude' && (
        <>
          <ClaudeOptionsPanel
            bucket={state.autoScan}
            bucketId="autoScan"
            models={claudeModels}
            dispatch={dispatch}
            skipPermissionsHint="When off, the scan pauses at permission prompts — you'll get a notification and can open the session to approve."
          />
          <ClaudeAdvancedPanel bucket={state.autoScan} bucketId="autoScan" dispatch={dispatch} />
        </>
      )}
      {setup.cli === 'pi' && (
        <div className="option-group">
          <p className="field-hint">Pi runs its tools without asking.</p>
        </div>
      )}
      {setup.cli !== null && setup.cli !== 'claude' && setup.cli !== 'pi' && (
        <div className="option-group">
          <CheckboxField
            label="Run without asking for permission (dangerous)"
            checked={setup.autoApprove}
            hint="When on, the agent runs commands without asking (Codex also without its sandbox). When off, it may stop and wait for your approval in its session; Codex never asks and stays in its sandbox."
            onChange={(value) => {
              dispatch({ type: 'setAutoScanAutoApprove', value });
            }}
          />
        </div>
      )}

      <div className="option-group">
        <TextAreaField
          label="Initial prompt (optional)"
          value={state.autoScan.initialPrompt}
          rows={5}
          hint="The issue’s title, link and description are added after your prompt."
          onChange={(value) => {
            dispatch({
              type: 'setClaudeText',
              bucket: 'autoScan',
              field: 'initialPrompt',
              value,
            });
          }}
        />
      </div>
    </>
  );
}
