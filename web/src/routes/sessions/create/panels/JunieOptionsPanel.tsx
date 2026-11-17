import { CheckboxField } from '../../../../components/form/CheckboxField';
import type { ChatSupport } from '../../../../protocol/dto/agentVersion';
import type { JunieBucket, WizardAction } from '../wizardState';
import { TerminalUiField } from './TerminalUiField';

interface JunieOptionsPanelProps {
  bucket: JunieBucket;
  dispatch: (action: WizardAction) => void;
  /** Shows the Terminal UI toggle, gated on this PC's chat support for Junie. */
  chat?: ChatSupport;
}

/**
 * Junie has no model option at launch: the model and its effort are picked
 * inside the session.
 */
export function JunieOptionsPanel({ bucket, dispatch, chat }: JunieOptionsPanelProps) {
  return (
    <div className="option-group">
      <CheckboxField
        label="Start in Plan mode"
        checked={bucket.planMode}
        hint="Junie prepares a plan and asks you to approve it before it changes your files."
        onChange={(value) => {
          dispatch({ type: 'setJuniePlanMode', value });
        }}
      />
      <CheckboxField
        label="Brave mode (dangerous)"
        checked={bucket.brave}
        hint={
          bucket.brave
            ? 'Junie runs all commands without asking, in this session only.'
            : 'Junie uses its own Brave setting. By default it asks before commands that are not safe.'
        }
        onChange={(value) => {
          dispatch({ type: 'setJunieBrave', value });
        }}
      />
      {chat !== undefined && (
        <details className="advanced advanced-stack">
          <summary>Advanced</summary>
          <TerminalUiField
            chat={chat}
            agent="junie"
            checked={bucket.terminalUi}
            onChange={(value) => {
              dispatch({ type: 'setJunieTerminalUi', value });
            }}
          />
        </details>
      )}
    </div>
  );
}
