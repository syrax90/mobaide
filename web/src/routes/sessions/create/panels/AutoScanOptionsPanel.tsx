import { CheckboxField } from '../../../../components/form/CheckboxField';
import { TextAreaField } from '../../../../components/form/TextAreaField';
import type { ClaudeBucket, WizardAction } from '../wizardState';
import { ClaudeOptionsPanel } from './ClaudeOptionsPanel';

interface AutoScanOptionsPanelProps {
  bucket: ClaudeBucket;
  models: readonly string[] | null;
  dispatch: (action: WizardAction) => void;
}

/**
 * Auto Scan runs Claude against matching Linear issues. It shares the Claude
 * controls but never the Claude *values*, and it has no worktree section: the
 * scanner branches one per issue itself, so `worktree` is hardcoded true.
 */
export function AutoScanOptionsPanel({
  bucket,
  models,
  dispatch,
}: AutoScanOptionsPanelProps) {
  return (
    <>
      <p className="page-note">
        Each matching issue runs in its own git worktree, so its changes stay isolated from
        your working copy. Non-git projects run in the project directory.
      </p>

      <ClaudeOptionsPanel
        bucket={bucket}
        bucketId="autoScan"
        models={models}
        dispatch={dispatch}
        skipPermissionsHint="When off, the scan pauses at permission prompts — you'll get a notification and can open the session to approve."
      />

      <div className="option-group">
        <CheckboxField
          label="Plan mode"
          checked={bucket.planMode}
          onChange={(value) => {
            dispatch({ type: 'setClaudeFlag', bucket: 'autoScan', flag: 'planMode', value });
          }}
        />
        <TextAreaField
          label="Initial prompt (optional)"
          value={bucket.initialPrompt}
          rows={5}
          onChange={(value) => {
            dispatch({
              type: 'setClaudeText',
              bucket: 'autoScan',
              field: 'initialPrompt',
              value,
            });
          }}
        />
      </div>
    </>
  );
}
