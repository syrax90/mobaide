import { CheckboxField } from '../../../../components/form/CheckboxField';
import type { AgentBucketTarget, CursorBucket, WizardAction } from '../wizardState';
import { ScanAskHint } from './ScanAskHint';

interface CursorOptionsPanelProps {
  bucket: CursorBucket;
  dispatch: (action: WizardAction) => void;
  /** Auto Scan edits its own copy of the options. */
  target?: AgentBucketTarget;
}

/**
 * Cursor has no model option at launch: the model, and its settings, are
 * picked inside the session.
 */
export function CursorOptionsPanel({ bucket, dispatch, target }: CursorOptionsPanelProps) {
  return (
    <div className="option-group">
      <CheckboxField
        label="Run everything without asking (dangerous)"
        checked={bucket.force}
        hint="When on, Cursor runs commands without asking, except commands that its settings deny."
        onChange={(value) => {
          dispatch({ type: 'setCursorForce', value, target });
        }}
      />
      <ScanAskHint show={target === 'autoScan' && !bucket.force} />
    </div>
  );
}
