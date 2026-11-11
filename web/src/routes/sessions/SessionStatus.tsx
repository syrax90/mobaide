import type { SessionResponse } from '../../protocol/dto/session';
import { isSessionWorking, sessionStateTone, sessionStatusChip } from './sessionDisplay';

type StatusSession = Pick<SessionResponse, 'state' | 'activity' | 'attention'>;

/**
 * The row's leading mark: a spinning 2×2 pixel square while the agent works,
 * otherwise the static status square. Decorative — the chip carries the words.
 */
export function SessionStatusIndicator({ session }: { session: StatusSession }) {
  if (isSessionWorking(session)) {
    return (
      <span className="session-spinner" aria-hidden="true">
        <span />
        <span />
        <span />
        <span />
      </span>
    );
  }
  return (
    <span
      className={`session-dot session-dot-${sessionStateTone(session.state)}`}
      aria-hidden="true"
    />
  );
}

export function SessionStatusChip({ session }: { session: StatusSession }) {
  const chip = sessionStatusChip(session);
  if (chip === null) return null;
  return <span className={`session-attention session-attention-${chip.tone}`}>{chip.text}</span>;
}
