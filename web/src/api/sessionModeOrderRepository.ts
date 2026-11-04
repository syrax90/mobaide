/**
 * The account's order of the create-session mode picker — central-server
 * axis, shared by every device. Mode ids are stored opaquely; see
 * `applyModeOrder` for how a list that does not name every card is applied.
 */
import { relayClient } from './clients';
import { toApiError } from './errors';
import { sessionModeOrderSchema, type SessionModeOrder } from '../protocol/dto/userEvents';

export type { SessionModeOrder } from '../protocol/dto/userEvents';

export async function getSessionModeOrder(): Promise<SessionModeOrder> {
  const { data, error, response } = await relayClient.GET('/api/session-mode-order');
  if (error !== undefined || data === undefined) {
    throw toApiError(response, error);
  }
  return sessionModeOrderSchema.parse(data);
}

/** Replace the order; the server pushes the result to the other devices. */
export async function saveSessionModeOrder(order: readonly string[]): Promise<SessionModeOrder> {
  const { data, error, response } = await relayClient.PUT('/api/session-mode-order', {
    body: { order: [...order] },
  });
  if (error !== undefined || data === undefined) {
    throw toApiError(response, error);
  }
  return sessionModeOrderSchema.parse(data);
}
