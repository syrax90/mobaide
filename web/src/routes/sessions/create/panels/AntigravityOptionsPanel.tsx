import { CheckboxField } from '../../../../components/form/CheckboxField';
import { SelectField } from '../../../../components/form/SelectField';
import { TextAreaField } from '../../../../components/form/TextAreaField';
import type { ChatSupport } from '../../../../protocol/dto/agentVersion';
import {
  addDirsError,
  ANTIGRAVITY_MODELS,
  type AgentBucketTarget,
  type AntigravityBucket,
  type WizardAction,
} from '../wizardState';
import { modelOptions } from './ClaudeOptionsPanel';
import { ScanAskHint } from './ScanAskHint';
import { TerminalUiField } from './TerminalUiField';

interface AntigravityOptionsPanelProps {
  bucket: AntigravityBucket;
  models: readonly string[] | null;
  dispatch: (action: WizardAction) => void;
  /** Shows the Terminal UI toggle, gated on this PC's chat support for Antigravity. */
  chat?: ChatSupport;
  /** Auto Scan edits its own copy of the options. */
  target?: AgentBucketTarget;
}

export function AntigravityOptionsPanel({
  bucket,
  models,
  dispatch,
  chat,
  target,
}: AntigravityOptionsPanelProps) {
  return (
    <div className="option-group">
      <SelectField
        label="Model"
        value={bucket.model}
        options={modelOptions(models, ANTIGRAVITY_MODELS, bucket.model, 'Auto')}
        onChange={(model) => {
          dispatch({ type: 'setAntigravityModel', model, target });
        }}
      />
      {/*
        Mutually exclusive: --dangerously-skip-permissions auto-approves sandbox
        escapes, so the pair is a 422 server-side. The reducer enforces it too —
        disabling alone would not survive a restored preference.
      */}
      <CheckboxField
        label="Skip permissions (dangerous)"
        checked={bucket.skipPermissions}
        disabled={bucket.sandbox}
        onChange={(value) => {
          dispatch({ type: 'setAntigravitySkipPermissions', value, target });
        }}
      />
      <ScanAskHint show={target === 'autoScan' && !bucket.skipPermissions} />
      <CheckboxField
        label="Sandbox"
        checked={bucket.sandbox}
        disabled={bucket.skipPermissions}
        onChange={(value) => {
          dispatch({ type: 'setAntigravitySandbox', value, target });
        }}
      />
      <TextAreaField
        label="Additional directories"
        value={bucket.addDirsDraft}
        placeholder="/one/path, /another/path"
        hint="One per line, or comma-separated."
        error={addDirsError(bucket.addDirsDraft) ?? undefined}
        onChange={(draft) => {
          dispatch({ type: 'setAntigravityAddDirs', draft, target });
        }}
      />
      {chat !== undefined && (
        <details className="advanced advanced-stack">
          <summary>Advanced</summary>
          <TerminalUiField
            chat={chat}
            agent="antigravity"
            checked={bucket.terminalUi}
            onChange={(value) => {
              dispatch({ type: 'setAntigravityTerminalUi', value });
            }}
          />
        </details>
      )}
    </div>
  );
}
