import type { ChatState } from './chatWire';

/** "12s", "3m 05s", "1h 02m". */
export function formatElapsed(ms: number): string {
  const total = Math.max(0, Math.floor(ms / 1000));
  const hours = Math.floor(total / 3600);
  const minutes = Math.floor((total % 3600) / 60);
  const seconds = total % 60;
  if (hours > 0) return `${String(hours)}h ${String(minutes).padStart(2, '0')}m`;
  if (minutes > 0) return `${String(minutes)}m ${String(seconds).padStart(2, '0')}s`;
  return `${String(seconds)}s`;
}

/** "Just now", "5 min ago", "3 h ago", "Yesterday", "4 days ago", or a date. */
export function formatRelativeTime(iso: string, now: number): string | null {
  const then = Date.parse(iso);
  if (Number.isNaN(then)) return null;
  const minutes = Math.floor((now - then) / 60_000);
  if (minutes < 1) return 'Just now';
  if (minutes < 60) return `${String(minutes)} min ago`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${String(hours)} h ago`;
  const days = Math.floor(hours / 24);
  if (days === 1) return 'Yesterday';
  if (days < 7) return `${String(days)} days ago`;
  return new Date(then).toLocaleDateString(undefined, {
    year: 'numeric',
    month: 'short',
    day: 'numeric',
  });
}

export function formatTokens(value: number): string {
  if (value >= 1_000_000) return `${(value / 1_000_000).toFixed(1).replace(/\.0$/, '')}M`;
  if (value >= 1000) return `${(value / 1000).toFixed(1).replace(/\.0$/, '')}k`;
  return String(Math.round(value));
}

export function formatCost(amount: number, currency: string): string {
  try {
    return new Intl.NumberFormat(undefined, {
      style: 'currency',
      currency: currency === '' ? 'USD' : currency,
      maximumFractionDigits: amount < 1 ? 3 : 2,
    }).format(amount);
  } catch {
    return `${amount.toFixed(2)} ${currency}`;
  }
}

/** `/a/b/file.ts` → `{ dir: '/a/b/', name: 'file.ts' }`, for either separator. */
export function splitPath(path: string): { dir: string; name: string } {
  const cut = Math.max(path.lastIndexOf('/'), path.lastIndexOf('\\'));
  return cut === -1 ? { dir: '', name: path } : { dir: path.slice(0, cut + 1), name: path.slice(cut + 1) };
}

export type StatusTone = 'muted' | 'accent' | 'danger';

export interface StatusLine {
  text: string;
  tone: StatusTone;
}

/** The top bar's second line, first match wins. */
export function chatStatusLine(
  state: ChatState,
  live: boolean,
  gone: boolean,
  now: number,
): StatusLine {
  if (gone || state.status === 'stopped') return { text: 'Session closed', tone: 'muted' };
  if (!live) return { text: 'Reconnecting…', tone: 'muted' };
  if (state.requests.length > 0) return { text: 'Waiting for you', tone: 'accent' };
  if (state.status === 'starting') return { text: 'Starting…', tone: 'muted' };
  if (state.status === 'loading') return { text: 'Opening the session…', tone: 'muted' };
  if (state.cancelling) return { text: 'Stopping…', tone: 'muted' };
  if (state.status === 'running') {
    const started = state.turn_started_at === null ? Number.NaN : Date.parse(state.turn_started_at);
    return {
      text: Number.isNaN(started) ? 'Working' : `Working · ${formatElapsed(now - started)}`,
      tone: 'muted',
    };
  }
  if (state.status === 'error') return { text: 'Stopped', tone: 'danger' };
  return { text: 'Ready', tone: 'muted' };
}
