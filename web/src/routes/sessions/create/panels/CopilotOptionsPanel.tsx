import { CheckboxField } from '../../../../components/form/CheckboxField';
import type { ChatSupport } from '../../../../protocol/dto/agentVersion';
import type { CopilotBucket, WizardAction } from '../wizardState';
import { TerminalUiField } from './TerminalUiField';

interface CopilotOptionsPanelProps {
  bucket: CopilotBucket;
  dispatch: (action: WizardAction) => void;
  /** Shows the Terminal UI toggle, gated on this PC's chat support for Copilot. */
  chat?: ChatSupport;
}

/**
 * Copilot has no model option at launch: the models depend on the account's
 * plan, and the model is picked inside the session.
 */
export function CopilotOptionsPanel({ bucket, dispatch, chat }: CopilotOptionsPanelProps) {
  return (
    <div className="option-group">
      <CheckboxField
        label="Allow all tools, paths and URLs (dangerous)"
        checked={bucket.allowAll}
        hint="When on, Copilot does not ask before it runs commands, edits files or opens URLs."
        onChange={(value) => {
          dispatch({ type: 'setCopilotAllowAll', value });
        }}
      />
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
