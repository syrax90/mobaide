import { useId } from 'react';
import type { Agent } from '../../api/agentRepository';
import { Modal } from '../../components/Modal';
import { useAgentEvents } from '../../realtime/useAgentEvents';
import { CloudVoicePanel } from '../../voice/CloudVoicePanel';
import { localBlocker } from '../../voice/VoiceEngineChooser';
import { VoiceEngineSetting } from '../../voice/VoiceEngineSetting';
import { VoicePanel } from '../../voice/VoicePanel';
import { VoiceSessionToggles } from '../../voice/VoiceSessionToggles';
import { useCloudVoice } from '../../voice/useCloudVoice';
import {
  useVoiceCapability,
  useVoiceReprobeOnline,
  useVoiceStatus,
  voiceQueryFailed,
} from '../../voice/useVoiceStatus';
import { cloudVoiceOffered, voiceRoute } from '../../voice/voiceEngine';

interface VoiceSettingsDialogProps {
  agent: Agent;
  onClose: () => void;
}

/**
 * The agent's Voice input settings: where speech is transcribed (Mobaide
 * Cloud or the computer, when cloud voice input is offered), and for the
 * computer's own voice the status and install progress, enable, remove and
 * reinstall, the model and its license. Holds the agent's events channel
 * while open, so progress arrives as `voice.changed` instead of polling.
 */
export function VoiceSettingsDialog({ agent, onClose }: VoiceSettingsDialogProps) {
  const titleId = useId();
  useAgentEvents(agent.agentId);
  const capability = useVoiceCapability(agent.agentId);
  const supported = capability.data !== undefined && capability.data !== null;
  const status = useVoiceStatus(agent.agentId, supported);
  const probeFailed = voiceQueryFailed(capability);
  const statusFailed = supported && voiceQueryFailed(status);
  useVoiceReprobeOnline(agent.agentId, agent.online);
  const cloudQuery = useCloudVoice();
  const cloud = cloudQuery.data;
  const offered = cloudVoiceOffered(cloud);
  const route = voiceRoute(cloud, agent.agentId, status.data);
  let localBlockedBy: string | null;
  if (capability.isPending && !probeFailed) localBlockedBy = `Checking ${agent.name}…`;
  else localBlockedBy = localBlocker(agent.name, supported, status.data, statusFailed, probeFailed);

  let local;
  if (capability.isPending && !probeFailed) {
    local = (
      <p className="page-note" role="status">
        Checking {agent.name}…
      </p>
    );
  } else if (probeFailed) {
    local = <p role="alert">Couldn&rsquo;t reach {agent.name}. Is it online?</p>;
  } else if (!supported) {
    local = (
      <p role="status">
        {agent.name} runs a Mobaide agent without {offered ? 'on-computer ' : ''}voice input. It
        updates itself; check again after its next update.
      </p>
    );
  } else {
    local = (
      <VoicePanel
        agentId={agent.agentId}
        agentName={agent.name}
        variant="settings"
        status={status.data}
        loadFailed={statusFailed}
        embedded={route === 'cloud'}
      />
    );
  }
  // Under cloud voice input the computer's own voice only matters once it is
  // enabled there: then it can still be removed or repaired from here.
  const localInstalled = supported && status.data?.enabled === true;

  return (
    <Modal labelledBy={titleId} onClose={onClose} className="voice-dialog">
      <h2 id={titleId}>Voice input · {agent.name}</h2>
      {offered && (
        <VoiceEngineSetting agentId={agent.agentId} route={route} localBlocker={localBlockedBy} />
      )}
      {offered && route === 'choose' ? (
        // No engine yet: showing the computer's enable panel here would
        // steer the user to the option that is not the recommended one.
        <div className="voice-panel">
          <p className="page-note" role="status">
            Choose one to turn on voice input for {agent.name}.
          </p>
          <VoiceSessionToggles agentId={agent.agentId} />
        </div>
      ) : offered && route === 'cloud' ? (
        <>
          <CloudVoicePanel
            agentId={agent.agentId}
            agentName={agent.name}
            variant="settings"
            cloud={cloud}
            checking={cloudQuery.isFetching}
            onRecheck={() => cloudQuery.refetch()}
          />
          {localInstalled && (
            <details className="voice-details">
              <summary>On-computer voice input on {agent.name}</summary>
              {local}
            </details>
          )}
        </>
      ) : (
        local
      )}
      <div className="button-row voice-dialog-footer">
        <button type="button" className="button" onClick={onClose}>
          Close
        </button>
      </div>
    </Modal>
  );
}
