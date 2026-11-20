export const PI_LAUNCH_NOTE =
  'Pi runs commands and edits files without asking. ' +
  'Pick the model and the thinking level inside the session.';

/**
 * Pi has no option to set at launch: it has no approval mode, and the model
 * and thinking level are picked inside the session.
 */
export function PiOptionsPanel() {
  return (
    <div className="option-group">
      <p className="field-hint">{PI_LAUNCH_NOTE}</p>
    </div>
  );
}
