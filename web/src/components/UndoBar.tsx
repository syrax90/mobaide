/**
 * The bar an open undo window shows — mobile's snackbar-with-action, which the
 * zero-dependency toast store has no concept of.
 *
 * `role="status"` rather than `alert`: the action it announces is reversible and
 * user-initiated, so interrupting a screen reader mid-sentence would be wrong.
 * Dismissing commits immediately, matching Android's `commitNow()`.
 */
interface UndoBarProps {
  message: string;
  onUndo: () => void;
  onDismiss: () => void;
}

export function UndoBar({ message, onUndo, onDismiss }: UndoBarProps) {
  return (
    <div className="undo-bar" role="status" aria-live="polite">
      <span className="undo-bar-message">{message}</span>
      <button type="button" className="undo-bar-action" onClick={onUndo}>
        Undo
      </button>
      <button
        type="button"
        className="undo-bar-dismiss"
        aria-label="Dismiss"
        onClick={onDismiss}
      >
        ×
      </button>
    </div>
  );
}
