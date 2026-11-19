import { SelectField } from '../../../../components/form/SelectField';
import { CODEX_APPROVALS, CODEX_SANDBOXES } from '../../../../protocol/dto/sessionCreate';
import type { ChatSupport, CliModelDetail } from '../../../../protocol/dto/agentVersion';
import {
  CODEX_MODELS,
  codexEffortsFor,
  codexPreset,
  type CodexApproval,
  type CodexBucket,
  type CodexPreset,
  type AgentBucketTarget,
  type CodexSandbox,
  type WizardAction,
} from '../wizardState';
import { AUTO_SCAN_CODEX_ASK_HINT } from '../autoScanSetup';
import { modelOptions } from './ClaudeOptionsPanel';
import { ScanAskHint } from './ScanAskHint';
import { TerminalUiField } from './TerminalUiField';

interface CodexOptionsPanelProps {
  bucket: CodexBucket;
  models: readonly string[] | null;
  modelDetails: readonly CliModelDetail[] | null;
  dispatch: (action: WizardAction) => void;
  /** Shows the Terminal UI toggle, gated on this PC's chat support for Codex. */
  chat?: ChatSupport;
  /** Auto Scan edits its own copy of the options. */
  target?: AgentBucketTarget;
}

const PRESETS: readonly { preset: CodexPreset; label: string; description: string }[] = [
  {
    preset: 'readOnly',
    label: 'Read-only',
    description: 'Reads the workspace; asks before any change.',
  },
  {
    preset: 'auto',
    label: 'Auto',
    description: 'Edits within the workspace; asks before risky actions.',
  },
  {
    preset: 'fullAccess',
    label: 'Full access',
    description: 'No sandbox or approval prompts (--yolo).',
  },
];

export function CodexOptionsPanel({
  bucket,
  models,
  modelDetails,
  dispatch,
  chat,
  target,
}: CodexOptionsPanelProps) {
  const active = codexPreset(bucket);
  const efforts = codexEffortsFor(modelDetails, bucket.model);
  // Same keep-selected repair as `modelOptions`: a restored or late-probe
  // effort outside the advertised set must stay renderable, or the controlled
  // <select> goes blank while the request still sends the value.
  const effortOptions =
    bucket.reasoningEffort !== null && !efforts.includes(bucket.reasoningEffort)
      ? [...efforts, bucket.reasoningEffort]
      : efforts;

  return (
    <div className="option-group">
      <h3>Autonomy</h3>
      <div className="preset-row">
        {PRESETS.map((entry) => (
          <button
            key={entry.preset}
            type="button"
            className={`preset-card${active === entry.preset ? ' preset-card-active' : ''}`}
            aria-pressed={active === entry.preset}
            onClick={() => {
              dispatch({ type: 'setCodexPreset', preset: entry.preset, target });
            }}
          >
            <span className="preset-title">{entry.label}</span>
            <span className="preset-description">{entry.description}</span>
          </button>
        ))}
      </div>
      {active === null && (
        <p className="field-hint">Custom sandbox/approval set in Advanced.</p>
      )}
      {/* Approval `never` does not ask, even without Full access. */}
      <ScanAskHint
        show={target === 'autoScan' && !bucket.fullAuto && bucket.approval !== 'never'}
        text={AUTO_SCAN_CODEX_ASK_HINT}
        flush
      />

      <SelectField
        label="Model"
        value={bucket.model}
        options={modelOptions(models, CODEX_MODELS, bucket.model)}
        onChange={(model) => {
          dispatch({
            type: 'setCodexModel',
            model,
            detail: model !== null
              ? (modelDetails?.find((detail) => detail.id === model) ?? null)
              : null,
            target,
          });
        }}
      />
      <SelectField
        label="Reasoning effort"
        value={bucket.reasoningEffort}
        // `null` is a real wire value here, not "unset": it tells the agent to
        // emit no --reasoning-effort flag at all.
        options={[
          { value: null, label: 'Agent default' },
          ...effortOptions.map((effort) => ({ value: effort, label: effort })),
        ]}
        onChange={(effort) => {
          dispatch({ type: 'setCodexReasoningEffort', effort, target });
        }}
      />

      <details className="advanced advanced-stack">
        <summary>Advanced</summary>
        {chat !== undefined && (
          <TerminalUiField
            chat={chat}
            agent="codex"
            checked={bucket.terminalUi}
            onChange={(value) => {
              dispatch({ type: 'setCodexTerminalUi', value });
            }}
          />
        )}
        <SelectField
          label="Sandbox"
          value={bucket.sandbox}
          options={CODEX_SANDBOXES.map((value) => ({ value, label: value }))}
          disabled={bucket.fullAuto}
          hint={
            bucket.fullAuto
              ? 'Sandbox and approval are ignored in Full-access (--yolo).'
              : undefined
          }
          onChange={(sandbox) => {
            if (sandbox !== null) {
              dispatch({ type: 'setCodexSandbox', sandbox: sandbox as CodexSandbox, target });
            }
          }}
        />
        <SelectField
          label="Approval"
          value={bucket.approval}
          options={CODEX_APPROVALS.map((value) => ({ value, label: value }))}
          disabled={bucket.fullAuto}
          onChange={(approval) => {
            if (approval !== null) {
              dispatch({
                type: 'setCodexApproval',
                approval: approval as CodexApproval,
                target,
              });
            }
          }}
        />
      </details>
    </div>
  );
}
