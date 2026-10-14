import { useId } from 'react';
import { Modal } from '../../../components/Modal';
import type { VoiceStatus } from '../../../protocol/dto/voice';
import { VoicePanel } from '../../../voice/VoicePanel';

interface VoiceSetupDialogProps {
  agentId: string;
  agentName: string;
  status: VoiceStatus | undefined;
  loadFailed: boolean;
  onClose: () => void;
}

/**
 * What the mic opens while voice is not ready: the enable sheet with the
 * agent's real costs, the install progress, or a failed install with Retry.
 * It follows the live status, so Enable turns into progress in place. Never
 * opens on its own.
 */
export function VoiceSetupDialog({
  agentId,
  agentName,
  status,
  loadFailed,
  onClose,
}: VoiceSetupDialogProps) {
  const titleId = useId();
  return (
    <Modal labelledBy={titleId} onClose={onClose} className="voice-dialog">
      <h2 id={titleId}>Voice input</h2>
      <VoicePanel
        agentId={agentId}
        agentName={agentName}
        variant="setup"
        status={status}
        loadFailed={loadFailed}
        onDismiss={onClose}
      />
      <div className="button-row voice-dialog-footer">
        <button type="button" className="button" onClick={onClose}>
          Close
        </button>
      </div>
    </Modal>
  );
}
