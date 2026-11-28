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
 * exit_reason?, kind?, kind_label?, request_id?, request_tool_kind?, agent_id}`
 * and the channel is
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
import type { z } from 'zod';
import { queryClient } from '../lib/queryClient';
import { queryKeys } from '../lib/queryKeys';
import type { EventEnvelope } from '../protocol/dto/envelope';
import {
  sessionEventSchema,
  sessionsChangedSchema,
  type SessionResponse,
} from '../protocol/dto/session';
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

export type AttentionOutcome = 'system' | 'toast' | 'suppressed' | 'ignored' | 'deferred';

type AttentionPayload = z.infer<typeof sessionAttentionSchema>;

/**
 * How long a permission alert waits for the session row that names its
 * request: that row comes on the agent's own socket, often just after.
 */
export const REQUEST_TITLE_WAIT_MS = 750;

/**
 * Alerts held for their request title, by session. A newer alert of the
 * session replaces a held one, and a held one goes once the session's row
 * named its request and then cleared (an answer from another device).
 */
interface HeldAlert {
  timer: ReturnType<typeof setTimeout>;
  requestId: string;
  /** A row named this request: a later row without attention is its clear. */
  seen: boolean;
}
const held = new Map<string, HeldAlert>();

function dropHeld(sessionId: string): void {
  const alert = held.get(sessionId);
  if (alert === undefined) return;
  clearTimeout(alert.timer);
  held.delete(sessionId);
}

/**
 * Handle one user-events envelope. Returns what it did, for tests and for the
 * caller's own logging; unrelated types are `ignored`. A permission alert
 * whose request title this tab can still learn is `deferred` and delivered
 * after [REQUEST_TITLE_WAIT_MS], with the title when the row came meanwhile.
 */
export function handleAttentionEvent(envelope: EventEnvelope): AttentionOutcome {
  if (envelope.type !== 'session.attention') return 'ignored';
  const decoded = sessionAttentionSchema.safeParse(envelope.payload);
  if (!decoded.success) return 'ignored';

  const payload = decoded.data;
  if (attentionContent(payload) === null) return 'ignored';
  dropHeld(payload.session_id);
  const requestId = payload.request_id;
  if (requestId !== undefined && requestTitle(payload) === undefined && agentSessions(payload)) {
    const alert: HeldAlert = {
      timer: setTimeout(() => {
        if (held.get(payload.session_id) === alert) held.delete(payload.session_id);
        deliverAttention(payload);
      }, REQUEST_TITLE_WAIT_MS),
      requestId,
      seen: false,
    };
    held.set(payload.session_id, alert);
    return 'deferred';
  }
  return deliverAttention(payload);
}

function deliverAttention(payload: AttentionPayload): AttentionOutcome {
  const content = attentionContent({ ...payload, request_title: requestTitle(payload) });
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
 * The title of the chat request an alert is about. The user-events payload
 * never carries it (it passes the server, and an APNs alert is built from it),
 * so it comes from the agent's session list this tab holds — present when that
 * agent is the open one and the row already names the same request.
 */
function requestTitle(payload: AttentionPayload): string | undefined {
  if (payload.request_id === undefined) return undefined;
  const request = rowFor(payload)?.attention_request;
  return request?.id === payload.request_id ? request.title : undefined;
}

/** The agent's session list this tab holds, if that agent is the open one. */
function agentSessions(payload: AttentionPayload): SessionResponse[] | undefined {
  if (payload.agent_id === undefined) return undefined;
  return queryClient.getQueryData<SessionResponse[]>(queryKeys.sessions(payload.agent_id));
}

function rowFor(payload: AttentionPayload): SessionResponse | undefined {
  return agentSessions(payload)?.find((row) => row.session_id === payload.session_id);
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
    followHeld(sessions);
    return;
  }
  if (envelope.type !== 'sessions.changed') return;
  const decoded = sessionsChangedSchema.safeParse(envelope.payload);
  if (!decoded.success) return;
  for (const session of decoded.data.sessions) {
    if (session.attention === undefined) closeAttentionNotification(session.session_id);
    followHeld(session);
  }
}

/**
 * A row without attention clears a held alert only after a row named its
 * request: rows the agent sent before the attention edge can still arrive
 * after the user event.
 */
function followHeld(row: SessionResponse): void {
  const alert = held.get(row.session_id);
  if (alert === undefined) return;
  if (row.attention_request?.id === alert.requestId) alert.seen = true;
  else if (row.attention === undefined && alert.seen) dropHeld(row.session_id);
}

/**
 * Drop every alert this tab posted.
 *
 * Alerts are scoped to an account's agents, so they must go when the session
 * does — the teardown half of the rule iOS spent four review rounds on. Called
 * from `stopUserEvents`, which runs on sign-out and on every account handover.
 */
export function clearAttentionNotifications(): void {
  for (const alert of held.values()) clearTimeout(alert.timer);
  held.clear();
  closeAllAttentionNotifications();
}
