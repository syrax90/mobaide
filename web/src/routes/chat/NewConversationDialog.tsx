import { useRef } from 'react';
import { Modal } from '../../components/Modal';

interface NewConversationDialogProps {
  /** The agent's display name, e.g. "Claude Code". */
  kindLabel: string;
  /** A turn runs now: starting over stops it. */
  running: boolean;
  /** Messages wait in the queue: starting over removes them. */
  queued: boolean;
  onConfirm: () => void;
  onCancel: () => void;
}

/**
 * Confirms `new_conversation` when it stops a turn or drops queued messages.
 * The chat empties; the agent keeps the old one.
 */
export function NewConversationDialog({
  kindLabel,
  running,
  queued,
  onConfirm,
  onCancel,
}: NewConversationDialogProps) {
  const cancelRef = useRef<HTMLButtonElement>(null);
  return (
    <Modal
      role="alertdialog"
      labelledBy="new-conversation-title"
      initialFocus={cancelRef}
      onClose={onCancel}
    >
      <h2 id="new-conversation-title">Start a new conversation?</h2>
      <p>{newConversationMessage(kindLabel, running, queued)}</p>
      <div className="button-row">
        <button type="button" className="button button-primary" onClick={onConfirm}>
          New conversation
        </button>
        <button type="button" ref={cancelRef} className="button" onClick={onCancel}>
          Cancel
        </button>
      </div>
    </Modal>
  );
}

function newConversationMessage(kindLabel: string, running: boolean, queued: boolean): string {
  const stop = running ? 'The running turn stops. ' : '';
  const dropped = queued ? 'Queued messages are removed. ' : '';
  const agent = kindLabel.charAt(0).toUpperCase() + kindLabel.slice(1);
  return (
    `${stop}${dropped}The chat starts empty, with the same folder and settings. ` +
    `If ${agent} saved this conversation, open it again with Continue a session.`
  );
}
