import { useId } from 'react';
import { VOICE_ENGINE, type VoiceEngine } from '../protocol/dto/cloudVoice';
import { CLOUD_VOICE_NAME, LOCAL_VOICE_NAME } from './cloudVoiceCopy';
import { useVoiceEngineChoice } from './useCloudVoice';
import type { VoiceRoute } from './voiceEngine';

interface VoiceEngineSettingProps {
  agentId: string;
  route: VoiceRoute;
  /** Why "On this computer" cannot be chosen, or null when it can. */
  localBlocker: string | null;
}

/**
 * "Speech recognition" in an agent's Voice input settings: Mobaide Cloud or
 * the computer. Stored on the server, so every device follows.
 */
export function VoiceEngineSetting({ agentId, route, localBlocker }: VoiceEngineSettingProps) {
  // An agent already on its own voice keeps the option, whatever its state now.
  const localDisabled = localBlocker !== null && route !== VOICE_ENGINE.local;
  const labelId = useId();
  const choice = useVoiceEngineChoice(agentId);
  const pick = (engine: VoiceEngine) => {
    if (route !== engine) choice.mutate(engine);
  };
  return (
    <div className="voice-engine-setting">
      <div className="voice-engine-options" role="radiogroup" aria-labelledby={labelId}>
        <span id={labelId} className="voice-engine-setting-label">
          Speech recognition
        </span>
        <label className="voice-toggle">
          <input
            type="radio"
            name={labelId}
            checked={route === VOICE_ENGINE.cloud}
            onChange={() => {
              pick(VOICE_ENGINE.cloud);
            }}
          />
          {CLOUD_VOICE_NAME}
          <span className="voice-engine-badge">Recommended</span>
        </label>
        <label className="voice-toggle">
          <input
            type="radio"
            name={labelId}
            checked={route === VOICE_ENGINE.local}
            disabled={localDisabled}
            onChange={() => {
              pick(VOICE_ENGINE.local);
            }}
          />
          {LOCAL_VOICE_NAME}
        </label>
        {/* A choice that cannot be made says why, as its card in the chooser does. */}
        {localDisabled && <span className="voice-engine-card-note">{localBlocker}</span>}
      </div>
      {choice.isError && (
        <p className="voice-error" role="alert">
          Couldn&rsquo;t save this setting. Check the connection and try again.
        </p>
      )}
    </div>
  );
}
