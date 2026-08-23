/**
 * The agent's `GET /api/version`, pinned by
 * `tests/protocol_fixtures/dto/agent/VersionInfoResponse.json`.
 *
 * Unlike most agent endpoints this one is declared as a bare `dict[str, object]`
 * (`agent/app.py::version_info`), so `openapi-typescript` generates
 * `{ [key: string]: unknown }` and the generated types pin nothing. Hand-written
 * Zod is the only description of the shape the mode picker reads.
 */
import { z } from 'zod';
import { optionalWireString } from '../wireSchema';

export const agentVersionSchema = z.looseObject({
  name: z.string(),
  server_version: z.string(),
  protocol_versions: z.array(z.number()),
  min_client_version: z.number(),
  /**
   * Free-form by design: `{cli: {<id>: {available, models}}}` today, plus
   * whatever a newer agent adds. Kept as `unknown` values and walked
   * defensively rather than modelled, so a shape change degrades to "unknown"
   * instead of failing the whole probe.
   */
  capabilities: z
    .record(z.string(), z.unknown())
    .nullish()
    .transform((value) => value ?? {}),
  latest_agent_version: optionalWireString(),
});

export type AgentVersionInfo = z.infer<typeof agentVersionSchema>;

function cliEntry(info: AgentVersionInfo | undefined, cliId: string): unknown {
  if (info === undefined) return undefined;
  const cli = info.capabilities.cli;
  if (typeof cli !== 'object' || cli === null) return undefined;
  // `Object.hasOwn`, not a bare index: `cliId` reaches here from a URL search
  // param, and `constructor` / `toString` would otherwise resolve to inherited
  // functions rather than being treated as the unknown CLI they are.
  return Object.hasOwn(cli, cliId) ? (cli as Record<string, unknown>)[cliId] : undefined;
}

/**
 * Whether the agent reports this CLI as installed.
 *
 * `null` means "unknown" — an absent probe, an absent CLI entry, or a shape the
 * schema could not read. Callers treat unknown as **enabled**: only an explicit
 * `false` disables a mode card, matching `CreateSessionViewModel.claudeEnabled`
 * and iOS's `reason(for:)`. A newer agent that stops reporting a CLI must not
 * grey out a launch that would have worked.
 */
export function cliAvailable(info: AgentVersionInfo | undefined, cliId: string): boolean | null {
  const entry = cliEntry(info, cliId);
  if (typeof entry !== 'object' || entry === null) return null;
  const available = (entry as { available?: unknown }).available;
  if (typeof available === 'boolean') return available;
  // iOS tolerates the stringified form (`VersionDto.swift`); so do we.
  if (available === 'true') return true;
  if (available === 'false') return false;
  return null;
}

/**
 * The models the agent advertises for a CLI, or `null` to use the client's
 * hardcoded list.
 *
 * An **empty** array is deliberately `null` too: an agent that knows of no
 * models for a CLI has told us nothing useful, and an empty picker would be
 * worse than the built-in fallback.
 */
export function cliModels(
  info: AgentVersionInfo | undefined,
  cliId: string,
): readonly string[] | null {
  const entry = cliEntry(info, cliId);
  if (typeof entry !== 'object' || entry === null) return null;
  const models = (entry as { models?: unknown }).models;
  if (!Array.isArray(models)) return null;
  const names = models
    .map((model) => (typeof model === 'string' ? model : typeof model === 'number' ? String(model) : null))
    .filter((model): model is string => model !== null && model !== '');
  return names.length > 0 ? names : null;
}

/**
 * Whether the agent's startup model enumeration was still running when this
 * response was built — the absence of `models`/`model_details` is then
 * provisional, not authoritative: callers keep persisted catalog selections
 * instead of reconciling them away, and may re-probe. Absent key (old agents,
 * finished warmup) or any unreadable shape reads as `false`.
 */
export function cliModelsPending(info: AgentVersionInfo | undefined, cliId: string): boolean {
  const entry = cliEntry(info, cliId);
  if (typeof entry !== 'object' || entry === null) return false;
  return (entry as { models_pending?: unknown }).models_pending === true;
}

export interface CliModelDetail {
  readonly id: string;
  readonly efforts: readonly string[];
  readonly defaultEffort: string | null;
}

/**
 * Per-model reasoning-effort capabilities the agent advertises for a CLI, or
 * `null` to use the client's hardcoded effort list.
 *
 * Same posture as {@link cliModels}: entries that don't parse (blank id, no
 * usable efforts) are dropped rather than failing the walk, and an empty
 * result is `null` — nothing useful was advertised.
 */
export function cliModelDetails(
  info: AgentVersionInfo | undefined,
  cliId: string,
): readonly CliModelDetail[] | null {
  const entry = cliEntry(info, cliId);
  if (typeof entry !== 'object' || entry === null) return null;
  const details = (entry as { model_details?: unknown }).model_details;
  if (!Array.isArray(details)) return null;
  const parsed: CliModelDetail[] = [];
  for (const detail of details) {
    if (typeof detail !== 'object' || detail === null) continue;
    const { id, efforts, default_effort } = detail as {
      id?: unknown;
      efforts?: unknown;
      default_effort?: unknown;
    };
    if (typeof id !== 'string' || id === '') continue;
    if (!Array.isArray(efforts)) continue;
    const effortNames = efforts.filter(
      (effort): effort is string => typeof effort === 'string' && effort !== '',
    );
    if (effortNames.length === 0) continue;
    parsed.push({
      id,
      efforts: effortNames,
      defaultEffort: typeof default_effort === 'string' && default_effort !== '' ? default_effort : null,
    });
  }
  return parsed.length > 0 ? parsed : null;
}
