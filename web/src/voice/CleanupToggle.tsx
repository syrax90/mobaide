import { useId, useRef, useState } from 'react';
import { Modal } from '../components/Modal';
import { useVoicePrefsStore } from './voicePrefsStore';

/** Android `VoiceCleanupCopy` and iOS `VoiceCleanupCopy` hold the same text. */
export const CLEANUP_TOGGLE_LABEL =
  'Clean up dictated text with the session’s AI agent: remove filler words, fix punctuation and technical terms';

export function cleanupNoticeText(agentName: string): string {
  return (
    `The session’s AI agent on ${agentName} cleans up each transcript with its cheapest ` +
    'model, for example Claude Code with Claude Haiku. It runs under your own account: the ' +
    'text goes to that agent’s provider and counts toward your usage. Mobaide doesn’t store it.'
  );
}

interface CleanupToggleProps {
  agentName: string;
  checked: boolean;
  /** `true` comes only after the user pressed "Got it" on the notice, once per browser. */
  onChange: (on: boolean) => void;
  className: string;
  disabled?: boolean;
  /** The agent refused or missed the last change; the box shows its old value again. */
  saveFailed?: boolean;
}

/**
 * The transcript cleanup checkbox. The first time it is turned on in this
 * browser, a notice says where the text goes, and the setting changes only
 * after "Got it".
 */
export function CleanupToggle({
  agentName,
  checked,
  onChange,
  className,
  disabled = false,
  saveFailed = false,
}: CleanupToggleProps) {
  const noticeSeen = useVoicePrefsStore((state) => state.cleanupNoticeSeen);
  const acknowledge = useVoicePrefsStore((state) => state.acknowledgeCleanupNotice);
  const [noticeOpen, setNoticeOpen] = useState(false);
  const titleId = useId();
  const confirmRef = useRef<HTMLButtonElement>(null);

  // A wrapper, so the notice's <dialog> is never a direct child of another
  // `.dialog`, whose `> * + *` margin would undo its centering.
  return (
    <div>
      <label className={className}>
        <input
          type="checkbox"
          checked={checked}
          disabled={disabled}
          onChange={(event) => {
            if (!event.target.checked) onChange(false);
            else if (noticeSeen) onChange(true);
            else setNoticeOpen(true);
          }}
        />
        {CLEANUP_TOGGLE_LABEL}
      </label>
      {saveFailed && (
        <p className="voice-error" role="alert">
          {`Couldn’t save this setting on ${agentName}. Try again.`}
        </p>
      )}
      {noticeOpen && (
        <Modal
          labelledBy={titleId}
          initialFocus={confirmRef}
          onClose={() => {
            setNoticeOpen(false);
          }}
        >
          <h2 id={titleId}>Clean up dictated text</h2>
          <p>{cleanupNoticeText(agentName)}</p>
          <div className="button-row">
            <button
              ref={confirmRef}
              type="button"
              className="button button-primary"
              onClick={() => {
                setNoticeOpen(false);
                acknowledge();
                onChange(true);
              }}
            >
              Got it
            </button>
            <button
              type="button"
              className="button"
              onClick={() => {
                setNoticeOpen(false);
              }}
            >
              Cancel
            </button>
          </div>
        </Modal>
      )}
    </div>
  );
}
