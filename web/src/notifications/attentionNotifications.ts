/**
 * Turns a `session.attention` event into something the user notices.
 *
 * **Edge-triggered only, deliberately.** The `attention` field on a session is
 * *latched* on the agent — cleared only by a client PTY write, a
 * `UserPromptSubmit` hook, or output resuming — so every full-state snapshot
 * (`GET /api/sessions`, `sessions.changed`, `session.updated`) keeps reporting
 * it, and `core/attention_gate.py` warns clients not to re-post from one. This
 * consumes only the one-shot `session.attention` edge, which is what makes
 * Android correct by construction and what iOS had to add a per-session
 * seen-store to emulate (PR #571). A browser tab that was closed shows nothing,
 * which is honest: there is no background delivery to reconcile against.
 *
 * It listens on the **user-events** channel rather than the agent bus. The
 * payload there is the slim `{session_id, reason, title?, project_name?,
 * exit_reason?, kind?, kind_label?, agent_id}` and the channel is
 * account-wide, so a browser — the first client that routinely holds several
 * agents — is alerted about all of them, not just the one whose sessions list
 * is open. Consuming both channels would double-post.
 *
 * Web sends no `device_id` on that socket and registers no push token, so it can
 * never appear in `RoutingTable.get_user_device_ids()` and an open tab never
 * suppresses the APNs push to a phone (`relay/agent_ws.py::_handle_user_event`).
 * That is the whole of the tab-lifecycle contract: the socket stays open when
 * hidden, and the phone still rings.
 */
import type { EventEnvelope } from '../protocol/dto/envelope';
import { sessionEventSchema, sessionsChangedSchema } from '../protocol/dto/session';
import { sessionAttentionSchema } from '../protocol/dto/userEvents';
import { useNotificationPrefsStore } from '../stores/notificationPrefsStore';
import { useToastStore } from '../stores/toastStore';
import { useVisibleSessionStore } from '../stores/visibleSessionStore';
import { attentionContent } from './attentionContent';
import { attentionDelivery } from './attentionSuppression';
import {
  closeAllAttentionNotifications,
  closeAttentionNotification,
  notificationPermission,
  postAttentionNotification,
} from './notificationService';

export type AttentionOutcome = 'system' | 'toast' | 'suppressed' | 'ignored';

/**
 * Handle one user-events envelope. Returns what it did, for tests and for the
 * caller's own logging; unrelated types are `ignored`.
 */
export function handleAttentionEvent(envelope: EventEnvelope): AttentionOutcome {
  if (envelope.type !== 'session.attention') return 'ignored';
  const decoded = sessionAttentionSchema.safeParse(envelope.payload);
  if (!decoded.success) return 'ignored';

  const payload = decoded.data;
  const content = attentionContent(payload);
  // An unrecognised reason posts nothing, and — importantly — cancels nothing.
  // This is not how a *clear* arrives: `relay/client.py::_attention_user_event`
  // returns `None` unless `attention` is a non-empty string, so a cleared
  // attention never reaches this channel at all. The only reachable input here
  // is a reason a newer agent knows and this client does not, where dismissing
  // would kill a live alert the phone is still showing. Both mobile clients
  // return null and leave it alone; clearing is `dismissResolvedAttention`'s job.
  if (content === null) return 'ignored';

  const delivery = attentionDelivery({
    documentHidden: document.hidden,
    visibleSessionId: useVisibleSessionStore.getState().visibleSessionId,
    eventSessionId: payload.session_id,
    enabled: useNotificationPrefsStore.getState().enabled,
    permission: notificationPermission(),
  });

  switch (delivery) {
    case 'system': {
      const post: Parameters<typeof postAttentionNotification>[0] = {
        sessionId: payload.session_id,
        title: content.title,
        body: content.body,
      };
      if (payload.agent_id !== undefined) post.agentId = payload.agent_id;
      return postAttentionNotification(post) ? 'system' : 'suppressed';
    }
    case 'toast':
      useToastStore.getState().show(`${content.title} — ${content.body}`, 'info');
      return 'toast';
    default:
      return 'suppressed';
  }
}

/**
 * Dismiss the alerts for sessions that no longer need anyone.
 *
 * The posting half is edge-triggered, but the *clearing* half cannot be: the
 * agent publishes no "attention resolved" event. What it does publish is the
 * whole session row on `session.updated`, and the whole list on
 * `sessions.changed`, each carrying the latched `attention` field — so an absent
 * one is the clear. Android runs exactly this as a second collector
 * (`SessionEventService.observeAttention` → `notifier.cancel`); without it a
 * notification deep-links forever to a session the user already answered from
 * their phone.
 *
 * Reading the snapshot to *dismiss* is safe in a way reading it to *post* is
 * not — the trap `attention_gate.py` warns about is re-raising an alert from a
 * latched field, and this only ever takes one away.
 */
export function dismissResolvedAttention(envelope: EventEnvelope): void {
  const sessions =
    envelope.type === 'session.updated' || envelope.type === 'session.attention'
      ? sessionEventSchema.safeParse(envelope.payload).data?.session
      : undefined;
  if (sessions !== undefined) {
    if (sessions.attention === undefined) closeAttentionNotification(sessions.session_id);
    return;
  }
  if (envelope.type !== 'sessions.changed') return;
  const decoded = sessionsChangedSchema.safeParse(envelope.payload);
  if (!decoded.success) return;
  for (const session of decoded.data.sessions) {
    if (session.attention === undefined) closeAttentionNotification(session.session_id);
  }
}

/**
 * Drop every alert this tab posted.
 *
 * Alerts are scoped to an account's agents, so they must go when the session
 * does — the teardown half of the rule iOS spent four review rounds on. Called
 * from `stopUserEvents`, which runs on sign-out and on every account handover.
 */
export function clearAttentionNotifications(): void {
  closeAllAttentionNotifications();
}
