import { QueryClient } from '@tanstack/react-query';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import { queryKeys } from '../lib/queryKeys';
import type { SessionModeOrder } from '../protocol/dto/userEvents';
import {
  applySessionModeOrderEvent,
  newerSessionModeOrder,
  writeSessionModeOrder,
} from './sessionModeOrderCache';

const key = queryKeys.sessionModeOrder();

const snapshot = (version: number, order: string[]): SessionModeOrder => ({
  order,
  version,
  updated_at: `2026-11-04T10:00:0${String(version)}Z`,
});

const cached = (client: QueryClient): SessionModeOrder | undefined =>
  client.getQueryData<SessionModeOrder>(key);

let client: QueryClient;

beforeEach(() => {
  client = new QueryClient();
});

afterEach(() => {
  client.clear();
});

describe('newerSessionModeOrder', () => {

  it('keeps the cached snapshot when it is newer', () => {
    const current = snapshot(3, ['shell']);
    expect(newerSessionModeOrder(current, snapshot(2, ['codex']))).toBe(current);
  });

  it('takes the incoming snapshot on a version tie', () => {
    const incoming = snapshot(2, ['codex']);
    expect(newerSessionModeOrder(snapshot(2, ['codex']), incoming)).toBe(incoming);
  });
});

describe('writeSessionModeOrder', () => {
  it('seeds a cache nothing has fetched', () => {
    expect(writeSessionModeOrder(client, snapshot(1, ['codex', 'claude']))).toBe(true);

    expect(cached(client)?.order).toEqual(['codex', 'claude']);
  });

  it('keeps a newer cached order when an older answer lands second', () => {
    // The push and the PUT response to the same save race; so do two saves.
    writeSessionModeOrder(client, snapshot(3, ['shell']));

    expect(writeSessionModeOrder(client, snapshot(2, ['codex']))).toBe(false);
    expect(cached(client)).toEqual(snapshot(3, ['shell']));
  });
});

describe('applySessionModeOrderEvent', () => {
  it('writes a newer push into the cache', () => {
    writeSessionModeOrder(client, snapshot(1, ['claude']));

    expect(
      applySessionModeOrderEvent(client, {
        order: ['shell', 'claude'],
        version: 2,
        updated_at: '2026-11-04T10:00:02Z',
      }),
    ).toBe('applied');
    expect(cached(client)?.order).toEqual(['shell', 'claude']);
  });

  it('reports a push older than the cache as stale and keeps the cache', () => {
    writeSessionModeOrder(client, snapshot(4, ['codex']));

    expect(applySessionModeOrderEvent(client, snapshot(3, ['shell']))).toBe('stale');
    expect(cached(client)?.order).toEqual(['codex']);
  });

  it('drops a malformed payload instead of throwing inside the socket listener', () => {
    writeSessionModeOrder(client, snapshot(1, ['codex']));

    expect(applySessionModeOrderEvent(client, { order: 'codex', version: 2 })).toBe('dropped');
    expect(applySessionModeOrderEvent(client, { order: ['codex'] })).toBe('dropped');
    expect(applySessionModeOrderEvent(client, null)).toBe('dropped');
    expect(cached(client)).toEqual(snapshot(1, ['codex']));
  });
});
