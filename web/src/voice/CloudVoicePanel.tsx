import { useState } from 'react';
import { PRIVACY_POLICY_URL } from '../routes/settings/supportLinks';
import { CLOUD_VOICE_STATE, type CloudVoice } from '../protocol/dto/cloudVoice';
import { VoiceSessionToggles } from './VoiceSessionToggles';
import { formatAllowance, formatUsage, resetTimeLabel } from './voiceEngine';

interface CloudVoicePanelProps {
  agentId: string;
  agentName: string;
  /**
   * `setup`: opened from the mic in a session, when cloud voice input cannot
   * record now. `settings`: the agent's Voice input settings.
   */
  variant: 'setup' | 'settings';
  cloud: CloudVoice;
  /** A fresh status is on its way. */
  checking: boolean;
  /** Asks for a fresh status; settles when the answer is in. */
  onRecheck: () => Promise<unknown>;
  /** Switch this agent to on-computer voice input; absent when it has none. */
  onUseLocal?: () => void;
}

const DOWN_NOTE = 'Mobaide’s speech service isn’t answering. This is usually short.';
const STILL_DOWN_NOTE = 'Still not answering. Try again in a few seconds.';
const RECHECK_LABEL = 'Try again';
const CHECKING_LABEL = 'Checking…';

/**
 * Cloud voice input for one agent: ready, unavailable or out of today's
 * allowance, each with what the user can do about it. Android
 * `CloudVoicePanel` and iOS `CloudVoicePanel` lay out the same content.
 */
export function CloudVoicePanel({
  agentId,
  agentName,
  variant,
  cloud,
  checking,
  onRecheck,
  onUseLocal,
}: CloudVoicePanelProps) {
  const settings = variant === 'settings';
  // "Try again" that finds the service still down must not look like a tap
  // that did nothing. Set by the answer, not the tap, so the note never shows
  // before the check has run; a check that runs by itself leaves it alone,
  // so the note does not swap back and forth.
  const [rechecked, setRechecked] = useState(false);
  // The next outage starts without a "still not answering".
  if (rechecked && cloud.state !== CLOUD_VOICE_STATE.unavailable) setRechecked(false);
  const limit = formatAllowance(cloud.usage.limit_seconds);
  const resetAt = resetTimeLabel(cloud.usage.resets_at);
  const meanwhile =
    onUseLocal !== undefined ? ` You can dictate with ${agentName} itself meanwhile.` : '';
  const useLocal = onUseLocal !== undefined && (
    <button type="button" className="button" onClick={onUseLocal}>
      Use this computer instead
    </button>
  );

  let body;
  switch (cloud.state) {
    case CLOUD_VOICE_STATE.unavailable:
      body = (
        <>
          <p role="alert">Cloud voice input is unavailable right now.</p>
          {/* One slot for both notes, as tall as the taller one: a note
              added under the buttons, or one that wraps to fewer lines, would
              move them under the pointer on every retry. */}
          <div className="voice-note-slot">
            <p className="page-note" role="status">
              {rechecked ? STILL_DOWN_NOTE : DOWN_NOTE}
              {meanwhile}
            </p>
            <p className="page-note" aria-hidden="true">
              {rechecked ? DOWN_NOTE : STILL_DOWN_NOTE}
              {meanwhile}
            </p>
          </div>
          <div className="button-row">
            <button
              type="button"
              className="button button-primary"
              disabled={checking}
              onClick={() => {
                setRechecked(false);
                void onRecheck().then(
                  () => {
                    setRechecked(true);
                  },
                  () => undefined,
                );
              }}
            >
              {/* Both labels in one cell, so the button keeps one width and
                  the row never wraps differently while it checks. */}
              <span className="voice-label-slot">
                <span>{checking ? CHECKING_LABEL : RECHECK_LABEL}</span>
                <span aria-hidden="true">{checking ? RECHECK_LABEL : CHECKING_LABEL}</span>
              </span>
            </button>
            {useLocal}
          </div>
        </>
      );
      break;
    case CLOUD_VOICE_STATE.limitReached:
      body = (
        <>
          <p role="alert">You&rsquo;ve used today&rsquo;s {limit} of cloud voice input.</p>
          <p className="page-note">
            {resetAt === '' ? 'It resets tomorrow.' : `It resets at ${resetAt}.`}
            {meanwhile}
          </p>
          {useLocal !== false && <div className="button-row">{useLocal}</div>}
        </>
      );
      break;
    default:
      body = (
        <p role="status">
          {settings
            ? `Mobaide Cloud turns your speech into text for ${agentName}.`
            : 'Cloud voice input is ready. Close this and tap the mic to dictate.'}
        </p>
      );
  }

  return (
    <div className="voice-panel">
      {body}
      {settings && (
        <>
          <dl className="voice-model">
            <div>
              <dt>Used today</dt>
              <dd>{formatUsage(cloud.usage.used_seconds, cloud.usage.limit_seconds)}</dd>
            </div>
            <div>
              <dt>Recording</dt>
              <dd>Up to {formatAllowance(cloud.limits.max_seconds)} each</dd>
            </div>
          </dl>
          <p className="page-note">
            Recordings go to Mobaide&rsquo;s speech service over an encrypted connection and
            are not kept. The text is kept for a short time to prevent abuse.{' '}
            <a href={PRIVACY_POLICY_URL} target="_blank" rel="noreferrer">
              Privacy policy
            </a>
          </p>
          <VoiceSessionToggles agentId={agentId} />
        </>
      )}
    </div>
  );
}
