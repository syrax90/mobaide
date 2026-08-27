import { describe, expect, it } from 'vitest';
import {
  agentVersionSchema,
  cliAvailable,
  cliModelDetails,
  cliModels,
  cliModelsPending,
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

describe('cliModelsPending', () => {
  it('reads the warming marker, defaulting to false for anything else', () => {
    const probe = info({
      cli: { codex: { available: true, models_pending: true }, claude: { available: true } },
    });

    expect(cliModelsPending(probe, 'codex')).toBe(true);
    // Absent key (old agent / finished warmup), unreadable shapes, and a
    // missing probe all read as "not pending" — the marker is opt-in.
    expect(cliModelsPending(probe, 'claude')).toBe(false);
    expect(cliModelsPending(info({ cli: { codex: { models_pending: 'yes' } } }), 'codex')).toBe(
      false,
    );
    expect(cliModelsPending(info({}), 'codex')).toBe(false);
    expect(cliModelsPending(undefined, 'codex')).toBe(false);
  });
});

describe('cliModelDetails', () => {
  it('parses the advertised per-model effort sets', () => {
    const probe = info({
      cli: {
        codex: {
          model_details: [
            { id: 'gpt-5.6-sol', efforts: ['low', 'high', 'ultra'], default_effort: 'low' },
            { id: 'gpt-5.4-mini', efforts: ['low'] },
          ],
        },
      },
    });

    expect(cliModelDetails(probe, 'codex')).toEqual([
      { id: 'gpt-5.6-sol', efforts: ['low', 'high', 'ultra'], defaultEffort: 'low' },
      { id: 'gpt-5.4-mini', efforts: ['low'], defaultEffort: null },
    ]);
  });

  it('drops malformed entries and salvages the rest', () => {
    const probe = info({
      cli: {
        codex: {
          model_details: [
            'not an object',
            { id: '', efforts: ['low'] },
            { id: 'no-efforts' },
            { id: 'empty-efforts', efforts: [] },
            { id: 'non-string-efforts', efforts: [1, ''] },
            { id: 'ok', efforts: ['medium'], default_effort: '' },
          ],
        },
      },
    });

    expect(cliModelDetails(probe, 'codex')).toEqual([
      { id: 'ok', efforts: ['medium'], defaultEffort: null },
    ]);
  });

  it('answers null when nothing usable was advertised', () => {
    // Same posture as cliModels: absent, empty, or unreadable all mean
    // "use the client's hardcoded effort list".
    expect(cliModelDetails(info({ cli: { codex: { model_details: [] } } }), 'codex')).toBeNull();
    expect(cliModelDetails(info({ cli: { codex: { model_details: 'x' } } }), 'codex')).toBeNull();
    expect(cliModelDetails(info({ cli: { codex: { available: true } } }), 'codex')).toBeNull();
    expect(cliModelDetails(info({ cli: {} }), 'codex')).toBeNull();
    expect(cliModelDetails(undefined, 'codex')).toBeNull();
  });
});
