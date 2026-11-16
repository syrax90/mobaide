import { herdrChip, type HerdrStatus } from '../../herdr/herdrDisplay';

/**
 * The leading mark of a Herdr row: the session list's spinning square while an
 * agent works, its static square otherwise. Decorative — the chip has the words.
 */
export function HerdrStatusMark({ status }: { status: HerdrStatus }) {
  if (status === 'working') {
    return (
      <span className="session-spinner" aria-hidden="true">
        <span />
        <span />
        <span />
        <span />
      </span>
    );
  }
  const tone =
    status === 'blocked' || status === 'failed'
      ? 'crashed'
      : status === 'done'
        ? 'running'
        : status === 'none'
          ? 'stopped'
          : 'pending';
  return <span className={`session-dot session-dot-${tone}`} aria-hidden="true" />;
}

export function HerdrStatusChip({ status }: { status: HerdrStatus }) {
  const chip = herdrChip(status);
  if (chip === null) return null;
  return <span className={`session-attention session-attention-${chip.tone}`}>{chip.text}</span>;
}
