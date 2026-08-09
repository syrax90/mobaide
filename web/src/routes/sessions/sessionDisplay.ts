/**
 * How a session renders on a card. Pure, so the parity rules are testable
 * without a DOM.
 */
import type { SessionResponse } from '../../protocol/dto/session';

const BUILT_IN_KIND_LABELS: Readonly<Record<string, string>> = {
  claude: 'Claude Code',
  codex: 'Codex',
  antigravity: 'Antigravity',
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
  if (session.title !== undefined && session.title !== '') return session.title;
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
  tone: 'permission' | 'ready' | 'ended';
}

/**
 * The attention pill, iOS `SessionListModel.attention`. Android decodes the same
 * field but renders nothing, so this is the behaviour web follows.
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
    case 'ended':
      return { text: 'Ended', tone: 'ended' };
    default:
      return null;
  }
}

export function canStopSession(state: string): boolean {
  return state === 'running' || state === 'starting';
}

/** `custom_<id>` → display name, for the kind-label fallback. */
export function customTypeNameMap(
  types: readonly { id: string; name: string }[] | undefined,
): ReadonlyMap<string, string> {
  return new Map((types ?? []).map((type) => [`custom_${type.id}`, type.name]));
}
