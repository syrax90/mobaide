import { CheckboxField } from '../../../../components/form/CheckboxField';
import { TextAreaField } from '../../../../components/form/TextAreaField';
import type { ChatSupport } from '../../../../protocol/dto/agentVersion';
import type { ClaudeBucket, ClaudeBucketId, WizardAction } from '../wizardState';
import { chatBlocked, TerminalUiField } from './TerminalUiField';

interface ClaudeAdvancedPanelProps {
  bucket: ClaudeBucket;
  bucketId: ClaudeBucketId;
  dispatch: (action: WizardAction) => void;
  /** Claude Code only: shows the Terminal UI toggle, gated on this PC's chat support. */
  chat?: ChatSupport;
}

/** Collapsed-by-default Claude flags, matching both mobile clients' "Advanced" expander. */
export function ClaudeAdvancedPanel({ bucket, bucketId, dispatch, chat }: ClaudeAdvancedPanelProps) {
  // Debug output lands in the terminal, so it is offered only when there is one.
  const terminal = chat === undefined || bucket.terminalUi || chatBlocked(chat);
  return (
    <div className="option-group">
      <details className="advanced advanced-stack">
        <summary>Advanced</summary>
        {chat !== undefined && (
          <TerminalUiField
            chat={chat}
            agent="claude"
            checked={bucket.terminalUi}
            onChange={(value) => {
              dispatch({ type: 'setClaudeFlag', bucket: bucketId, flag: 'terminalUi', value });
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
