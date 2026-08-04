import { describe, expect, it } from 'vitest';
import {
  agentVersionSchema,
  cliAvailable,
  cliModels,
  type AgentVersionInfo,
} from './agentVersion';

const info = (capabilities: unknown): AgentVersionInfo =>
  agentVersionSchema.parse({
    name: 'mobaide-agent',
    server_version: '1.4.0',
    protocol_versions: [7],
    min_client_version: 1,
    capabilities,
  });

describe('agentVersionSchema', () => {
  it('materialises absent capabilities as an empty block', () => {
    expect(info(null).capabilities).toEqual({});
    expect(agentVersionSchema.safeParse({ name: 'x' }).success).toBe(false);
  });
});

describe('cliAvailable', () => {
  it('reports the boolean the agent sent', () => {
    const probe = info({ cli: { claude: { available: true }, codex: { available: false } } });

    expect(cliAvailable(probe, 'claude')).toBe(true);
    expect(cliAvailable(probe, 'codex')).toBe(false);
  });

  it('tolerates the stringified form iOS accepts', () => {
    const probe = info({ cli: { claude: { available: 'true' }, codex: { available: 'false' } } });

    expect(cliAvailable(probe, 'claude')).toBe(true);
    expect(cliAvailable(probe, 'codex')).toBe(false);
  });

  const UNKNOWN: [string, unknown][] = [
    ['no probe at all', undefined],
    ['no capabilities', {}],
    ['a cli block of the wrong shape', { cli: 'yes' }],
    ['an unlisted cli', { cli: { codex: { available: true } }, extra: 1 }],
    ['an unreadable available field', { cli: { claude: { available: 1 } } }],
    ['no available field', { cli: { claude: { models: ['opus'] } } }],
  ];

  it.each(UNKNOWN)('answers unknown for %s, which callers treat as enabled', (_label, capabilities) => {
    const probe = capabilities === undefined ? undefined : info(capabilities);

    expect(cliAvailable(probe, 'claude')).toBeNull();
  });

  it('does not mistake an inherited object key for a cli entry', () => {
    // `cliId` reaches here from a URL search param.
    expect(cliAvailable(info({ cli: {} }), 'constructor')).toBeNull();
  });
});

describe('cliModels', () => {
  it('returns the advertised names, coercing the numeric ones', () => {
    const probe = info({ cli: { claude: { models: ['opus', 4.6, '', 'haiku'] } } });

    expect(cliModels(probe, 'claude')).toEqual(['opus', '4.6', 'haiku']);
  });

  it('falls back to the hardcoded list when the agent knows of none', () => {
    // An empty picker is worse than the client's own defaults.
    expect(cliModels(info({ cli: { claude: { models: [] } } }), 'claude')).toBeNull();
    expect(cliModels(info({ cli: { claude: { models: ['', null] } } }), 'claude')).toBeNull();
    expect(cliModels(info({ cli: { claude: { models: 'opus' } } }), 'claude')).toBeNull();
    expect(cliModels(info({ cli: { claude: { available: true } } }), 'claude')).toBeNull();
    expect(cliModels(undefined, 'claude')).toBeNull();
  });
});
