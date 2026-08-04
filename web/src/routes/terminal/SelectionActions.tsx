import type { TerminalSessionModel } from '../../terminal/sessionModel';

interface SelectionActionsProps {
  model: TerminalSessionModel;
  canRun: boolean;
}

/**
 * The selection toolbar, in the item order both mobile clients fixed:
 * Send to Input · Copy · Run.
 *
 * "Run" is the desktop stand-in for mobile's tap-a-row-to-execute — the only
 * producer of the `x` execute frame — and is gated on the socket being live
 * rather than on the process running, exactly like the row tap it replaces.
 *
 * The synchronized-output guard deliberately lives in the model rather than in a
 * `disabled` prop here: xterm clears mode 2026 on a silent 1 s timer, so a
 * cached flag has a transition it can never see, and a button that turns
 * `disabled` between pointerdown and mouseup swallows the click outright.
 */
export function SelectionActions({ model, canRun }: SelectionActionsProps) {
  return (
    <div className="terminal-selection-bar" role="toolbar" aria-label="Selection">
      <button
        type="button"
        className="button"
        onClick={() => {
          model.sendSelectionToComposer();
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
