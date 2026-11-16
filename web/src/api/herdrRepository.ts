import { createAgentClient } from './clients';
import { isApiError, toApiError } from './errors';
import type { components } from './generated/agent';
import { herdrSnapshotSchema, type HerdrSnapshot } from '../protocol/dto/herdr';
import { PANE_CLIENT_ID } from '../protocol/paneClientId';

export type HerdrCreateRequest = components['schemas']['HerdrCreateRequest'];
export type HerdrRenameRequest = components['schemas']['HerdrRenameRequest'];
export type HerdrCloseRequest = components['schemas']['HerdrCloseRequest'];
export type HerdrPaneText = components['schemas']['HerdrPaneTextResponse'];

export interface HerdrPaneRef {
  serverId: string;
  paneId: string;
}

/** The pane is closed, or its herdr server stopped. Retrying cannot help. */
export const HERDR_GONE_CODES: readonly string[] = ['pane_not_found', 'server_not_found'];

/** The `detail.code` of a Herdr refusal, or `null` for any other error. */
export function herdrErrorCode(error: unknown): string | null {
  if (!isApiError(error)) return null;
  const detail = error.detail;
  if (typeof detail !== 'object' || detail === null) return null;
  const code = (detail as { code?: unknown }).code;
  return typeof code === 'string' ? code : null;
}

/**
 * The Herdr servers on the agent's computer.
 *
 * Decoded through the Zod mirror, because the same shape arrives over the
 * events channel as `herdr.changed` and both writers have to agree.
 */
export async function fetchHerdr(agentId: string): Promise<HerdrSnapshot> {
  const { data, error, response } = await createAgentClient(agentId).GET('/api/herdr');
  if (error !== undefined || data === undefined) throw toApiError(response, error);
  return herdrSnapshotSchema.parse(data);
}

/** The terminal session that shows a pane; the agent makes it when it is missing. */
export async function openHerdrPane(agentId: string, pane: HerdrPaneRef): Promise<string> {
  const { data, error, response } = await createAgentClient(agentId).POST(
    '/api/herdr/pane/open',
    { body: { server_id: pane.serverId, pane_id: pane.paneId } },
  );
  if (error !== undefined || data === undefined) throw toApiError(response, error);
  return data.session_id;
}

/** Take control of a pane as this tab, or give it back. Only the controller can give it back. */
export async function setHerdrControl(
  agentId: string,
  pane: HerdrPaneRef,
  control: boolean,
): Promise<void> {
  const { error, response } = await createAgentClient(agentId).POST(
    '/api/herdr/pane/control',
    {
      body: {
        server_id: pane.serverId,
        pane_id: pane.paneId,
        control,
        client_id: PANE_CLIENT_ID,
      },
    },
  );
  if (error !== undefined) throw toApiError(response, error);
}

export async function fetchHerdrPaneText(
  agentId: string,
  pane: HerdrPaneRef,
  lines: number,
): Promise<HerdrPaneText> {
  const { data, error, response } = await createAgentClient(agentId).GET(
    '/api/herdr/pane/text',
    { params: { query: { server_id: pane.serverId, pane_id: pane.paneId, lines } } },
  );
  if (error !== undefined || data === undefined) throw toApiError(response, error);
  return data;
}

export interface HerdrCreated {
  paneId: string;
  /** Set when the pane was made and its agent did not start: a message for the user. */
  agentError: string | null;
}

/** Create a workspace, a tab or a split. */
export async function createHerdrTerminal(
  agentId: string,
  request: HerdrCreateRequest,
): Promise<HerdrCreated> {
  const { data, error, response } = await createAgentClient(agentId).POST(
    '/api/herdr/terminals',
    { body: request },
  );
  if (error !== undefined || data === undefined) throw toApiError(response, error);
  return { paneId: data.pane_id, agentError: data.agent_error ?? null };
}

export async function startHerdrAgent(
  agentId: string,
  pane: HerdrPaneRef,
  agent: string,
): Promise<void> {
  const { error, response } = await createAgentClient(agentId).POST('/api/herdr/pane/agent', {
    body: { server_id: pane.serverId, pane_id: pane.paneId, agent },
  });
  if (error !== undefined) throw toApiError(response, error);
}

export async function renameHerdrItem(
  agentId: string,
  request: HerdrRenameRequest,
): Promise<void> {
  const { error, response } = await createAgentClient(agentId).POST('/api/herdr/rename', {
    body: request,
  });
  if (error !== undefined) throw toApiError(response, error);
}

export async function closeHerdrItem(
  agentId: string,
  request: HerdrCloseRequest,
): Promise<void> {
  const { error, response } = await createAgentClient(agentId).POST('/api/herdr/close', {
    body: request,
  });
  if (error !== undefined) throw toApiError(response, error);
}
