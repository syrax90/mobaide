import { useId } from 'react';
import type { Agent } from '../../api/agentRepository';
import { Modal } from '../../components/Modal';
import { CheckboxField } from '../../components/form/CheckboxField';
import { KEEP_AWAKE_STATE, type KeepAwakeStatus } from '../../protocol/dto/agentSettings';
import { useAgentEvents } from '../../realtime/useAgentEvents';
import { useVoiceReprobeOnline, voiceQueryFailed } from '../../voice/useVoiceStatus';
import { useAgentSettings, useKeepAwakeCapability, useKeepAwakeSetting } from './useKeepAwake';

interface AgentSettingsDialogProps {
  agent: Agent;
  onClose: () => void;
}

/** Android `KeepAwakeCopy` and iOS `KeepAwakeCopy` hold the same text. */
export function keepAwakeHint(agentName: string): string {
  return (
    `Stops ${agentName} from going to sleep while it is idle, so your sessions stay reachable. ` +
    'The screen can still turn off, except on some Windows laptops on battery, ' +
    'where it stays on and does not lock by itself. Closing a laptop lid still puts it to sleep.'
  );
}

/**
 * The settings stored on one agent, shared by every client of it. Holds the
 * agent's events channel while open, so a change made elsewhere, or a lock
 * the computer lost, arrives as `agent_settings.changed`.
 */
export function AgentSettingsDialog({ agent, onClose }: AgentSettingsDialogProps) {
  const titleId = useId();
  useAgentEvents(agent.agentId);
  const capability = useKeepAwakeCapability(agent.agentId);
  const probeFailed = voiceQueryFailed(capability);
  const supported = capability.data === true;
  const settings = useAgentSettings(agent.agentId, supported);
  const settingsFailed = supported && voiceQueryFailed(settings);
  // The capability probe is shared with voice input: the same re-probe when
  // the agent list says the agent came back online.
  useVoiceReprobeOnline(agent.agentId, agent.online);

  let body;
  if (capability.isPending && !probeFailed) {
    body = (
      <p className="page-note" role="status">
        Checking {agent.name}…
      </p>
    );
  } else if (probeFailed) {
    body = <p role="alert">Couldn&rsquo;t reach {agent.name}. Is it online?</p>;
  } else if (!supported) {
    body = (
      <p role="status">
        {agent.name} runs a Mobaide agent without this setting. It updates itself; check again
        after its next update.
      </p>
    );
  } else if (settings.data === undefined) {
    body = settingsFailed ? (
      <p role="alert">Couldn&rsquo;t reach {agent.name}. Is it online?</p>
    ) : (
      <p className="page-note" role="status">
        Checking {agent.name}…
      </p>
    );
  } else {
    body = (
      <KeepAwakeSetting
        agentId={agent.agentId}
        agentName={agent.name}
        status={settings.data.keep_awake}
      />
    );
  }

  return (
    <Modal labelledBy={titleId} onClose={onClose}>
      <h2 id={titleId}>Settings · {agent.name}</h2>
      <div className="agent-settings">{body}</div>
      <div className="button-row agent-settings-footer">
        <button type="button" className="button" onClick={onClose}>
          Close
        </button>
      </div>
    </Modal>
  );
}

function KeepAwakeSetting({
  agentId,
  agentName,
  status,
}: {
  agentId: string;
  agentName: string;
  status: KeepAwakeStatus;
}) {
  const save = useKeepAwakeSetting(agentId);
  const unsupported = status.state === KEEP_AWAKE_STATE.unsupported;
  // While a change is saved the box shows it; the answer then shows what the
  // agent applied.
  const checked = save.isPending ? (save.variables ?? status.enabled) : status.enabled;
  let note: { text: string; alert: boolean } | null = null;
  if (save.isError) {
    note = { text: `Couldn’t save this setting on ${agentName}. Try again.`, alert: true };
  } else if (unsupported) {
    note = { text: status.message ?? `${agentName} can’t be kept awake.`, alert: false };
  } else if (!save.isPending && status.enabled && status.state !== KEEP_AWAKE_STATE.active) {
    const reason = status.message ?? 'The computer did not say why.';
    note = { text: `${agentName} can’t stay awake: ${reason}`, alert: true };
  } else if (status.state === KEEP_AWAKE_STATE.active && status.message) {
    note = { text: status.message, alert: false };
  }
  return (
    <>
      <CheckboxField
        label={`Keep ${agentName} awake`}
        hint={keepAwakeHint(agentName)}
        checked={checked}
        disabled={save.isPending || (unsupported && !status.enabled)}
        onChange={(on) => {
          save.mutate(on);
        }}
      />
      {note !== null && (
        <p
          className={note.alert ? 'agent-settings-error' : 'page-note'}
          role={note.alert ? 'alert' : 'status'}
        >
          {note.text}
        </p>
      )}
    </>
  );
}
