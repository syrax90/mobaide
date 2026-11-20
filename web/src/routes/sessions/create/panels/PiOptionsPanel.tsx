import { CheckboxField } from '../../../../components/form/CheckboxField';
import type { ChatSupport } from '../../../../protocol/dto/agentVersion';
import type { PiBucket, WizardAction } from '../wizardState';
import { TerminalUiField } from './TerminalUiField';

interface PiOptionsPanelProps {
  bucket: PiBucket;
  dispatch: (action: WizardAction) => void;
  /** Shows the Terminal UI toggle, gated on this PC's chat support for Pi. */
  chat?: ChatSupport;
}

export const PI_LAUNCH_NOTE =
  'Pi runs commands and edits files without asking. ' +
  'Pick the model and the thinking level inside the session.';

export const PI_CODE_MODE_HINT =
  "Adds Pi's codemode tool: the model writes JavaScript that calls several tools in one step.";

/**
 * Pi has no approval mode, and the model and thinking level are picked inside
 * the session. Code Mode is its one launch option.
 */
export function PiOptionsPanel({ bucket, dispatch, chat }: PiOptionsPanelProps) {
  return (
    <div className="option-group">
      <p className="field-hint">{PI_LAUNCH_NOTE}</p>
      <CheckboxField
        label="Code Mode"
        checked={bucket.codeMode}
        hint={PI_CODE_MODE_HINT}
        onChange={(value) => {
          dispatch({ type: 'setPiCodeMode', value });
        }}
      />
      {chat !== undefined && (
        <details className="advanced advanced-stack">
          <summary>Advanced</summary>
          <TerminalUiField
            chat={chat}
            agent="pi"
            checked={bucket.terminalUi}
            onChange={(value) => {
              dispatch({ type: 'setPiTerminalUi', value });
            }}
          />
        </details>
      )}
    </div>
  );
}
