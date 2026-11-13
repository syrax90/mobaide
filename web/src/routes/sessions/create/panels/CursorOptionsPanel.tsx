import { CheckboxField } from '../../../../components/form/CheckboxField';
import type { ChatSupport } from '../../../../protocol/dto/agentVersion';
import type { CursorBucket, WizardAction } from '../wizardState';
import { TerminalUiField } from './TerminalUiField';

interface CursorOptionsPanelProps {
  bucket: CursorBucket;
  dispatch: (action: WizardAction) => void;
  /** Shows the Terminal UI toggle, gated on this PC's chat support for Cursor. */
  chat?: ChatSupport;
}

/**
 * Cursor has no model option at launch: the model, and its settings, are
 * picked inside the session.
 */
export function CursorOptionsPanel({ bucket, dispatch, chat }: CursorOptionsPanelProps) {
  return (
    <div className="option-group">
      <CheckboxField
        label="Run everything without asking (dangerous)"
        checked={bucket.force}
        hint="When on, Cursor runs commands without asking, except commands that its settings deny."
        onChange={(value) => {
          dispatch({ type: 'setCursorForce', value });
        }}
      />
      {chat !== undefined && (
        <details className="advanced advanced-stack">
          <summary>Advanced</summary>
          <TerminalUiField
            chat={chat}
            agent="cursor"
            checked={bucket.terminalUi}
            onChange={(value) => {
              dispatch({ type: 'setCursorTerminalUi', value });
            }}
          />
        </details>
      )}
    </div>
  );
}
