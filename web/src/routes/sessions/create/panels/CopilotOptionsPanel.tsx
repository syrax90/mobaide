import { CheckboxField } from '../../../../components/form/CheckboxField';
import type { ChatSupport } from '../../../../protocol/dto/agentVersion';
import type { AgentBucketTarget, CopilotBucket, WizardAction } from '../wizardState';
import { ScanAskHint } from './ScanAskHint';
import { TerminalUiField } from './TerminalUiField';

interface CopilotOptionsPanelProps {
  bucket: CopilotBucket;
  dispatch: (action: WizardAction) => void;
  /** Shows the Terminal UI toggle, gated on this PC's chat support for Copilot. */
  chat?: ChatSupport;
  /** Auto Scan edits its own copy of the options. */
  target?: AgentBucketTarget;
}

/**
 * Copilot has no model option at launch: the models depend on the account's
 * plan, and the model is picked inside the session.
 */
export function CopilotOptionsPanel({ bucket, dispatch, chat, target }: CopilotOptionsPanelProps) {
  return (
    <div className="option-group">
      <CheckboxField
        label="Allow all tools, paths and URLs (dangerous)"
        checked={bucket.allowAll}
        hint="When on, Copilot does not ask before it runs commands, edits files or opens URLs."
        onChange={(value) => {
          dispatch({ type: 'setCopilotAllowAll', value, target });
        }}
      />
      <ScanAskHint show={target === 'autoScan' && !bucket.allowAll} />
      {chat !== undefined && (
        <details className="advanced advanced-stack">
          <summary>Advanced</summary>
          <TerminalUiField
            chat={chat}
            agent="copilot"
            checked={bucket.terminalUi}
            onChange={(value) => {
              dispatch({ type: 'setCopilotTerminalUi', value });
            }}
          />
        </details>
      )}
    </div>
  );
}
