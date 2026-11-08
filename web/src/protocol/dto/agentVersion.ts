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

export interface GitCapabilities {
  stage: boolean;
  discard: boolean;
  commit: boolean;
  fetch: boolean;
  pull: boolean;
  push: boolean;
  /** The project Git screen's remote rows, New branch and Check out (#677). */
  branches: boolean;
  /** *Rebase…* and the Changes screen's Continue / Skip / Abort. */
  rebase: boolean;
  /** `stage` refuses a conflicted file that still holds markers, so *Mark resolved* is safe. */
  resolve: boolean;
  /** The project Git screen may delete remote-tracking rows (on the remote). */
  deleteRemote: boolean;
  /** The conflict resolver's routes and `resolved_paths` on status. */
  conflicts: boolean;
}

export const NO_GIT_CAPABILITIES: GitCapabilities = {
  stage: false,
  discard: false,
  commit: false,
  fetch: false,
  pull: false,
  push: false,
  branches: false,
  rebase: false,
  resolve: false,
  deleteRemote: false,
  conflicts: false,
};

/** The *Fetch / Pull / Push…* entry and the Sync button exist on any of the three. */
export function hasGitSync(capabilities: GitCapabilities): boolean {
  return capabilities.fetch || capabilities.pull || capabilities.push;
}

/**
 * Whether the agent advertises one `capabilities.git.<key>` as `true`.
 *
 * The opposite default from `cliAvailable`: a mutating control is shown only
 * on an explicit `true`, so an older agent (or an unreadable probe) keeps the
 * Changes viewer read-only rather than offering a Stage that would 404.
 */
export function gitCapability(info: AgentVersionInfo | undefined, key: string): boolean {
  if (info === undefined) return false;
  const git = info.capabilities.git;
  if (typeof git !== 'object' || git === null) return false;
  return Object.hasOwn(git, key) && (git as Record<string, unknown>)[key] === true;
}

/** `capabilities.voice`: what dictation needs to know before it starts. */
export interface VoiceCapability {
  sampleRate: number;
  maxSeconds: number;
  maxChunkBytes: number;
}

function positiveInteger(value: unknown): number | null {
  return typeof value === 'number' && Number.isInteger(value) && value > 0 ? value : null;
}

/**
 * The agent's voice input limits, or `null` when it does not offer voice.
 * Present whatever the install state (`GET /api/voice/status` tells that), so
 * `null` means "an agent too old for voice" and hides the mic entirely.
 */
export function voiceCapability(info: AgentVersionInfo | undefined): VoiceCapability | null {
  if (info === undefined) return null;
  const voice = info.capabilities.voice;
  if (typeof voice !== 'object' || voice === null) return null;
  const fields = voice as Record<string, unknown>;
  if (fields.dictation !== true) return null;
  const sampleRate = positiveInteger(fields.sample_rate);
  const maxSeconds = positiveInteger(fields.max_seconds);
  const maxChunkBytes = positiveInteger(fields.max_chunk_bytes);
  if (sampleRate === null || maxSeconds === null || maxChunkBytes === null) return null;
  return { sampleRate, maxSeconds, maxChunkBytes };
}

/** `capabilities.fs.<key>`, with `gitCapability`'s explicit-`true` rule. */
export function fsCapability(info: AgentVersionInfo | undefined, key: string): boolean {
  if (info === undefined) return false;
  const fs = info.capabilities.fs;
  if (typeof fs !== 'object' || fs === null) return false;
  return Object.hasOwn(fs, key) && (fs as Record<string, unknown>)[key] === true;
}

export function gitCapabilities(info: AgentVersionInfo | undefined): GitCapabilities {
  return {
    stage: gitCapability(info, 'stage'),
    discard: gitCapability(info, 'discard'),
    commit: gitCapability(info, 'commit'),
    fetch: gitCapability(info, 'fetch'),
    pull: gitCapability(info, 'pull'),
    push: gitCapability(info, 'push'),
    branches: gitCapability(info, 'branches'),
    rebase: gitCapability(info, 'rebase'),
    resolve: gitCapability(info, 'resolve'),
    deleteRemote: gitCapability(info, 'delete_remote'),
    conflicts: gitCapability(info, 'conflicts'),
  };
}

/**
 * Whether an agent kind can start in the chat UI on this PC (`capabilities.chat`).
 *
 * `unknown` is a probe that has not answered (or failed): callers let the
 * server decide then, but never send `chat` once they know it is unavailable.
 * `outdated` is an agent with no chat support at all; `unavailable` is one
 * that knows chat but cannot run this kind (no Node.js on the PC).
 */
export type ChatSupport =
  | { kind: 'unknown' }
  | { kind: 'available'; resume: boolean; live: boolean }
  | { kind: 'outdated' }
  | { kind: 'unavailable' };

export function chatSupport(info: AgentVersionInfo | undefined, agentKind: string): ChatSupport {
  if (info === undefined) return { kind: 'unknown' };
  const chat = info.capabilities.chat;
  if (typeof chat !== 'object' || chat === null || Array.isArray(chat)) {
    return { kind: 'outdated' };
  }
  if (!Object.hasOwn(chat, agentKind)) return { kind: 'unavailable' };
  const entry = (chat as Record<string, unknown>)[agentKind];
  const flag = (key: 'resume' | 'live') =>
    typeof entry === 'object' && entry !== null && (entry as Record<string, unknown>)[key] === true;
  return { kind: 'available', resume: flag('resume'), live: flag('live') };
}
