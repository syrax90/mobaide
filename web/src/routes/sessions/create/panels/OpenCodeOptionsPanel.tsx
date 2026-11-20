import { CheckboxField } from '../../../../components/form/CheckboxField';
import type { AgentBucketTarget, OpenCodeBucket, WizardAction } from '../wizardState';
import { ScanAskHint } from './ScanAskHint';

export const OPENCODE_CODE_MODE_HINT =
  'OpenCode 2 calls MCP tools from JavaScript, several in one step. ' +
  'Turn it off to have the model call each tool directly.';

interface OpenCodeOptionsPanelProps {
  bucket: OpenCodeBucket;
  dispatch: (action: WizardAction) => void;
  /** Auto Scan edits its own copy of the options. */
  target?: AgentBucketTarget;
}

/**
 * OpenCode has no model or agent option at launch: both are picked inside the
 * session (the chat's chips, or the terminal UI's own lists). Code Mode starts
 * on, as in OpenCode itself.
 */
export function OpenCodeOptionsPanel({ bucket, dispatch, target }: OpenCodeOptionsPanelProps) {
  return (
    <div className="option-group">
      <CheckboxField
        label="Auto-approve permissions (dangerous)"
        checked={bucket.autoApprove}
        hint="OpenCode accepts every request that your rules do not deny."
        onChange={(value) => {
          dispatch({ type: 'setOpenCodeAutoApprove', value, target });
        }}
      />
      <CheckboxField
        label="Code Mode"
        checked={bucket.codeMode}
        hint={OPENCODE_CODE_MODE_HINT}
        onChange={(value) => {
          dispatch({ type: 'setOpenCodeCodeMode', value, target });
        }}
      />
      <ScanAskHint show={target === 'autoScan' && !bucket.autoApprove} />
    </div>
  );
}
