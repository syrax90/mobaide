import { QueryClient } from '@tanstack/react-query';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import { queryKeys } from '../lib/queryKeys';
import {
  autoScanListResponseSchema,
  type AutoScanListResponse,
} from '../protocol/dto/autoscan';
import { applyAutoScanSnapshot, applyAutoScanStatus } from './autoscanCache';

const AGENT = 'agent-1';
const key = queryKeys.autoscan(AGENT);

function wireScan(projectName: string, overrides: Record<string, unknown> = {}) {
  return {
    project_name: projectName,
    status: 'scanning',
    started_at: 1_751_800_000.5,
    last_activity_at: 1_751_800_005.5,
    ...overrides,
  };
}

function cachedNames(client: QueryClient): string[] {
  return (client.getQueryData<AutoScanListResponse>(key)?.scans ?? []).map(
    (scan) => scan.project_name,
  );
}

let client: QueryClient;

beforeEach(() => {
  client = new QueryClient();
});

afterEach(() => {
  client.clear();
});

describe('applyAutoScanSnapshot', () => {
  it('seeds a cache nothing has fetched', () => {
    expect(
      applyAutoScanSnapshot(client, AGENT, {
        scans: [wireScan('a')],
        capacity: 1,
        max_scans: 5,
        available_projects: ['a', 'b'],
        auto_scan_prompt: 'go',
      }),
    ).toBe('applied');

    const cached = client.getQueryData<AutoScanListResponse>(key);
    expect(cachedNames(client)).toEqual(['a']);
    expect(cached?.available_projects).toEqual(['a', 'b']);
    expect(cached?.auto_scan_prompt).toBe('go');
  });

  it('accepts the partial body the relay sends when no manager is configured', () => {
    // `relay/client.py` answers `{scans: [], capacity: 0, max_scans: 0,
    // available_projects: []}` with no `auto_scan_prompt` at all, and the
    // generated type calls that field required — so the defaults have to hold.
    expect(
      applyAutoScanSnapshot(client, AGENT, {
        scans: [],
        capacity: 0,
        max_scans: 0,
        available_projects: [],
      }),
    ).toBe('applied');

    const cached = client.getQueryData<AutoScanListResponse>(key);
    expect(cached).toMatchObject({ scans: [], capacity: 0, max_scans: 0, available_projects: [] });
    expect(cached?.auto_scan_prompt).toBeUndefined();
  });

  it('drops a payload carrying no scans rather than emptying a good list', () => {
    // Every field of the schema defaults, so `{}` — or an `AutoScanInstanceDto`
    // misrouted to this handler — would otherwise parse to an empty list and
    // make the whole section vanish until the next start or stop.
    applyAutoScanSnapshot(client, AGENT, { scans: [wireScan('a')] });

    expect(applyAutoScanSnapshot(client, AGENT, {})).toBe('dropped');
    expect(applyAutoScanSnapshot(client, AGENT, wireScan('a'))).toBe('dropped');
    expect(applyAutoScanSnapshot(client, AGENT, null)).toBe('dropped');
    expect(cachedNames(client)).toEqual(['a']);
  });

  it('replaces the whole list, because the payload is authoritative', () => {
    applyAutoScanSnapshot(client, AGENT, { scans: [wireScan('gone'), wireScan('kept')] });
    applyAutoScanSnapshot(client, AGENT, { scans: [wireScan('kept')] });

    expect(cachedNames(client)).toEqual(['kept']);
  });

  it('drops a malformed payload instead of throwing inside the socket listener', () => {
    applyAutoScanSnapshot(client, AGENT, { scans: [wireScan('a')] });

    expect(applyAutoScanSnapshot(client, AGENT, { scans: 'nope' })).toBe('dropped');
    expect(applyAutoScanSnapshot(client, AGENT, { scans: [{ status: 'idle' }] })).toBe(
      'dropped',
    );
    expect(cachedNames(client)).toEqual(['a']);
  });

  it('survives a REST list that resolves after it with older rows', async () => {
    let settle: (value: AutoScanListResponse) => void = () => undefined;
    const listed = new Promise<AutoScanListResponse>((resolve) => {
      settle = resolve;
    });
    const fetched = client
      .fetchQuery({ queryKey: key, queryFn: () => listed })
      .catch(() => undefined);

    applyAutoScanSnapshot(client, AGENT, { scans: [wireScan('fresh')] });
    settle(autoScanListResponseSchema.parse({}));
    await fetched;
    await listed;

    expect(cachedNames(client)).toEqual(['fresh']);
  });
});

describe('applyAutoScanStatus', () => {
  beforeEach(() => {
    applyAutoScanSnapshot(client, AGENT, {
      scans: [wireScan('a', { cycle_number: 1 }), wireScan('b')],
      capacity: 2,
      max_scans: 5,
      available_projects: ['a', 'b'],
    });
  });

  it('replaces one row and leaves the rest of the response alone', () => {
    expect(
      applyAutoScanStatus(client, AGENT, wireScan('a', { cycle_number: 9, status: 'error' })),
    ).toBe('applied');

    const cached = client.getQueryData<AutoScanListResponse>(key);
    expect(cached?.scans[0]).toMatchObject({ cycle_number: 9, status: 'error' });
    expect(cached?.scans[1]?.project_name).toBe('b');
    expect(cached?.capacity).toBe(2);
    expect(cached?.available_projects).toEqual(['a', 'b']);
  });

  it('never inserts a scan the list does not already carry', () => {
    // The event has no `capacity` / `max_scans` / `available_projects`, so it
    // cannot complete a row on its own; the `autoscan.list.changed` that every
    // start publishes is what introduces one. Matches iOS.
    expect(applyAutoScanStatus(client, AGENT, wireScan('c'))).toBe('skipped');
    expect(cachedNames(client)).toEqual(['a', 'b']);
  });

  it('is a no-op when nothing is cached yet', () => {
    client.clear();
    expect(applyAutoScanStatus(client, AGENT, wireScan('a'))).toBe('skipped');
    expect(client.getQueryData(key)).toBeUndefined();
  });

  it('keeps precision on a current_seq past 2 ** 53 rather than rejecting it', () => {
    // The same object arrives inside the `ws/autoscan` snapshot, where
    // `parseWireJson` has already promoted the literal to a bigint.
    expect(applyAutoScanStatus(client, AGENT, wireScan('a', { current_seq: 9007199254740993n }))).toBe(
      'applied',
    );
    expect(client.getQueryData<AutoScanListResponse>(key)?.scans[0]?.current_seq).toBe(
      9007199254740992,
    );
  });

  it('drops a malformed payload', () => {
    expect(applyAutoScanStatus(client, AGENT, { status: 'idle' })).toBe('dropped');
    expect(applyAutoScanStatus(client, AGENT, null)).toBe('dropped');
    expect(cachedNames(client)).toEqual(['a', 'b']);
  });
});
