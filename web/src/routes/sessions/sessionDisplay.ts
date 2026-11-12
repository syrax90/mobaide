/**
 * How a session renders on a card. Pure, so the parity rules are testable
 * without a DOM.
 */
import type { SessionResponse } from '../../protocol/dto/session';

const BUILT_IN_KIND_LABELS: Readonly<Record<string, string>> = {
  claude: 'Claude Code',
  codex: 'Codex',
  antigravity: 'Antigravity',
  opencode: 'OpenCode',
  copilot: 'GitHub Copilot',
  auto_scan: 'Auto Scan',
  shell: 'Terminal',
};

/**
 * The display name for a session's kind.
 *
 * The fallback chain applies to **every** unrecognised kind, not only
 * `custom_<12hex>`: a future built-in (`gemini`, say) reaching an older client
 * must render as something rather than as a raw wire token.
 *
 * `kind_label` comes first because it is snapshotted at creation, so it survives
 * the type being renamed or deleted afterwards. The live registry is the second
 * chance for sessions created before that snapshot existed.
 */
export function sessionKindLabel(
  session: SessionResponse,
  customTypeNames: ReadonlyMap<string, string>,
): string {
  const builtIn = Object.hasOwn(BUILT_IN_KIND_LABELS, session.kind)
    ? BUILT_IN_KIND_LABELS[session.kind]
    : undefined;
  if (builtIn !== undefined) return builtIn;
  return session.kind_label ?? customTypeNames.get(session.kind) ?? 'Custom';
}

/**
 * The card's headline. Falls back to Android's composite rather than iOS's bare
 * kind label — with several sessions of one kind open, "Claude Code" three times
 * over identifies nothing.
 */
export function sessionTitle(session: SessionResponse, kindLabel: string): string {
  if (session.title !== undefined && session.title.trim() !== '') return session.title;
  return `${kindLabel} · ${session.project_name ?? session.session_id.slice(0, 8)}`;
}

/**
 * The terminal header's label: the live OSC title while there is one, otherwise
 * the kind. Deliberately not [sessionTitle]'s composite — the cards may show a
 * truncated id to disambiguate a list, a single screen's header has nothing to
 * disambiguate against, so it never renders one.
 */
export function sessionHeaderLabel(
  session: SessionResponse,
  customTypeNames: ReadonlyMap<string, string>,
): string {
  if (session.title !== undefined && session.title.trim() !== '') return session.title;
  return sessionKindLabel(session, customTypeNames);
}

/**
 * How a confirm dialog names a session. Neither of the helpers above fits: a
 * destructive prompt must never fall back to an id the way [sessionTitle] can,
 * and [sessionHeaderLabel]'s bare kind is too vague when the list it was raised
 * from holds three sessions of that kind.
 */
export function sessionDialogLabel(
  session: SessionResponse,
  customTypeNames: ReadonlyMap<string, string>,
): string {
  if (session.title !== undefined && session.title.trim() !== '') return session.title;
  const kindLabel = sessionKindLabel(session, customTypeNames);
  if (session.project_name !== undefined && session.project_name.trim() !== '') {
    return `${kindLabel} · ${session.project_name}`;
  }
  return kindLabel;
}

export type SessionStateTone = 'running' | 'pending' | 'stopped' | 'crashed';

/**
 * `starting` and `stopping` collapse into one "pending" tone, matching the
 * terminal screen's existing status bar. Mobile gives them separate colours;
 * on a list of desktop-sized rows the extra shade reads as noise.
 */
export function sessionStateTone(state: string): SessionStateTone {
  switch (state) {
    case 'running':
      return 'running';
    case 'starting':
    case 'stopping':
      return 'pending';
    case 'crashed':
      return 'crashed';
    default:
      return 'stopped';
  }
}

export interface AttentionChip {
  text: string;
  tone: 'permission' | 'ready' | 'failed' | 'ended' | 'working';
}

/**
 * The attention pill, iOS `SessionDisplay.attention` and Android
 * `sessionAttention`.
 *
 * An unrecognised reason renders no chip rather than the raw token — a newer
 * agent inventing one should be invisible, not garbled.
 */
export function attentionChip(attention: string | undefined): AttentionChip | null {
  switch (attention) {
    case 'awaiting_permission':
    case 'awaiting_os_permission':
      return { text: 'Needs permission', tone: 'permission' };
    case 'turn_finished':
      return { text: 'Ready', tone: 'ready' };
    case 'turn_failed':
      return { text: 'Failed', tone: 'failed' };
    case 'ended':
      return { text: 'Ended', tone: 'ended' };
    default:
      return null;
  }
}

function needsPermission(attention: string | undefined): boolean {
  return attention === 'awaiting_permission' || attention === 'awaiting_os_permission';
}

/**
 * Whether the agent is mid-turn, so the row shows the spinner and the
 * "Working" chip. `activity` is absent where the agent cannot see it (a Codex,
 * Antigravity or Terminal PTY session, or an older agent): those rows keep the
 * plain status square. A pending permission prompt wins — the turn is blocked
 * on the user, not working.
 */
export function isSessionWorking(
  session: Pick<SessionResponse, 'state' | 'activity' | 'attention'>,
): boolean {
  return (
    session.state === 'running' &&
    session.activity === 'working' &&
    !needsPermission(session.attention)
  );
}

/**
 * The row's chip: "Working" while the agent is mid-turn, otherwise the
 * attention pill. Working outranks a standing "Ready" — that alert was for an
 * earlier turn, and it shows again when this one ends.
 */
export function sessionStatusChip(
  session: Pick<SessionResponse, 'state' | 'activity' | 'attention'>,
): AttentionChip | null {
  if (isSessionWorking(session)) return { text: 'Working', tone: 'working' };
  return attentionChip(session.attention);
}

export function canStopSession(state: string): boolean {
  return state === 'running' || state === 'starting';
}

/**
 * Where to go after stopping the session on screen: the closest open session
 * to the stopped row — below first, then above, widening outward. Positions
 * come from `snapshot`, the list as the user saw it when confirming the stop,
 * because the agent's push can drop the stopped row before the stop resolves;
 * a candidate must still be open in `current`. `null` means none is left.
 */
export function nearestOpenSession(
  snapshot: readonly SessionResponse[],
  current: readonly SessionResponse[],
  stoppedId: string,
): string | null {
  const index = snapshot.findIndex((session) => session.session_id === stoppedId);
  if (index < 0) return null;
  const isOpen = (id: string) =>
    current.some((session) => session.session_id === id && canStopSession(session.state));
  for (let distance = 1; distance < snapshot.length; distance++) {
    for (const candidate of [snapshot[index + distance], snapshot[index - distance]]) {
      if (candidate !== undefined && isOpen(candidate.session_id)) return candidate.session_id;
    }
  }
  return null;
}

/** `custom_<id>` → display name, for the kind-label fallback. */
export function customTypeNameMap(
  types: readonly { id: string; name: string }[] | undefined,
): ReadonlyMap<string, string> {
  return new Map((types ?? []).map((type) => [`custom_${type.id}`, type.name]));
}
