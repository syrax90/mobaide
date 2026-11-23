import { useId, useState } from 'react';
import { PRIVACY_POLICY_URL } from '../routes/settings/supportLinks';
import { VOICE_ENGINE, type CloudVoice, type VoiceEngine } from '../protocol/dto/cloudVoice';
import { VOICE_RECOMMENDATION_LOW, VOICE_STATE, type VoiceStatus } from '../protocol/dto/voice';
import {
  CLOUD_VOICE_NAME,
  CLOUD_VOICE_SUMMARY,
  LOCAL_VOICE_NAME,
  VOICE_REVIEW_NOTE,
  cloudVoiceDetails,
  localVoiceSummary,
} from './cloudVoiceCopy';
import { useVoicePrefsStore } from './voicePrefsStore';
import { formatBytes } from './voiceStatusView';

interface VoiceEngineChooserProps {
  agentId: string;
  agentName: string;
  cloud: CloudVoice;
  /** The agent has voice input of its own (`capabilities.voice`). */
  localSupported: boolean;
  /** The agent's voice status; undefined while loading or when it has none. */
  status: VoiceStatus | undefined;
  loadFailed: boolean;
  /** The agent did not answer the capability probe. */
  probeFailed?: boolean;
  /**
   * Save the choice. The parent owns the request: saving re-renders this
   * chooser away, and a request owned here would lose its follow-up.
   */
  onChoose: (engine: VoiceEngine) => void;
  saving: boolean;
  saveFailed: boolean;
  /** The user left the choice for later. */
  onDismiss: () => void;
}

/** Why "On this computer" cannot be chosen, or null when it can. */
export function localBlocker(
  agentName: string,
  localSupported: boolean,
  status: VoiceStatus | undefined,
  loadFailed: boolean,
  probeFailed = false,
): string | null {
  // An agent that did not answer the capability probe is unreachable, which
  // is not the same as an agent without voice of its own.
  if (probeFailed) return `Couldn’t reach ${agentName}.`;
  if (!localSupported) return `${agentName} runs a Mobaide agent without on-computer voice input.`;
  if (status === undefined) return loadFailed ? `Couldn’t reach ${agentName}.` : `Checking ${agentName}…`;
  if (status.state === VOICE_STATE.unsupported) return `${agentName} can’t run on-computer voice input.`;
  return null;
}

/**
 * The first mic tap on an agent with no engine choice: where the speech is
 * turned into text. Mobaide Cloud is preselected; nothing is recorded or sent
 * before the user confirms. Android `VoiceEngineChooser` and iOS
 * `VoiceEngineChooser` lay out the same content.
 */
export function VoiceEngineChooser({
  agentId,
  agentName,
  cloud,
  localSupported,
  status,
  loadFailed,
  probeFailed = false,
  onChoose,
  saving,
  saveFailed,
  onDismiss,
}: VoiceEngineChooserProps) {
  const groupId = useId();
  const [selected, setSelected] = useState<VoiceEngine>(VOICE_ENGINE.cloud);
  const setMicHidden = useVoicePrefsStore((state) => state.setMicHidden);
  const blocker = localBlocker(agentName, localSupported, status, loadFailed, probeFailed);
  const lowResources = status?.host.recommendation === VOICE_RECOMMENDATION_LOW;

  return (
    <div className="voice-panel">
      <p id={`${groupId}-label`}>
        Choose where your speech is turned into text for <strong>{agentName}</strong>.
      </p>
      <div className="voice-engine-cards" role="radiogroup" aria-labelledby={`${groupId}-label`}>
        <label className="voice-engine-card">
          <input
            type="radio"
            name={groupId}
            checked={selected === VOICE_ENGINE.cloud}
            onChange={() => {
              setSelected(VOICE_ENGINE.cloud);
            }}
          />
          <span className="voice-engine-card-body">
            <span className="voice-engine-card-title">
              {CLOUD_VOICE_NAME}
              <span className="voice-engine-badge">Recommended</span>
            </span>
            <span>{CLOUD_VOICE_SUMMARY}</span>
            <span className="voice-engine-card-note">
              {cloudVoiceDetails(cloud)}{' '}
              <a href={PRIVACY_POLICY_URL} target="_blank" rel="noreferrer">
                Privacy policy
              </a>
            </span>
          </span>
        </label>
        <label className="voice-engine-card" aria-disabled={blocker !== null}>
          <input
            type="radio"
            name={groupId}
            checked={selected === VOICE_ENGINE.local}
            disabled={blocker !== null}
            onChange={() => {
              setSelected(VOICE_ENGINE.local);
            }}
          />
          <span className="voice-engine-card-body">
            <span className="voice-engine-card-title">{LOCAL_VOICE_NAME}</span>
            {blocker !== null ? (
              <span className="voice-engine-card-note">{blocker}</span>
            ) : (
              <>
                <span>{localVoiceSummary(agentName)}</span>
                {status !== undefined && (
                  <span className="voice-engine-card-note">
                    {status.state === VOICE_STATE.notInstalled
                      ? `A one-time download of about ${formatBytes(status.model.download_bytes)}, then about ${formatBytes(status.model.disk_bytes)} of disk and about ${formatBytes(status.model.ram_bytes)} of memory while you dictate. `
                      : ''}
                    The audio travels through the Mobaide relay to {agentName} and is never
                    stored.
                    {lowResources ? ' This computer may struggle with it.' : ''}
                  </span>
                )}
              </>
            )}
          </span>
        </label>
      </div>
      <p className="page-note">
        {VOICE_REVIEW_NOTE} You can change this later in {agentName}&rsquo;s Voice input
        settings.
      </p>
      {saveFailed && (
        <p className="voice-error" role="alert">
          Couldn&rsquo;t save your choice. Check the connection and try again.
        </p>
      )}
      <div className="button-row voice-engine-actions">
        <button
          type="button"
          className="button button-primary"
          disabled={saving}
          onClick={() => {
            onChoose(selected);
          }}
        >
          Continue
        </button>
        <button type="button" className="button" onClick={onDismiss}>
          Not now
        </button>
      </div>
      <button
        type="button"
        className="link-button"
        onClick={() => {
          setMicHidden(agentId, true);
          onDismiss();
        }}
      >
        Hide the mic for this agent
      </button>
    </div>
  );
}
