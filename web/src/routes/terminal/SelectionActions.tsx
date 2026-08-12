import type { TerminalSessionModel } from '../../terminal/sessionModel';

interface SelectionActionsProps {
  model: TerminalSessionModel;
  canRun: boolean;
  canPaste: boolean;
}

/**
 * The selection toolbar, in the item order both mobile clients fixed:
 * Send to Input · Copy · Run.
 *
 * "Send to input" pastes the selection onto the program's input line; "Run" is
 * the desktop stand-in for mobile's tap-a-row-to-execute — the only producer
 * of the `x` execute frame. Their gates differ on purpose: Run needs only a
 * live socket (a stopped session's crashed command can be retried), while a
 * paste needs the process running to have an input line to land on.
 *
 * The synchronized-output guard deliberately lives in the model rather than in a
 * `disabled` prop here: xterm clears mode 2026 on a silent 1 s timer, so a
 * cached flag has a transition it can never see, and a button that turns
 * `disabled` between pointerdown and mouseup swallows the click outright.
 */
export function SelectionActions({ model, canRun, canPaste }: SelectionActionsProps) {
  return (
    <div className="terminal-selection-bar" role="toolbar" aria-label="Selection">
      <button
        type="button"
        className="button"
        disabled={!canPaste}
        onClick={() => {
          model.pasteSelection();
        }}
      >
        Send to input
      </button>
      <button
        type="button"
        className="button"
        onClick={() => {
          void model.copySelection();
        }}
      >
        Copy
      </button>
      <button
        type="button"
        className="button"
        disabled={!canRun}
        onClick={() => {
          model.runSelection();
        }}
      >
        Run
      </button>
    </div>
  );
}
