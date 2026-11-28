import { useRef } from 'react';
import { Modal } from '../../components/Modal';

interface ForkDialogProps {
  /** Up to one answer; false copies the whole conversation. */
  atAnswer: boolean;
  onConfirm: () => void;
  onCancel: () => void;
}

/** Confirms `fork`: a new chat with the conversation, this one unchanged. */
export function ForkDialog({ atAnswer, onConfirm, onCancel }: ForkDialogProps) {
  const cancelRef = useRef<HTMLButtonElement>(null);
  return (
    <Modal role="alertdialog" labelledBy="fork-title" initialFocus={cancelRef} onClose={onCancel}>
      <h2 id="fork-title">{atAnswer ? 'Fork from here?' : 'Fork this chat?'}</h2>
      <p>{forkMessage(atAnswer)}</p>
      <div className="button-row">
        <button type="button" className="button button-primary" onClick={onConfirm}>
          Fork
        </button>
        <button type="button" ref={cancelRef} className="button" onClick={onCancel}>
          Cancel
        </button>
      </div>
    </Modal>
  );
}

/** The same text on Android and iOS. */
function forkMessage(atAnswer: boolean): string {
  return atAnswer
    ? 'Start a new chat with the conversation up to this answer? This chat stays as it is. Files on your PC are not changed back.'
    : 'Start a new chat with a copy of this conversation? This chat stays as it is. Both chats work in the same folder.';
}
