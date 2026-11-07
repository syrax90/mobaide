import { useState, type ReactNode } from 'react';
import { VOICE_RECOMMENDATION_LOW, VOICE_STATE, type VoiceStatus } from '../protocol/dto/voice';
import { CleanupToggle } from './CleanupToggle';
import {
  useVoiceActions,
  useVoiceCleanupCapability,
  useVoiceCleanupSetting,
  type VoiceAction,
} from './useVoiceStatus';
import { isVoiceApiError } from './voiceApi';
import { installErrorCopy, voiceActionErrorCopy } from './voiceErrorCopy';
import { useMicHidden, useSkipReview, useVoicePrefsStore } from './voicePrefsStore';
import {
  formatBytes,
  languageNames,
  voicePreparingLabel,
  voiceProgressPercent,
} from './voiceStatusView';

interface VoicePanelProps {
  agentId: string;
  agentName: string;
  /**
   * `setup`: opened from the mic in a session (enable sheet, progress, retry).
   * `settings`: the agent's Voice input settings (adds remove, reinstall,
   * the mic toggle and the model's license).
   */
  variant: 'setup' | 'settings';
  status: VoiceStatus | undefined;
  loadFailed: boolean;
  /**
   * The setup variant's session can clean up transcripts, so the cleanup
   * toggle is offered once voice is enabled: the first dictation can use it.
   * The settings variant reads the agent's capability instead.
   */
  cleanupOffered?: boolean;
  /** "Not now" / "Hide the mic" in the setup variant. */
  onDismiss?: () => void;
}

/**
 * Voice input for one agent, in every state, with the real costs the agent
 * reports. Android `VoicePanel.kt` and iOS `VoicePanel.swift` lay out the
 * same content.
 */
export function VoicePanel({
  agentId,
  agentName,
  variant,
  status,
  loadFailed,
  cleanupOffered = false,
  onDismiss,
}: VoicePanelProps) {
  const actions = useVoiceActions(agentId);
  const hidden = useMicHidden(agentId);
  const setMicHidden = useVoicePrefsStore((state) => state.setMicHidden);
  const skipReview = useSkipReview(agentId);
  const setSkipReview = useVoicePrefsStore((state) => state.setSkipReview);
  const settings = variant === 'settings';
  const cleanupSupported = useVoiceCleanupCapability(agentId, settings).data === true;
  const cleanUp = useVoiceCleanupSetting(agentId, status);
  const [confirmRemove, setConfirmRemove] = useState(false);

  if (status === undefined) {
    return (
      <p className="page-note" role="status">
        {loadFailed ? `Couldn't reach ${agentName}.` : 'Checking voice input…'}
      </p>
    );
  }

  const run = (action: VoiceAction) => {
    setConfirmRemove(false);
    actions.mutate(action);
  };
  const busy = actions.isPending;
  const actionError = actions.error;
  const languages = languageNames(status.model.languages);
  const lowResources = status.host.recommendation === VOICE_RECOMMENDATION_LOW;
  const cleanupToggleShown = settings
    ? cleanupSupported && status.state !== VOICE_STATE.unsupported
    : cleanupOffered &&
      (status.state === VOICE_STATE.queued ||
        status.state === VOICE_STATE.downloading ||
        status.state === VOICE_STATE.verifying ||
        status.state === VOICE_STATE.ready);

  const removeControls = settings && (
    <>
      {confirmRemove ? (
        <div className="voice-confirm" role="group" aria-label="Remove voice input">
          <p>
            Remove voice input from {agentName}? This frees about{' '}
            {formatBytes(status.model.disk_bytes)} of disk.
          </p>
          <div className="button-row">
            <button
              type="button"
              className="button button-danger"
              disabled={busy}
              onClick={() => {
                run('remove');
              }}
            >
              Remove
            </button>
            <button
              type="button"
              className="button"
              onClick={() => {
                setConfirmRemove(false);
              }}
            >
              Keep
            </button>
          </div>
        </div>
      ) : (
        <button
          type="button"
          className="button"
          disabled={busy}
          onClick={() => {
            setConfirmRemove(true);
          }}
        >
          Remove voice input
        </button>
      )}
    </>
  );

  let body: ReactNode;
  switch (status.state) {
    case VOICE_STATE.notInstalled:
      body = (
        <>
          <p>
            Voice input on <strong>{agentName}</strong> downloads about{' '}
            {formatBytes(status.model.download_bytes)} once, then uses about{' '}
            {formatBytes(status.model.disk_bytes)} of disk and about{' '}
            {formatBytes(status.model.ram_bytes)} of memory while you dictate.
          </p>
          {lowResources && <LowResourcesWarning status={status} />}
          <p className="page-note">
            {agentName} transcribes what you say. The audio travels through the Mobaide relay
            and is never stored. The text appears for you to review and is never sent on its
            own.
          </p>
          <div className="button-row">
            <button
              type="button"
              className="button button-primary"
              disabled={busy}
              onClick={() => {
                run('install');
              }}
            >
              Enable voice input
            </button>
            {!settings && (
              <button type="button" className="button" onClick={onDismiss}>
                Not now
              </button>
            )}
          </div>
          {!settings && (
            <button
              type="button"
              className="link-button"
              onClick={() => {
                setMicHidden(agentId, true);
                onDismiss?.();
              }}
            >
              Hide the mic for this agent
            </button>
          )}
        </>
      );
      break;
    case VOICE_STATE.queued:
    case VOICE_STATE.downloading:
    case VOICE_STATE.verifying: {
      const percent = voiceProgressPercent(status);
      const label = voicePreparingLabel(status);
      body = (
        <>
          <p role="status">{label}</p>
          <progress
            className="voice-progress"
            aria-label={label}
            max={100}
            {...(percent === null ? {} : { value: percent })}
          />
          <p className="page-note">
            {status.repairing === true
              ? 'Your voice model stays; only a small update is downloading.'
              : 'You can close this. The mic turns on by itself when voice input is ready.'}
          </p>
          <div className="button-row">
            <button
              type="button"
              className="button"
              disabled={busy}
              onClick={() => {
                run('cancel');
              }}
            >
              {status.repairing === true ? 'Stop' : 'Cancel'}
            </button>
          </div>
        </>
      );
      break;
    }
    case VOICE_STATE.ready:
      body = (
        <>
          <p role="status">Voice input is ready on {agentName}.</p>
          {settings && (
            <div className="button-row">
              <button
                type="button"
                className="button"
                disabled={busy}
                onClick={() => {
                  run('reinstall');
                }}
              >
                Reinstall voice input
              </button>
              {removeControls}
            </div>
          )}
        </>
      );
      break;
    case VOICE_STATE.failed:
      body = (
        <>
          <p role="alert">
            {installErrorCopy(status.error?.code ?? '', status.repairing === true)}
          </p>
          {status.error?.message !== undefined && status.error.message !== '' && (
            <details className="voice-details">
              <summary>Details</summary>
              <pre>{status.error.message}</pre>
            </details>
          )}
          {lowResources && <LowResourcesWarning status={status} />}
          <div className="button-row">
            <button
              type="button"
              className="button button-primary"
              disabled={busy}
              onClick={() => {
                run('install');
              }}
            >
              Retry
            </button>
            {removeControls}
          </div>
        </>
      );
      break;
    default:
      body = (
        <>
          <p role="status">{agentName} can&rsquo;t run voice input.</p>
          {status.host.reasons.length > 0 && (
            <ul className="voice-reasons">
              {status.host.reasons.map((reason) => (
                <li key={reason.code}>{reason.message}</li>
              ))}
            </ul>
          )}
        </>
      );
  }

  return (
    <div className="voice-panel">
      {body}
      {actionError !== null && (
        <p className="voice-error" role="alert">
          {isVoiceApiError(actionError) && actionError.network
            ? `Couldn't reach ${agentName}.`
            : voiceActionErrorCopy(isVoiceApiError(actionError) ? actionError.code : null)}
        </p>
      )}
      {(settings || status.state === VOICE_STATE.notInstalled) && (
        <dl className="voice-model">
          <div>
            <dt>Model</dt>
            <dd>{status.model.name}</dd>
          </div>
          <div>
            <dt>Languages</dt>
            <dd>{languages.join(', ')}</dd>
          </div>
        </dl>
      )}
      {settings && status.state !== VOICE_STATE.unsupported && (
        <label className="voice-toggle">
          <input
            type="checkbox"
            checked={!hidden}
            onChange={(event) => {
              setMicHidden(agentId, !event.target.checked);
            }}
          />
          Show the mic button in sessions
        </label>
      )}
      {settings && status.state !== VOICE_STATE.unsupported && (
        <label className="voice-toggle">
          <input
            type="checkbox"
            checked={!skipReview}
            onChange={(event) => {
              setSkipReview(agentId, !event.target.checked);
            }}
          />
          Review dictated text before it is inserted
        </label>
      )}
      {cleanupToggleShown && (
        <CleanupToggle
          className="voice-toggle"
          agentName={agentName}
          checked={cleanUp.on}
          saveFailed={cleanUp.failed}
          onChange={cleanUp.set}
        />
      )}
      {settings && status.model.notice !== undefined && status.model.notice !== '' && (
        <details className="voice-details">
          <summary>Model license</summary>
          <pre className="voice-notice">{status.model.notice}</pre>
        </details>
      )}
    </div>
  );
}

function LowResourcesWarning({ status }: { status: VoiceStatus }) {
  return (
    <div className="voice-warning" role="note">
      <strong>This computer may struggle with voice input.</strong>
      <ul>
        {status.host.reasons.map((reason) => (
          <li key={reason.code}>{reason.message}</li>
        ))}
      </ul>
    </div>
  );
}
