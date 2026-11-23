import { useId } from 'react';
import { Modal } from '../../../components/Modal';
import { VOICE_ENGINE, type CloudVoice, type VoiceEngine } from '../../../protocol/dto/cloudVoice';
import { VOICE_STATE, type VoiceStatus } from '../../../protocol/dto/voice';
import { CloudVoicePanel } from '../../../voice/CloudVoicePanel';
import { VoiceEngineChooser } from '../../../voice/VoiceEngineChooser';
import { VoicePanel } from '../../../voice/VoicePanel';
import { useVoiceEngineChoice } from '../../../voice/useCloudVoice';
import { useVoiceActions } from '../../../voice/useVoiceStatus';
import { cloudVoiceOffered, type VoiceRoute } from '../../../voice/voiceEngine';

interface VoiceSetupDialogProps {
  agentId: string;
  agentName: string;
  route: VoiceRoute;
  cloud: CloudVoice | undefined;
  cloudChecking: boolean;
  onRecheckCloud: () => Promise<unknown>;
  /** The agent has voice input of its own. */
  localSupported: boolean;
  /** The agent did not answer the capability probe. */
  localProbeFailed?: boolean;
  status: VoiceStatus | undefined;
  loadFailed: boolean;
  /** This session can clean up transcripts: the setup offers the toggle. */
  cleanupOffered: boolean;
  onClose: () => void;
}

/**
 * What the mic opens while it cannot record:
 *
 * - no engine chosen for this agent yet: the chooser;
 * - cloud voice input that is down or out of today's allowance: why, with
 *   Try again and the way to the computer's own voice;
 * - on-computer voice that is not ready: the enable sheet with the agent's
 *   real costs, the install progress, or a failed install with Retry.
 *
 * It follows the live state, so a choice or Enable turns into the next step
 * in place. Never opens on its own.
 */
export function VoiceSetupDialog({
  agentId,
  agentName,
  route,
  cloud,
  cloudChecking,
  onRecheckCloud,
  localSupported,
  localProbeFailed = false,
  status,
  loadFailed,
  cleanupOffered,
  onClose,
}: VoiceSetupDialogProps) {
  const titleId = useId();
  const choice = useVoiceEngineChoice(agentId);
  const actions = useVoiceActions(agentId);
  const offered = cloudVoiceOffered(cloud);
  const notInstalled = status?.state === VOICE_STATE.notInstalled;
  // Owned here, not by the chooser: the saved choice replaces the chooser
  // with the next step, and this dialog is what stays mounted to act on it.
  const choose = (engine: VoiceEngine) => {
    choice.mutate(engine, {
      onSuccess: () => {
        if (engine === VOICE_ENGINE.cloud) onClose();
        // The card showed the download's cost, so the install starts now and
        // the dialog turns into its progress.
        else if (notInstalled) actions.mutate('install');
      },
    });
  };
  // The chooser has its own "Not now", so the dialog's Close would only add
  // height on a phone-sized window.
  const choosing = offered && route === 'choose';
  let content;
  if (offered && route === 'choose') {
    content = (
      <VoiceEngineChooser
        agentId={agentId}
        agentName={agentName}
        cloud={cloud}
        localSupported={localSupported}
        status={status}
        loadFailed={loadFailed}
        probeFailed={localProbeFailed}
        onChoose={choose}
        saving={choice.isPending}
        saveFailed={choice.isError}
        onDismiss={onClose}
      />
    );
  } else if (offered && route === 'cloud') {
    content = (
      <CloudVoicePanel
        agentId={agentId}
        agentName={agentName}
        variant="setup"
        cloud={cloud}
        checking={cloudChecking}
        onRecheck={onRecheckCloud}
        {...(localSupported
          ? {
              // No install from here: this panel never showed its cost. The
              // dialog turns into the enable sheet, which does.
              onUseLocal: () => {
                choice.mutate(VOICE_ENGINE.local);
              },
            }
          : {})}
      />
    );
  } else {
    content = (
      <VoicePanel
        agentId={agentId}
        agentName={agentName}
        variant="setup"
        status={status}
        loadFailed={loadFailed}
        cleanupOffered={cleanupOffered}
        onDismiss={onClose}
        {...(offered
          ? {
              onUseCloud: () => {
                choice.mutate(VOICE_ENGINE.cloud, { onSuccess: onClose });
              },
            }
          : {})}
      />
    );
  }
  return (
    <Modal labelledBy={titleId} onClose={onClose} className="voice-dialog">
      <h2 id={titleId}>Voice input</h2>
      {content}
      {choice.isError && !choosing && (
        <p className="voice-error" role="alert">
          Couldn&rsquo;t save your choice. Check the connection and try again.
        </p>
      )}
      {!choosing && (
        <div className="button-row voice-dialog-footer">
          <button type="button" className="button" onClick={onClose}>
            Close
          </button>
        </div>
      )}
    </Modal>
  );
}
