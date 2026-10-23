import { useEffect, useId, useRef, useState, type ReactNode } from 'react';
import { Modal } from '../../../components/Modal';
import type { DictationState, InterruptReason } from '../../../voice/dictationController';
import { VOICE_CODE } from '../../../voice/voiceApi';
import { dictationErrorCopy } from '../../../voice/voiceErrorCopy';
import { joinTranscript } from '../../../voice/useDictation';
import { formatDuration } from '../../../voice/voiceStatusView';
import { MicIcon } from './VoiceIcons';

/** Claude Code shows a paste over 800 characters as `[Pasted text #n]`. */
const PASTE_COLLAPSE_CHARS = 800;

interface DictationDialogProps {
  /** `idle` only while `carried` holds text: the review is showing it again. */
  state: DictationState;
  /** The review's text kept while the user dictates more; the next result is appended. */
  carried: string | null;
  agentName: string;
  canSend: boolean;
  /** False once the session has ended: a new recording could never be inserted. */
  canRecord: boolean;
  onStop: () => void;
  onCancel: () => void;
  onRetry: () => void;
  onRecordAgain: () => void;
  /** Leaves an empty result or a failure (back to the review if text is carried). */
  onDismiss: () => void;
  /** Records more from the review, keeping its current text. */
  onContinue: (draft: string) => void;
  /** Throws the review's text away. */
  onDiscard: () => void;
  /** Returns false when the terminal refused the text. */
  onInsert: (text: string) => boolean;
  skipReview: boolean;
  onSkipReviewChange: (skip: boolean) => void;
}

function interruptionNote(reason: InterruptReason | null): string | null {
  switch (reason) {
    case 'background':
      return 'Recording stopped because the tab was hidden.';
    case 'device':
      return 'Recording stopped because the microphone went away.';
    case 'limit':
      return 'Recording stopped at the time limit.';
    default:
      return null;
  }
}

const PERMISSION_HELP =
  'Allow the microphone for this site, then try again. Chrome and Edge: the site ' +
  'settings icon left of the address bar. Firefox: the microphone icon in the ' +
  'address bar. Safari: Safari ▸ Settings for This Website.';

/**
 * One dictation, start to finish, as a top-layer dialog: recording (timer,
 * level, countdown near the limit), transcribing, then an editable review
 * field. **Insert** pastes the text into the session the way Templates does
 * — never with Enter — so the user always sends it themselves.
 *
 * The review's mic records again; while it has text the new transcript is
 * appended, and a cancel, an empty result or a failure returns to it.
 *
 * Esc cancels a recording or a transcription, and discards a result.
 */
export function DictationDialog({
  state,
  carried,
  agentName,
  canSend,
  canRecord,
  onStop,
  onCancel,
  onRetry,
  onRecordAgain,
  onDismiss,
  onContinue,
  onDiscard,
  onInsert,
  skipReview,
  onSkipReviewChange,
}: DictationDialogProps) {
  const titleId = useId();
  const [draft, setDraft] = useState(() => {
    if (state.kind !== 'result') return carried ?? '';
    return carried === null ? state.text : joinTranscript(carried, state.text);
  });
  const [insertRefused, setInsertRefused] = useState(false);
  const resultText = state.kind === 'result' ? state.text : null;
  // Read when a result lands, not a dependency: `carried` is set while the
  // previous result is still showing, which must not re-append it.
  const carriedRef = useRef(carried);
  carriedRef.current = carried;

  useEffect(() => {
    if (resultText !== null) {
      const before = carriedRef.current;
      setDraft(before === null ? resultText : joinTranscript(before, resultText));
      setInsertRefused(false);
    }
  }, [resultText]);

  const live = state.kind === 'starting' || state.kind === 'recording' || state.kind === 'transcribing';

  let title: string;
  let body: ReactNode;
  let actions: ReactNode;
  switch (state.kind) {
    case 'starting':
      title = 'Dictation';
      body = <p role="status">Opening the microphone…</p>;
      actions = (
        <button type="button" className="button" onClick={onCancel}>
          Cancel
        </button>
      );
      break;
    case 'recording': {
      const levelPercent = Math.round(state.level * 100);
      title = 'Recording';
      body = (
        <>
          <p className="voice-timer" aria-hidden="true">
            <span className="voice-recording-dot" />
            {formatDuration(state.elapsedMs)}
          </p>
          <div
            className="voice-meter"
            role="meter"
            aria-label="Microphone level"
            aria-valuemin={0}
            aria-valuemax={100}
            aria-valuenow={levelPercent}
          >
            <span style={{ width: `${String(levelPercent)}%` }} />
          </div>
          {state.remainingSeconds !== null && (
            <p className="voice-countdown">Stops in {state.remainingSeconds} s</p>
          )}
          {state.lagging && (
            <p className="page-note">The connection is slow. Recording continues.</p>
          )}
        </>
      );
      actions = (
        <>
          <button type="button" className="button" onClick={onCancel}>
            Cancel
          </button>
          {/* Focused when recording starts: one key press stops it. */}
          <button type="button" className="button button-primary" autoFocus onClick={onStop}>
            Stop
          </button>
        </>
      );
      break;
    }
    case 'transcribing': {
      const note = interruptionNote(state.interruptedBy);
      title = 'Transcribing';
      body = (
        <>
          <p role="status">Transcribing on {agentName}…</p>
          {note !== null && <p className="page-note">{note}</p>}
          {state.lagging && <p className="page-note">Waiting for the connection…</p>}
        </>
      );
      actions = (
        <button type="button" className="button" onClick={onCancel}>
          Cancel
        </button>
      );
      break;
    }
    case 'idle':
    case 'result': {
      const more = draft.trim() !== '';
      const micLabel = more ? 'Dictate more: adds to the text' : 'Dictate again';
      title = 'Review';
      body = (
        <>
          <label className="voice-review-label" htmlFor={`${titleId}-text`}>
            Edit the text, then insert it at the session&rsquo;s prompt. Nothing is sent until you
            press Enter there.
          </label>
          <textarea
            id={`${titleId}-text`}
            className="field-input field-textarea"
            rows={5}
            value={draft}
            autoFocus
            onChange={(event) => {
              setDraft(event.target.value);
            }}
          />
          {(draft.length > PASTE_COLLAPSE_CHARS || draft.includes('\n')) && (
            <p className="page-note">
              Claude Code may show long text as [Pasted text]; it still receives all of it.
            </p>
          )}
          {(!canSend || insertRefused) && (
            <p className="voice-error" role="alert">
              The session isn&rsquo;t accepting input right now.
            </p>
          )}
          <label className="voice-review-skip">
            <input
              type="checkbox"
              checked={skipReview}
              onChange={(event) => {
                onSkipReviewChange(event.target.checked);
              }}
            />
            Insert dictated text directly from now on, without this review
          </label>
          {skipReview && (
            <p className="page-note">
              You can turn the review back on in {agentName}&rsquo;s Voice input settings.
            </p>
          )}
        </>
      );
      actions = (
        <>
          <button
            type="button"
            className="button voice-dictate-more"
            aria-label={micLabel}
            title={micLabel}
            disabled={!canRecord}
            onClick={() => {
              onContinue(draft);
            }}
          >
            <MicIcon />
          </button>
          <button type="button" className="button" onClick={onDiscard}>
            Discard
          </button>
          <button
            type="button"
            className="button button-primary"
            disabled={!canSend || draft.trim() === ''}
            onClick={() => {
              if (!onInsert(draft)) setInsertRefused(true);
            }}
          >
            Insert
          </button>
        </>
      );
      break;
    }
    case 'empty':
      title = 'Nothing heard';
      body = <p role="status">No speech was recognised. Check the microphone and try again.</p>;
      actions = (
        <>
          <button type="button" className="button" onClick={onDismiss}>
            Close
          </button>
          <button
            type="button"
            className="button button-primary"
            disabled={!canRecord}
            onClick={onRecordAgain}
          >
            Record again
          </button>
        </>
      );
      break;
    case 'failed':
      title = 'Dictation failed';
      body = (
        <>
          <p role="alert">{dictationErrorCopy(state.code, agentName)}</p>
          {state.code === VOICE_CODE.micPermissionDenied && <p className="page-note">{PERMISSION_HELP}</p>}
          {state.message !== null && state.message !== '' && (
            <details className="voice-details">
              <summary>Details</summary>
              <pre>{state.message}</pre>
            </details>
          )}
        </>
      );
      actions = (
        <>
          <button type="button" className="button" onClick={onDismiss}>
            Close
          </button>
          {state.retryable && state.hasAudio ? (
            <button type="button" className="button button-primary" onClick={onRetry}>
              Retry
            </button>
          ) : (
            <button
              type="button"
              className="button button-primary"
              disabled={!canRecord}
              onClick={onRecordAgain}
            >
              Record again
            </button>
          )}
        </>
      );
      break;
  }

  return (
    <Modal
      labelledBy={titleId}
      onClose={live ? onCancel : state.kind === 'result' || state.kind === 'idle' ? onDiscard : onDismiss}
      className="voice-dialog"
    >
      <h2 id={titleId}>{title}</h2>
      {/* Announces the transitions a screen reader would otherwise miss. */}
      <p className="visually-hidden" aria-live="polite">
        {state.kind === 'recording'
          ? 'Recording'
          : state.kind === 'transcribing'
            ? 'Recording stopped. Transcribing'
            : ''}
      </p>
      {body}
      <div className="button-row voice-dialog-footer">{actions}</div>
    </Modal>
  );
}
