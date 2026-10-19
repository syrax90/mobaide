import { CheckboxField } from '../../../../components/form/CheckboxField';
import { TextAreaField } from '../../../../components/form/TextAreaField';
import type { ClaudeBucket, ClaudeBucketId, WizardAction } from '../wizardState';

interface ClaudeAdvancedPanelProps {
  bucket: ClaudeBucket;
  bucketId: ClaudeBucketId;
  dispatch: (action: WizardAction) => void;
}

/** Collapsed-by-default Claude flags, matching both mobile clients' "Advanced" expander. */
export function ClaudeAdvancedPanel({ bucket, bucketId, dispatch }: ClaudeAdvancedPanelProps) {
  return (
    <div className="option-group">
      <details className="advanced advanced-stack">
        <summary>Advanced</summary>
        <CheckboxField
          label="No memory"
          checked={bucket.noMemory}
          onChange={(value) => {
            dispatch({ type: 'setClaudeFlag', bucket: bucketId, flag: 'noMemory', value });
          }}
        />
        <CheckboxField
          label="Fast mode (Opus only)"
          checked={bucket.fastMode}
          onChange={(value) => {
            dispatch({ type: 'setClaudeFlag', bucket: bucketId, flag: 'fastMode', value });
          }}
        />
        <CheckboxField
          label="Ultracode"
          checked={bucket.ultracode}
          onChange={(value) => {
            dispatch({ type: 'setClaudeFlag', bucket: bucketId, flag: 'ultracode', value });
          }}
        />
        <CheckboxField
          label="Debug logging"
          checked={bucket.debug}
          hint="Claude Code prints verbose diagnostics into the terminal."
          onChange={(value) => {
            dispatch({ type: 'setClaudeFlag', bucket: bucketId, flag: 'debug', value });
          }}
        />
        <TextAreaField
          label="Append system prompt (optional)"
          value={bucket.appendSystemPrompt}
          onChange={(value) => {
            dispatch({
              type: 'setClaudeText',
              bucket: bucketId,
              field: 'appendSystemPrompt',
              value,
            });
          }}
        />
      </details>
    </div>
  );
}
