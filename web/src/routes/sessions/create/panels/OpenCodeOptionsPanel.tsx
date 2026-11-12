import { CheckboxField } from '../../../../components/form/CheckboxField';
import type { ChatSupport } from '../../../../protocol/dto/agentVersion';
import type { OpenCodeBucket, WizardAction } from '../wizardState';
import { TerminalUiField } from './TerminalUiField';

interface OpenCodeOptionsPanelProps {
  bucket: OpenCodeBucket;
  dispatch: (action: WizardAction) => void;
  /** Shows the Terminal UI toggle, gated on this PC's chat support for OpenCode. */
  chat?: ChatSupport;
}

/**
 * OpenCode has no model or agent option at launch: both are picked inside the
 * session (the chat's chips, or the terminal UI's own lists).
 */
export function OpenCodeOptionsPanel({ bucket, dispatch, chat }: OpenCodeOptionsPanelProps) {
  return (
    <div className="option-group">
      <CheckboxField
        label="Auto-approve permissions (dangerous)"
        checked={bucket.autoApprove}
        hint="OpenCode accepts every request that your rules do not deny."
        onChange={(value) => {
          dispatch({ type: 'setOpenCodeAutoApprove', value });
        }}
      />
      {chat !== undefined && (
        <details className="advanced advanced-stack">
          <summary>Advanced</summary>
          <TerminalUiField
            chat={chat}
            agent="opencode"
            checked={bucket.terminalUi}
            onChange={(value) => {
              dispatch({ type: 'setOpenCodeTerminalUi', value });
            }}
          />
        </details>
      )}
    </div>
  );
}
