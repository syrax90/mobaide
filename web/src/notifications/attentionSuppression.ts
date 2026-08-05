/**
 * Whether an attention event reaches the user, and how.
 *
 * Both mobile clients share one rule — *notify unless the app is in the
 * foreground and the user is already looking at the very session the event is
 * for* — and web keeps it, with the split a browser makes possible: a visible
 * tab already shows the session list updating, so a system notification there
 * would duplicate what is on screen. An in-app toast covers that case instead.
 *
 * Pure, so the matrix is testable without a DOM or a permission prompt.
 */
export type AttentionDelivery = 'system' | 'toast' | 'none';

export interface AttentionContext {
  /** `document.hidden` — the tab is backgrounded, minimised or on another tab. */
  documentHidden: boolean;
  /** The session whose terminal is on screen, if any. */
  visibleSessionId: string | null;
  eventSessionId: string;
  /** The user's opt-out in Settings. */
  enabled: boolean;
  /** `Notification.permission`, or `unsupported` where the API is absent. */
  permission: 'granted' | 'denied' | 'default' | 'unsupported';
}

export function attentionDelivery(context: AttentionContext): AttentionDelivery {
  if (!context.enabled) return 'none';
  // The shared rule. Deliberately scoped to *this* session: an alert for a
  // different one still fires while its terminal is open, which is most of why
  // the feature exists.
  if (!context.documentHidden && context.visibleSessionId === context.eventSessionId) {
    return 'none';
  }
  // A hidden tab has nowhere to put a toast — the viewport is not on screen and
  // the 5 s auto-dismiss would expire before anyone saw it.
  if (context.documentHidden) {
    return context.permission === 'granted' ? 'system' : 'none';
  }
  return 'toast';
}
