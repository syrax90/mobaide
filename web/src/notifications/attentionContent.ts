/**
 * Attention reason → notification copy.
 *
 * A cross-client contract, not a web string table: the same five pairs are
 * spelled in `ios/Mobaide/Notifications/AttentionContent.swift`,
 * `android/.../notifications/AttentionNotifier.kt` and
 * `central_server/central_server/push/content.py` (which is what an iPhone gets
 * over APNs). A phone and a browser watching the same session must say the same
 * thing, down to the em dashes, so `attentionContent.test.ts` asserts these
 * literally rather than through a helper.
 *
 * An absent or unrecognised reason returns `null`, and every client treats that
 * as "post nothing" — a newer agent reason must not surface as an empty alert.
 */

/**
 * Used when a session has neither a title nor a project, and the event names no
 * `kind` — an agent too old to send one only alerted for Claude sessions.
 */
export const FALLBACK_LABEL = 'Claude session';

const KIND_FALLBACK_LABELS: Readonly<Record<string, string>> = {
  claude: 'Claude session',
  codex: 'Codex session',
  antigravity: 'Antigravity session',
  opencode: 'OpenCode session',
  copilot: 'GitHub Copilot session',
  pi: 'Pi session',
  auto_scan: 'Auto Scan session',
  shell: 'Terminal session',
};

export interface AttentionSource {
  reason?: string;
  title?: string;
  project_name?: string;
  exit_reason?: string;
  kind?: string;
  kind_label?: string;
}

/** The label for a session with neither a title nor a project. */
function fallbackLabel(source: AttentionSource): string {
  const kind = source.kind;
  if (kind === undefined || kind === '') return FALLBACK_LABEL;
  const known = Object.hasOwn(KIND_FALLBACK_LABELS, kind) ? KIND_FALLBACK_LABELS[kind] : undefined;
  if (known !== undefined) return known;
  const custom = source.kind_label;
  if (custom !== undefined && custom.trim() !== '') return `${custom} session`;
  return 'Agent session';
}

export interface AttentionNotification {
  title: string;
  body: string;
}

/** `title` → `project_name` → the fallback, each only when non-blank. */
export function attentionLabel(source: AttentionSource): string {
  if (source.title !== undefined && source.title.trim() !== '') return source.title;
  if (source.project_name !== undefined && source.project_name.trim() !== '') {
    return source.project_name;
  }
  return fallbackLabel(source);
}

export function attentionContent(source: AttentionSource): AttentionNotification | null {
  const label = attentionLabel(source);
  switch (source.reason) {
    case 'awaiting_permission':
      return { title: 'Permission needed', body: `${label} is waiting for your approval` };
    case 'awaiting_os_permission':
      return {
        title: 'Action needed on your Mac',
        body: `${label} is paused — your Mac is asking to allow file access. Approve it on the Mac to continue.`,
      };
    case 'turn_finished':
      return { title: 'Your move', body: `${label} finished its turn — tap to continue` };
    case 'turn_failed':
      return {
        title: 'Turn failed',
        body: `${label} hit an error — tap to see what happened`,
      };
    case 'ended': {
      // `exit_reason` is the human-readable end cause, and it participates in
      // the title only when it says something: a session can end with none.
      const reason = source.exit_reason;
      const suffix = reason !== undefined && reason.trim() !== '' ? ` (${reason})` : '';
      return { title: `Session ended${suffix}`, body: label };
    }
    default:
      return null;
  }
}
