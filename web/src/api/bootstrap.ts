/**
 * The anonymous startup probe: `GET /api/version`.
 *
 * One call covers two needs — the protocol version gate, and the Google client
 * id the sign-in button initialises with. The endpoint is untyped in the
 * OpenAPI schema (`dict[str, object]`), so the shape is pinned here with Zod
 * rather than trusted from the generated types.
 */
import { z } from 'zod';
import type { IncompatibleServerInfo } from '../auth/authState';

/**
 * The relay envelope protocol this client speaks.
 *
 * Matches Android's `APP_PROTOCOL_VERSION` and iOS's `ProtocolVersion.app`.
 * The frame types themselves arrive with WEB-04; declaring the version now is
 * what makes the gate below meaningful, and keeps the three clients on one
 * number.
 */
export const APP_PROTOCOL_VERSION = 7;

const versionSchema = z.object({
  server_version: z.string().optional(),
  protocol_versions: z.array(z.number()).default([]),
  min_client_version: z.number().default(0),
  google_client_id: z.string().nullish(),
});

export interface ServerInfo {
  serverVersion: string | null;
  protocolVersions: number[];
  minClientVersion: number;
  googleClientId: string | null;
}

export async function fetchServerInfo(): Promise<ServerInfo> {
  const response = await fetch('/api/version', {
    headers: { Accept: 'application/json' },
  });
  if (!response.ok) {
    throw new Error(`Version probe failed (${String(response.status)})`);
  }
  const parsed = versionSchema.parse(await response.json());
  return {
    serverVersion: parsed.server_version ?? null,
    protocolVersions: parsed.protocol_versions,
    minClientVersion: parsed.min_client_version,
    googleClientId: parsed.google_client_id ?? null,
  };
}

/**
 * Decide whether this client may talk to the server.
 *
 * Set membership, not a range — the same rule as `VersionGateRepository.kt`
 * and its iOS twin. A server can drop support for an old protocol while still
 * advertising a lower `min_client_version`, so being at or above the minimum
 * is necessary but not sufficient.
 *
 * Returns null when the client is compatible. A probe that throws is *not* a
 * gate failure: an unreachable server must not lock the user out.
 */
export function checkVersionGate(info: ServerInfo): IncompatibleServerInfo | null {
  const compatible =
    APP_PROTOCOL_VERSION >= info.minClientVersion &&
    info.protocolVersions.includes(APP_PROTOCOL_VERSION);
  if (compatible) return null;
  return {
    reason:
      `Server requires protocol v${String(info.minClientVersion)}+, ` +
      `this app speaks v${String(APP_PROTOCOL_VERSION)}. ` +
      `Server protocols: ${info.protocolVersions.join(', ')}.`,
    minClientVersion: info.minClientVersion,
  };
}
