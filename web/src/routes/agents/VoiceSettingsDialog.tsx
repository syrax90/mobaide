import { useId } from 'react';
import type { Agent } from '../../api/agentRepository';
import { Modal } from '../../components/Modal';
import { useAgentEvents } from '../../realtime/useAgentEvents';
import { VoicePanel } from '../../voice/VoicePanel';
import { useVoiceCapability, useVoiceStatus } from '../../voice/useVoiceStatus';

interface VoiceSettingsDialogProps {
  agent: Agent;
  onClose: () => void;
}

/**
 * The agent's Voice input settings: status and install progress, enable,
 * remove and reinstall, the session mic toggle, the model and its license.
 * Holds the agent's events channel while open, so progress arrives as
 * `voice.changed` instead of polling.
 */
export function VoiceSettingsDialog({ agent, onClose }: VoiceSettingsDialogProps) {
  const titleId = useId();
  useAgentEvents(agent.agentId);
  const capability = useVoiceCapability(agent.agentId);
  const supported = capability.data !== undefined && capability.data !== null;
  const status = useVoiceStatus(agent.agentId, supported);

  let content;
  if (capability.isPending) {
    content = (
      <p className="page-note" role="status">
        Checking {agent.name}…
      </p>
    );
  } else if (capability.isError) {
    content = <p role="alert">Couldn&rsquo;t reach {agent.name}. Is it online?</p>;
  } else if (!supported) {
    content = (
      <p role="status">
        {agent.name} runs a Mobaide agent without voice input. It updates itself; check again
        after its next update.
      </p>
    );
  } else {
    content = (
      <VoicePanel
        agentId={agent.agentId}
        agentName={agent.name}
        variant="settings"
        status={status.data}
        loadFailed={status.isError}
      />
    );
  }

  return (
    <Modal labelledBy={titleId} onClose={onClose} className="voice-dialog">
      <h2 id={titleId}>Voice input · {agent.name}</h2>
      {content}
      <div className="button-row voice-dialog-footer">
        <button type="button" className="button" onClick={onClose}>
          Close
        </button>
      </div>
    </Modal>
  );
}
