import { useCallback, useEffect, useId, useLayoutEffect, useRef, useState, type ReactNode } from 'react';
import { Modal } from '../../../components/Modal';
import { CleanupToggle } from '../../../voice/CleanupToggle';
import type {
  CleanupError,
  CleanupOutcome,
  DictationState,
  InterruptReason,
} from '../../../voice/dictationController';
import { VOICE_CODE } from '../../../voice/voiceApi';
import { cleanupErrorCopy, dictationErrorCopy } from '../../../voice/voiceErrorCopy';
import { spliceTranscript, type CarriedText } from '../../../voice/useDictation';
import { formatDuration } from '../../../voice/voiceStatusView';
import { MicIcon } from './VoiceIcons';

/** Claude Code shows a paste over 800 characters as `[Pasted text #n]`. */
const PASTE_COLLAPSE_CHARS = 800;

interface DictationDialogProps {
  /** `idle` only while `carried` holds text: the review is showing it again. */
  state: DictationState;
  /** The review's text around its cursor while the user dictates more; the next result goes in between. */
  carried: CarriedText | null;
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
  /** Records more from the review, keeping its text either side of the cursor. */
  onContinue: (text: CarriedText) => void;
  /** Throws the review's text away. */
  onDiscard: () => void;
  /** Returns false when the terminal refused the text. */
  onInsert: (text: string) => boolean;
  skipReview: boolean;
  onSkipReviewChange: (skip: boolean) => void;
  /** The agent can clean up transcripts in this session. */
  cleanupAvailable: boolean;
  cleanUp: boolean;
  /** The agent did not store the last change of `cleanUp`. */
  cleanUpSaveFailed?: boolean;
  onCleanUpChange: (cleanUp: boolean) => void;
  /** Cleans up the review's text, when cleanup is turned on from the review. */
  onCleanUpText: (text: string, abort: AbortController) => Promise<CleanupOutcome>;
}

/** The review's own cleanup, run when the toggle is turned on there. */
type ReviewCleanup =
  | { kind: 'running' }
  | { kind: 'cleaned'; original: string; cleaned: string }
  | { kind: 'unchanged' }
  | { kind: 'failed'; error: CleanupError };

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
 * The review's mic records again; the new transcript goes in at the text's
 * cursor, and a cancel, an empty result or a failure returns to the review.
 *
 * With cleanup on, the session's AI agent cleans up the transcript while the
 * dialog still reads "Transcribing"; the review can swap back to the
 * transcript as heard, and a failed cleanup shows it as heard. Turning
 * cleanup on from the review cleans up the text already in it.
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
  cleanupAvailable,
  cleanUp,
  cleanUpSaveFailed = false,
  onCleanUpChange,
  onCleanUpText,
}: DictationDialogProps) {
  const titleId = useId();
  const [draft, setDraft] = useState(() => {
    if (state.kind !== 'result') return carried === null ? '' : carried.before + carried.after;
    return carried === null ? state.text : spliceTranscript(carried, state.text).text;
  });
  const [insertRefused, setInsertRefused] = useState(false);
  const [showingOriginal, setShowingOriginal] = useState(false);
  const [reviewCleanup, setReviewCleanup] = useState<ReviewCleanup | null>(null);
  const reviewCleanupAbortRef = useRef<AbortController | null>(null);
  const resultText = state.kind === 'result' ? state.text : null;
  const textRef = useRef<HTMLTextAreaElement>(null);
  // Where the cursor goes once a spliced transcript is in the field.
  const caretRef = useRef<number | null>(null);
  // The draft of the version not showing (cleaned up or as heard), edits
  // included, so swapping back and forth never loses them.
  const otherDraftRef = useRef<string | null>(null);
  // Read when a result lands, not a dependency: `carried` is set while the
  // previous result is still showing, which must not re-insert it.
  const carriedRef = useRef(carried);
  carriedRef.current = carried;

  const placeTranscript = useCallback((transcript: string) => {
    const around = carriedRef.current;
    if (around === null) {
      setDraft(transcript);
    } else {
      const spliced = spliceTranscript(around, transcript);
      caretRef.current = spliced.caret;
      setDraft(spliced.text);
    }
  }, []);

  useEffect(() => {
    if (resultText !== null) {
      placeTranscript(resultText);
      otherDraftRef.current = null;
      setShowingOriginal(false);
      setInsertRefused(false);
      setReviewCleanup(null);
    }
  }, [resultText, placeTranscript]);

  // Closing the dialog drops a cleanup of the review's text.
  useEffect(
    () => () => {
      const abort = reviewCleanupAbortRef.current;
      reviewCleanupAbortRef.current = null;
      abort?.abort();
    },
    [],
  );

  const cleanUpReview = (text: string) => {
    const abort = new AbortController();
    reviewCleanupAbortRef.current = abort;
    setReviewCleanup({ kind: 'running' });
    void onCleanUpText(text, abort).then((outcome) => {
      if (reviewCleanupAbortRef.current !== abort) return;
      reviewCleanupAbortRef.current = null;
      switch (outcome.kind) {
        case 'cleaned':
          setDraft(outcome.text);
          otherDraftRef.current = null;
          setShowingOriginal(false);
          setReviewCleanup({ kind: 'cleaned', original: text, cleaned: outcome.text });
          break;
        case 'unchanged':
          setReviewCleanup({ kind: 'unchanged' });
          break;
        case 'failed':
          setReviewCleanup({ kind: 'failed', error: outcome.error });
          break;
      }
    });
  };

  useLayoutEffect(() => {
    const caret = caretRef.current;
    const field = textRef.current;
    if (caret === null || field === null || field.value !== draft) return;
    caretRef.current = null;
    field.setSelectionRange(caret, caret);
  }, [draft]);

  const live =
    state.kind === 'starting' ||
    state.kind === 'recording' ||
    state.kind === 'transcribing' ||
    state.kind === 'cleaning';

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
    case 'cleaning':
      // Part of transcribing to the user: the same dialog, one line changed.
      title = 'Transcribing';
      body = <p role="status">Cleaning up the text…</p>;
      actions = (
        <button type="button" className="button" onClick={onCancel}>
          Cancel
        </button>
      );
      break;
    case 'idle':
    case 'result': {
      const more = draft.trim() !== '';
      const reviewCleaning = reviewCleanup?.kind === 'running';
      // The cleaned-up version and the text as heard, as whole review texts
      // when the review's own cleanup made them; else from the result.
      const reviewVersions = reviewCleanup?.kind === 'cleaned' ? reviewCleanup : null;
      const original =
        reviewVersions !== null
          ? reviewVersions.original
          : reviewCleanup === null && state.kind === 'result'
            ? (state.original ?? null)
            : null;
      const failedCleanup =
        reviewCleanup?.kind === 'failed'
          ? reviewCleanup.error
          : reviewCleanup === null && state.kind === 'result'
            ? (state.cleanupError ?? null)
            : null;
      const micLabel = more ? 'Dictate more at the cursor' : 'Dictate again';
      title = 'Review';
      body = (
        <>
          <label className="voice-review-label" htmlFor={`${titleId}-text`}>
            Edit the text, then insert it at the session&rsquo;s prompt. Nothing is sent until you
            press Enter there.
          </label>
          <textarea
            ref={textRef}
            id={`${titleId}-text`}
            className="field-input field-textarea"
            rows={5}
            value={draft}
            readOnly={reviewCleaning}
            autoFocus
            onChange={(event) => {
              setDraft(event.target.value);
            }}
          />
          {reviewCleaning && <p role="status">Cleaning up the text…</p>}
          {original !== null && (reviewVersions !== null || resultText !== null) && (
            <p className="page-note">
              {showingOriginal
                ? 'Showing the text as heard.'
                : 'Cleaned up by the session’s AI agent.'}{' '}
              <button
                type="button"
                className="link-button"
                onClick={() => {
                  const other = otherDraftRef.current;
                  otherDraftRef.current = draft;
                  if (other !== null) setDraft(other);
                  else if (reviewVersions !== null) {
                    setDraft(showingOriginal ? reviewVersions.cleaned : reviewVersions.original);
                  } else if (resultText !== null) {
                    placeTranscript(showingOriginal ? resultText : original);
                  }
                  setShowingOriginal(!showingOriginal);
                }}
              >
                {showingOriginal ? 'Show cleaned up' : 'Show original'}
              </button>
            </p>
          )}
          {failedCleanup !== null && (
            <>
              <p className="page-note" role="status">
                Couldn&rsquo;t clean up the text, so it&rsquo;s shown as heard.{' '}
                {cleanupErrorCopy(failedCleanup.code, agentName)}
              </p>
              {failedCleanup.message !== null && failedCleanup.message !== '' && (
                <details className="voice-details">
                  <summary>Details</summary>
                  <pre>{failedCleanup.message}</pre>
                </details>
              )}
            </>
          )}
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
          {cleanupAvailable && (
            <CleanupToggle
              className="voice-review-skip"
              agentName={agentName}
              checked={cleanUp}
              saveFailed={cleanUpSaveFailed}
              disabled={reviewCleaning}
              onChange={(on) => {
                onCleanUpChange(on);
                // The text in the review has not been cleaned up yet: do it
                // now, not only from the next dictation on.
                if (on && original === null && draft.trim() !== '') cleanUpReview(draft);
              }}
            />
          )}
          <label className="voice-review-skip">
            <input
              type="checkbox"
              disabled={reviewCleaning}
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
            disabled={!canRecord || reviewCleaning}
            onClick={() => {
              // The field keeps its selection after losing focus to this button.
              const field = textRef.current;
              const start = field?.selectionStart ?? draft.length;
              const end = field?.selectionEnd ?? draft.length;
              onContinue({ before: draft.slice(0, start), after: draft.slice(end) });
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
            disabled={!canSend || draft.trim() === '' || reviewCleaning}
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
            : state.kind === 'cleaning'
              ? 'Cleaning up the text'
              : ''}
      </p>
      {body}
      <div className="button-row voice-dialog-footer">{actions}</div>
    </Modal>
  );
}
