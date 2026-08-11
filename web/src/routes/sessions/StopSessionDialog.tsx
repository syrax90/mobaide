import { useRef } from 'react';
import { Modal } from '../../components/Modal';
import type { SessionResponse } from '../../protocol/dto/session';

interface StopSessionDialogProps {
  session: SessionResponse;
  busy: boolean;
  onStop: (force: boolean) => void;
  onCancel: () => void;
}

/**
 * iOS's confirm-with-Force-Stop sheet. Android kills unconfirmed, which is a
 * worse trade on a desktop-sized click target than on a phone.
 */
export function StopSessionDialog({
  session,
  busy,
  onStop,
  onCancel,
}: StopSessionDialogProps) {
  const cancelRef = useRef<HTMLButtonElement>(null);
  return (
    <Modal
      role="alertdialog"
      labelledBy="stop-session-title"
      dismissible={!busy}
      initialFocus={cancelRef}
      onClose={onCancel}
    >
      <h2 id="stop-session-title">Stop session?</h2>
      <p>
        Stop {session.title !== undefined ? `"${session.title}"` : session.session_id}? Force
        Stop kills the process immediately.
      </p>
      <div className="button-row">
        <button
          type="button"
          className="button button-danger"
          disabled={busy}
          onClick={() => {
            onStop(false);
          }}
        >
          Stop
        </button>
        <button
          type="button"
          className="button button-danger"
          disabled={busy}
          onClick={() => {
            onStop(true);
          }}
        >
          Force Stop
        </button>
        <button type="button" ref={cancelRef} className="button" onClick={onCancel}>
          Cancel
        </button>
      </div>
    </Modal>
  );
}
