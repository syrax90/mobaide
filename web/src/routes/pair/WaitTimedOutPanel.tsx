import { Link } from 'react-router';
import { WAIT_TIMEOUT_TITLE, waitTimeoutMessage } from './pairCopy';

interface WaitTimedOutPanelProps {
  friendlyName: string;
  wsDelivered: boolean | null;
  onKeepWaiting: () => void;
}

/**
 * What both pairing routes show when the 60 s wait runs out with the agent
 * still offline. A status, not an alert: the claim succeeded and the agent is
 * paired — the only open question is when its relay client connects.
 */
export function WaitTimedOutPanel({
  friendlyName,
  wsDelivered,
  onKeepWaiting,
}: WaitTimedOutPanelProps) {
  return (
    <div role="status" aria-live="polite">
      <h2>{WAIT_TIMEOUT_TITLE}</h2>
      <p className="page-note">{waitTimeoutMessage(friendlyName, wsDelivered)}</p>
      <div className="button-row">
        <button type="button" className="button button-primary" onClick={onKeepWaiting}>
          Keep waiting
        </button>
        <Link className="button" to="/agents">
          All agents
        </Link>
      </div>
    </div>
  );
}
