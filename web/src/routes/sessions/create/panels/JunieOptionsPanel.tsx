import { CheckboxField } from '../../../../components/form/CheckboxField';
import type { AgentBucketTarget, JunieBucket, WizardAction } from '../wizardState';
import { ScanAskHint } from './ScanAskHint';

interface JunieOptionsPanelProps {
  bucket: JunieBucket;
  dispatch: (action: WizardAction) => void;
  /** Auto Scan edits its own copy of the options. */
  target?: AgentBucketTarget;
}

/**
 * Junie has no model option at launch: the model and its effort are picked
 * inside the session.
 */
export function JunieOptionsPanel({ bucket, dispatch, target }: JunieOptionsPanelProps) {
  return (
    <div className="option-group">
      <CheckboxField
        label="Start in Plan mode"
        checked={bucket.planMode}
        hint="Junie prepares a plan and asks you to approve it before it changes your files."
        onChange={(value) => {
          dispatch({ type: 'setJuniePlanMode', value, target });
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
          dispatch({ type: 'setJunieBrave', value, target });
        }}
      />
      <ScanAskHint show={target === 'autoScan' && !bucket.brave} />
    </div>
  );
}
