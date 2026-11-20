import { CheckboxField } from '../../../../components/form/CheckboxField';
import type { PiBucket, WizardAction } from '../wizardState';

interface PiOptionsPanelProps {
  bucket: PiBucket;
  dispatch: (action: WizardAction) => void;
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
export function PiOptionsPanel({ bucket, dispatch }: PiOptionsPanelProps) {
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
    </div>
  );
}
