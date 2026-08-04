import { useId } from 'react';
import type { TerminalSessionModel } from '../../terminal/sessionModel';

interface ComposerProps {
  model: TerminalSessionModel;
  value: string;
  canSend: boolean;
  onOpenTemplates: () => void;
}

/**
 * The queued-message composer — the `m` submit frame's only producer.
 *
 * Submit is not "type into the terminal": the agent clears the prompt line
 * (`Ctrl+A Ctrl+K`), writes the body, pauses, then writes the CR as a separate
 * PTY write, so an Ink TUI sees a discrete Enter. That is why a long prompt
 * belongs here rather than in the grid, and why Enter sends while Shift+Enter
 * inserts a newline.
 */
export function Composer({ model, value, canSend, onOpenTemplates }: ComposerProps) {
  const fieldId = useId();
  const ready = canSend && value.trim().length > 0;

  return (
    <form
      className="terminal-composer"
      onSubmit={(event) => {
        event.preventDefault();
        model.submitComposer();
      }}
    >
      <label className="field-label" htmlFor={fieldId}>
        Message
      </label>
      <textarea
        id={fieldId}
        className="field-input terminal-composer-input"
        rows={2}
        value={value}
        placeholder="Type a message, then press Enter to send"
        onChange={(event) => {
          model.setComposer(event.target.value);
        }}
        onKeyDown={(event) => {
          if (event.key !== 'Enter' || event.shiftKey) return;
          // The Enter that confirms an IME composition must reach the IME, not
          // this handler: stealing it leaves the composition uncommitted and
          // submits the raw composition buffer. `keyCode === 229` is the
          // pre-`isComposing` signal Safari and older engines still use.
          if (event.nativeEvent.isComposing || event.keyCode === 229) return;
          event.preventDefault();
          model.submitComposer();
        }}
      />
      <div className="terminal-composer-actions">
        <button type="button" className="button" onClick={onOpenTemplates}>
          Templates
        </button>
        <button type="submit" className="button button-primary" disabled={!ready}>
          Send
        </button>
      </div>
    </form>
  );
}
