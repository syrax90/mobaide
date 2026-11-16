/**
 * Alerts for agents in Herdr panes: the `herdr.attention` counterpart of
 * `attentionNotifications.ts`, with the same two halves.
 *
 * Posting is edge-triggered from the **user-events** channel, so a browser is
 * alerted for every agent of the account. Clearing reads `herdr.changed` on the
 * agent channel: a pane that left `blocked`, `done` and `failed` no longer needs
 * anyone.
 *
 * A pane has no session until someone opens it, so an alert is identified by
 * `herdrPaneTag` and a click goes to the pane route.
 */
import type { EventEnvelope } from '../protocol/dto/envelope';
import { herdrAttentionSchema, herdrSnapshotSchema } from '../protocol/dto/herdr';
import { herdrPanePath, herdrPaneTag } from '../routes/herdr/herdrPath';
import { useNotificationPrefsStore } from '../stores/notificationPrefsStore';
import { useToastStore } from '../stores/toastStore';
import { useVisibleSessionStore } from '../stores/visibleSessionStore';
import { attentionContent } from './attentionContent';
import type { AttentionOutcome } from './attentionNotifications';
import { attentionDelivery } from './attentionSuppression';
import {
  closeAttentionNotification,
  notificationPermission,
  postAttentionNotification,
} from './notificationService';

/** The statuses that still want the user: their alert stays up. */
const NEEDS_USER: readonly string[] = ['blocked', 'done', 'failed'];

export function handleHerdrAttentionEvent(envelope: EventEnvelope): AttentionOutcome {
  if (envelope.type !== 'herdr.attention') return 'ignored';
  const decoded = herdrAttentionSchema.safeParse(envelope.payload);
  if (!decoded.success) return 'ignored';
  const payload = decoded.data;
  // The server stamps `agent_id`; without it there is no pane to open.
  if (payload.agent_id === undefined || payload.agent_id === '') return 'ignored';

  const content = attentionContent({
    reason: payload.reason,
    title: payload.title,
    kind: 'herdr',
  });
  if (content === null) return 'ignored';

  const tag = herdrPaneTag(payload.agent_id, payload.server_id, payload.pane_id);
  const delivery = attentionDelivery({
    documentHidden: document.hidden,
    visibleSessionId: useVisibleSessionStore.getState().visibleSessionId,
    eventSessionId: tag,
    enabled: useNotificationPrefsStore.getState().enabled,
    permission: notificationPermission(),
  });

  switch (delivery) {
    case 'system':
      return postAttentionNotification({
        sessionId: tag,
        agentId: payload.agent_id,
        title: content.title,
        body: content.body,
        path: herdrPanePath(payload.agent_id, payload.server_id, payload.pane_id),
      })
        ? 'system'
        : 'suppressed';
    case 'toast':
      useToastStore.getState().show(`${content.title} — ${content.body}`, 'info');
      return 'toast';
    default:
      return 'suppressed';
  }
}

/** Dismiss the alerts of the panes that no longer need the user. */
export function dismissResolvedHerdrAttention(agentId: string, envelope: EventEnvelope): void {
  if (envelope.type !== 'herdr.changed') return;
  const decoded = herdrSnapshotSchema.safeParse(envelope.payload);
  if (!decoded.success) return;
  for (const server of decoded.data.servers) {
    for (const workspace of server.workspaces) {
      for (const tab of workspace.tabs) {
        for (const pane of tab.panes) {
          if (pane.status === undefined || !NEEDS_USER.includes(pane.status)) {
            closeAttentionNotification(herdrPaneTag(agentId, server.server_id, pane.pane_id));
          }
        }
      }
    }
  }
}
