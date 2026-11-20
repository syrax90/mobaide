import { useId, type ReactNode } from 'react';
import { SelectField } from '../../../../components/form/SelectField';
import { TextAreaField } from '../../../../components/form/TextAreaField';
import type { CliModelDetail } from '../../../../protocol/dto/agentVersion';
import type { WorkspaceSummary } from '../../../../api/workspaceRepository';
import {
  AUTO_SCAN_CHAT_UNKNOWN_HINT,
  AUTO_SCAN_JUNIE_TRUST_HINT,
  AUTO_SCAN_SOURCE_TITLES,
  autoScanIntervalChoices,
  autoScanIntervalLabel,
  type AutoScanAgentOption,
} from '../autoScanSetup';
import type { AutoScanSourcesView } from '../useAutoScanSources';
import type { AutoScanTemplatesView } from '../useAutoScanTemplates';
import type { AutoScanSource, AutoScanUi, WizardAction, WizardState } from '../wizardState';
import { AntigravityOptionsPanel } from './AntigravityOptionsPanel';
import { AutoScanTemplatesPanel } from './AutoScanTemplatesPanel';
import { ClaudeAdvancedPanel } from './ClaudeAdvancedPanel';
import { ClaudeOptionsPanel } from './ClaudeOptionsPanel';
import { CodexOptionsPanel } from './CodexOptionsPanel';
import { CopilotOptionsPanel } from './CopilotOptionsPanel';
import { CursorOptionsPanel } from './CursorOptionsPanel';
import { GitHubSourcePanel } from './GitHubSourcePanel';
import { JunieOptionsPanel } from './JunieOptionsPanel';
import { LinearSourcePanel } from './LinearSourcePanel';
import { OpenCodeOptionsPanel } from './OpenCodeOptionsPanel';
import { ScanCallout } from './ScanCallout';
import { SessionViewChoice, terminalUiHint } from './SessionViewField';

const SOURCE_HINTS: Readonly<Record<AutoScanSource, string>> = {
  github: 'Open issues of this workspace’s GitHub repository, read with the GitHub CLI (gh) on the PC.',
  linear: 'Issues of your Linear team, read with this workspace’s Linear API key.',
};

/** The models each agent's picker offers, from the PC's probe (`null`: not known yet). */
export interface AutoScanModels {
  claude: readonly string[] | null;
  codex: readonly string[] | null;
  codexDetails: readonly CliModelDetail[] | null;
  antigravity: readonly string[] | null;
}

interface AutoScanPanelProps {
  workspace: WorkspaceSummary;
  state: WizardState;
  sources: readonly AutoScanSource[];
  agents: readonly AutoScanAgentOption[];
  models: AutoScanModels;
  sourcesView: AutoScanSourcesView;
  /** Absent where the wizard has no template list to show. */
  templates?: AutoScanTemplatesView;
  submitting: boolean;
  dispatch: (action: WizardAction) => void;
}

/**
 * Auto Scan for an agent with `capabilities.autoscan`: templates, how a scan
 * works, then four numbered steps — where to find issues, which issues, the
 * agent that works on each with that agent's own options, and the
 * instructions. Nothing is pre-chosen except what this workspace chose last
 * time (`workspace.auto_scan`) or a template the user applies.
 */
export function AutoScanPanel({
  workspace,
  state,
  sources,
  agents,
  models,
  sourcesView,
  templates,
  submitting,
  dispatch,
}: AutoScanPanelProps) {
  const sourceName = useId();
  const agentName = useId();
  const setup = state.autoScanSetup;
  const chosenAgent = agents.find((entry) => entry.id === setup.cli);

  return (
    <>
      <p className="page-note autoscan-intro">
        An AI agent picks up matching issues one by one and works on each in its own session.
      </p>

      {templates !== undefined && (
        <AutoScanTemplatesPanel templates={templates} state={state} dispatch={dispatch} />
      )}

      <HowItWorks
        isGit={workspace.is_git}
        source={setup.source}
        doneState={setup.linearFilter.done_state}
      />

      <ScanStep number={1} title="Where to find issues">
        <fieldset className="autoscan-choices">
          <legend className="visually-hidden">Where to find issues</legend>
          {sources.map((source) => (
            <label key={source} className="autoscan-choice">
              <input
                type="radio"
                name={sourceName}
                checked={setup.source === source}
                onChange={() => {
                  dispatch({ type: 'setAutoScanSource', source });
                }}
              />
              <span className="autoscan-choice-text">
                <strong>{AUTO_SCAN_SOURCE_TITLES[source]}</strong>
                <small>{SOURCE_HINTS[source]}</small>
              </span>
            </label>
          ))}
        </fieldset>
      </ScanStep>

      <ScanStep number={2} title="Which issues">
        {setup.source === 'github' ? (
          <GitHubSourcePanel
            filter={setup.githubFilter}
            sources={sourcesView}
            submitting={submitting}
            dispatch={dispatch}
          />
        ) : setup.source === 'linear' ? (
          <LinearSourcePanel
            hasLinearKey={workspace.has_linear_key}
            filter={setup.linearFilter}
            sources={sourcesView}
            dispatch={dispatch}
          />
        ) : (
          <p className="field-hint">Choose where to find issues first.</p>
        )}
      </ScanStep>

      <ScanStep number={3} title="AI agent">
        <fieldset className="autoscan-choices autoscan-choices-grid">
          <legend className="field-hint">The agent that works on each issue, in a session of its own.</legend>
          {agents.map((agent) => (
            <label
              key={agent.id}
              className={`autoscan-choice${agent.available ? '' : ' autoscan-choice-disabled'}`}
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
              <span className="autoscan-choice-text">
                <strong>{agent.name}</strong>
                {!agent.available && <small>Not installed on the PC</small>}
              </span>
            </label>
          ))}
        </fieldset>
        {/* Before the options: choosing Chat hides some of them, which must not move this. */}
        <AutoScanSessionView
          agent={chosenAgent}
          ui={setup.ui}
          dispatch={dispatch}
        />
        <AgentOptions
          state={state}
          models={models}
          inChat={setup.ui === 'chat' && chosenAgent?.chat === true}
          dispatch={dispatch}
        />
      </ScanStep>

      <ScanStep number={4} title="Instructions and schedule">
        <TextAreaField
          label="Instructions for the agent (optional)"
          value={state.autoScan.initialPrompt}
          rows={5}
          hint="Your playbook for every issue. The issue's title, link and description are added after it."
          onChange={(value) => {
            dispatch({
              type: 'setClaudeText',
              bucket: 'autoScan',
              field: 'initialPrompt',
              value,
            });
          }}
        />
        <SelectField
          label="Time between scans"
          value={String(setup.intervalMinutes)}
          options={autoScanIntervalChoices(setup.intervalMinutes).map((minutes) => ({
            value: String(minutes),
            label: autoScanIntervalLabel(minutes),
          }))}
          hint="After each scan, Auto Scan waits this long before it looks for new issues. Run now starts a scan at once."
          onChange={(value) => {
            if (value !== null) dispatch({ type: 'setAutoScanInterval', minutes: Number(value) });
          }}
        />
      </ScanStep>
    </>
  );
}

/**
 * "Open sessions in": the terminal or the chat view, for every issue. Chat is
 * offered only for an agent the PC has a chat view for; otherwise Terminal
 * shows as chosen, and that is what the start sends.
 */
function AutoScanSessionView({
  agent,
  ui,
  dispatch,
}: {
  agent: AutoScanAgentOption | undefined;
  ui: AutoScanUi;
  dispatch: (action: WizardAction) => void;
}) {
  if (agent === undefined) return null;
  const shown: AutoScanUi = ui === 'chat' && agent.chat ? 'chat' : 'terminal';
  return (
    <SessionViewChoice
      view={shown}
      chatEnabled={agent.chat}
      onChange={(view) => {
        dispatch({ type: 'setAutoScanUi', ui: view });
      }}
    >
      {!agent.chat && (
        <p className="field-hint">
          {agent.chatSupport.kind === 'unknown'
            ? AUTO_SCAN_CHAT_UNKNOWN_HINT
            : terminalUiHint(agent.chatSupport, agent.id)}
        </p>
      )}
      {agent.chat && agent.id === 'junie' && shown === 'terminal' && (
        <p className="field-hint">{AUTO_SCAN_JUNIE_TRUST_HINT}</p>
      )}
    </SessionViewChoice>
  );
}

function ScanStep({
  number,
  title,
  children,
}: {
  number: number;
  title: string;
  children: ReactNode;
}) {
  const titleId = useId();
  return (
    <section className="option-group autoscan-step" aria-labelledby={titleId}>
      <h3 className="autoscan-step-title" id={titleId}>
        <span className="autoscan-step-number" aria-hidden="true">
          {number}
        </span>
        {title}
      </h3>
      {children}
    </section>
  );
}

/** Said once, at the top: where each issue runs, when it starts, when its worktree goes. */
function HowItWorks({
  isGit,
  source,
  doneState,
}: {
  isGit: boolean;
  source: AutoScanSource | null;
  doneState: string;
}) {
  const done = doneState.trim() === '' ? 'Done' : doneState.trim();
  return (
    <ScanCallout tone="info" title="How Auto Scan works">
      <ul className="autoscan-callout-list">
        <li>
          {isGit
            ? "Each issue runs in its own git worktree and branch, so the agent's work never touches your working copy."
            : 'This folder is not a git repository, so each issue runs in the folder itself.'}
        </li>
        <li>
          Each session starts at once with the issue as its prompt. You get a notification when
          it needs you.
        </li>
        {isGit && (
          <li>
            Auto Scan trusts each worktree it creates, so the agent does not ask to trust the
            folder.
          </li>
        )}
        {isGit && source === 'github' && (
          <li>
            When an issue is closed, its worktree is removed. A worktree with uncommitted
            changes is kept.
          </li>
        )}
        {isGit && source === 'linear' && (
          <li>When an issue moves to “{done}”, its worktree is removed.</li>
        )}
      </ul>
    </ScanCallout>
  );
}

/** The chosen agent's own options, in the normal session's panels on Auto Scan's buckets. */
function AgentOptions({
  state,
  models,
  inChat,
  dispatch,
}: {
  state: WizardState;
  models: AutoScanModels;
  /** The sessions open in the chat view, so terminal-only options are hidden. */
  inChat: boolean;
  dispatch: (action: WizardAction) => void;
}) {
  const agents = state.autoScanAgents;
  switch (state.autoScanSetup.cli) {
    case 'claude':
      return (
        <>
          <ClaudeOptionsPanel
            bucket={state.autoScan}
            bucketId="autoScan"
            models={models.claude}
            showTerminalOptions={!inChat}
            dispatch={dispatch}
          />
          <ClaudeAdvancedPanel bucket={state.autoScan} bucketId="autoScan" dispatch={dispatch} />
        </>
      );
    case 'codex':
      return (
        <CodexOptionsPanel
          bucket={agents.codex}
          models={models.codex}
          modelDetails={models.codexDetails}
          dispatch={dispatch}
          target="autoScan"
        />
      );
    case 'antigravity':
      return (
        <AntigravityOptionsPanel
          bucket={agents.antigravity}
          models={models.antigravity}
          dispatch={dispatch}
          target="autoScan"
        />
      );
    case 'opencode':
      return <OpenCodeOptionsPanel bucket={agents.opencode} dispatch={dispatch} target="autoScan" />;
    case 'copilot':
      return <CopilotOptionsPanel bucket={agents.copilot} dispatch={dispatch} target="autoScan" />;
    case 'cursor':
      return <CursorOptionsPanel bucket={agents.cursor} dispatch={dispatch} target="autoScan" />;
    case 'junie':
      return <JunieOptionsPanel bucket={agents.junie} dispatch={dispatch} target="autoScan" />;
    case 'pi':
      return <p className="field-hint">Pi runs its tools without asking.</p>;
    default:
      return null;
  }
}
