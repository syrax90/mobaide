import { CheckboxField } from '../../../../components/form/CheckboxField';
import { TextAreaField } from '../../../../components/form/TextAreaField';
import type { ChatSupport } from '../../../../protocol/dto/agentVersion';
import type { ClaudeBucket, ClaudeBucketId, WizardAction } from '../wizardState';
import { chatBlocked } from './SessionViewField';

export const CLAUDE_CODE_MODE_HINT =
  'Experimental. Claude writes JavaScript that makes several tool calls in one step. ' +
  'Terminal only. Claude Code asks you to confirm until you accept; ' +
  'turn Code Mode off here to stop the question.';

export const NO_MEMORY_HINT = 'Claude Code does not read or write its auto memory. CLAUDE.md files still load.';

const DEBUG_TERMINAL_HINT = 'Claude Code prints verbose diagnostics into the terminal.';
export const DEBUG_CHAT_HINT = 'Claude Code writes verbose diagnostics to ~/.claude/debug/ on this PC.';

interface ClaudeAdvancedPanelProps {
  bucket: ClaudeBucket;
  bucketId: ClaudeBucketId;
  dispatch: (action: WizardAction) => void;
  /** Claude Code only: this PC's chat support, which hides Code Mode in the chat view. */
  chat?: ChatSupport;
  /**
   * Offer Code Mode without `chat`: Auto Scan, whose view is chosen apart from
   * this bucket; it passes false for the chat view, which hides Code Mode like Focus view.
   */
  codeMode?: boolean;
  /** Auto Scan only: whether its sessions open in the chat view. */
  chatView?: boolean;
}

/** Collapsed-by-default Claude flags, matching both mobile clients' "Advanced" expander. */
export function ClaudeAdvancedPanel({
  bucket,
  bucketId,
  dispatch,
  chat,
  codeMode = chat !== undefined,
  chatView,
}: ClaudeAdvancedPanelProps) {
  const terminal = chat === undefined || bucket.terminalUi || chatBlocked(chat);
  const inChat = chatView ?? !terminal;
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
          hint={NO_MEMORY_HINT}
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
          hint={inChat ? DEBUG_CHAT_HINT : DEBUG_TERMINAL_HINT}
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
