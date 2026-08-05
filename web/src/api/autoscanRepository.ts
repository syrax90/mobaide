import { createAgentClient } from './clients';
import { toApiError } from './errors';
import type { components } from './generated/agent';
import {
  autoScanListResponseSchema,
  type AutoScanListResponse,
} from '../protocol/dto/autoscan';

export type StartScanRequest = components['schemas']['StartScanRequest'];
export type RunNowResponse = components['schemas']['RunNowResponse'];

/**
 * Start an Auto Scan for a project.
 *
 * The success body is deliberately discarded. The direct-REST handler answers
 * `{"message": "..."}` but the relay tunnel — the only path a browser takes —
 * answers with an `AutoScanInstanceDto` instead (`relay/client.py`), so nothing
 * about it is portable enough to show. The caller authors its own confirmation.
 */
export async function startAutoScan(
  agentId: string,
  request: StartScanRequest,
): Promise<void> {
  const { error, response } = await createAgentClient(agentId).POST('/api/autoscan/start', {
    body: request,
  });
  if (error !== undefined) throw toApiError(response, error);
}

/**
 * The agent's running scans, plus what could still be started.
 *
 * Decoded through the Zod mirror rather than trusted as the generated type,
 * because the same shape arrives over the events channel as
 * `autoscan.list.changed` and both writers have to agree — and because the relay
 * answers with a partial body when the agent has no Auto Scan manager at all,
 * which the generated type calls required.
 */
export async function listAutoScans(agentId: string): Promise<AutoScanListResponse> {
  const { data, error, response } = await createAgentClient(agentId).GET(
    '/api/autoscan/list',
  );
  if (error !== undefined || data === undefined) throw toApiError(response, error);
  return autoScanListResponseSchema.parse(data);
}

/**
 * Stop one project's scan.
 *
 * The body is discarded for the same reason as `startAutoScan`: direct REST
 * answers `{"message": …}` and the relay answers `{"status": "stopped"}`. A 404
 * means the scan was already gone, which the caller surfaces as a conflict
 * rather than as failure.
 */
export async function stopAutoScan(agentId: string, projectName: string): Promise<void> {
  const { error, response } = await createAgentClient(agentId).POST('/api/autoscan/stop', {
    body: { project_name: projectName },
  });
  if (error !== undefined) throw toApiError(response, error);
}

/**
 * Ask a scan to run its next cycle immediately.
 *
 * Unlike start and stop this shape *is* portable — the relay forwards the
 * handler's own `RunNowResponse` verbatim. Callers currently reconcile through
 * the list cache rather than reading `next_scan_at` off it; iOS instead writes
 * the value straight into its detail model, which is the parity gap to close if
 * the countdown's lag after Run now ever matters.
 */
export async function runAutoScanNow(
  agentId: string,
  projectName: string,
): Promise<RunNowResponse> {
  const { data, error, response } = await createAgentClient(agentId).POST(
    '/api/autoscan/run-now',
    { body: { project_name: projectName } },
  );
  if (error !== undefined || data === undefined) throw toApiError(response, error);
  return data;
}
