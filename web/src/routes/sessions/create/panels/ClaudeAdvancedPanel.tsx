import { CheckboxField } from '../../../../components/form/CheckboxField';
import { TextAreaField } from '../../../../components/form/TextAreaField';
import type { ChatSupport } from '../../../../protocol/dto/agentVersion';
import type { ClaudeBucket, ClaudeBucketId, WizardAction } from '../wizardState';
import { chatBlocked } from './SessionViewField';

export const CLAUDE_CODE_MODE_HINT =
  'Experimental. Claude writes JavaScript that makes several tool calls in one step. ' +
  'Terminal only. Claude Code asks you to confirm until you accept; ' +
  'turn Code Mode off here to stop the question.';

interface ClaudeAdvancedPanelProps {
  bucket: ClaudeBucket;
  bucketId: ClaudeBucketId;
  dispatch: (action: WizardAction) => void;
  /** Claude Code only: this PC's chat support, which hides Debug logging in the chat view. */
  chat?: ChatSupport;
  /**
   * Offer Code Mode without `chat`: Auto Scan, whose view is chosen apart from
   * this bucket; it passes false for the chat view, which hides Code Mode like Focus view.
   */
  codeMode?: boolean;
}

/** Collapsed-by-default Claude flags, matching both mobile clients' "Advanced" expander. */
export function ClaudeAdvancedPanel({
  bucket,
  bucketId,
  dispatch,
  chat,
  codeMode = chat !== undefined,
}: ClaudeAdvancedPanelProps) {
  // Debug output lands in the terminal, so it is offered only when there is one.
  const terminal = chat === undefined || bucket.terminalUi || chatBlocked(chat);
  return (
    <div className="option-group">
      <details className="advanced advanced-stack">
        <summary>Advanced</summary>
        {/* Not for an older agent's Auto Scan, which cannot keep it. */}
        {codeMode && terminal && (
          <CheckboxField
            label="Code Mode"
            checked={bucket.codeMode}
            hint={CLAUDE_CODE_MODE_HINT}
            onChange={(value) => {
              dispatch({ type: 'setClaudeFlag', bucket: bucketId, flag: 'codeMode', value });
            }}
          />
        )}
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
        {terminal && (
          <CheckboxField
            label="Debug logging"
            checked={bucket.debug}
            hint="Claude Code prints verbose diagnostics into the terminal."
            onChange={(value) => {
              dispatch({ type: 'setClaudeFlag', bucket: bucketId, flag: 'debug', value });
            }}
          />
        )}
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
