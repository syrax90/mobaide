import { CheckboxField } from '../../../../components/form/CheckboxField';
import type { AgentBucketTarget, CopilotBucket, WizardAction } from '../wizardState';
import { ScanAskHint } from './ScanAskHint';

interface CopilotOptionsPanelProps {
  bucket: CopilotBucket;
  dispatch: (action: WizardAction) => void;
  /** Auto Scan edits its own copy of the options. */
  target?: AgentBucketTarget;
}

/**
 * Copilot has no model option at launch: the models depend on the account's
 * plan, and the model is picked inside the session.
 */
export function CopilotOptionsPanel({ bucket, dispatch, target }: CopilotOptionsPanelProps) {
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
    </div>
  );
}
