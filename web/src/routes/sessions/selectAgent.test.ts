import { describe, expect, it } from 'vitest';
import type { Agent } from '../../api/agentRepository';
import { resolveSelectedAgent } from './selectAgent';

const agent = (agentId: string, online: boolean): Agent => ({
  agentId,
  name: agentId,
  online,
  createdAt: '2026-08-04T10:00:00Z',
  lastSeenAt: null,
  agentVersion: null,
  agentProtocolVersion: null,
});

describe('resolveSelectedAgent', () => {
  it('has nothing to select without agents', () => {
    expect(resolveSelectedAgent([], 'a')).toBeNull();
  });

  it('keeps the stored agent even while it is offline', () => {
    // The stored pick is sticky on purpose: an agent going offline must not
    // silently move the user's list to a different one.
    const agents = [agent('a', false), agent('b', true)];

    expect(resolveSelectedAgent(agents, 'a')).toBe('a');
  });

  it('falls back to the first online agent when the stored id is gone', () => {
    const agents = [agent('a', false), agent('b', true), agent('c', true)];

    expect(resolveSelectedAgent(agents, 'deleted')).toBe('b');
    expect(resolveSelectedAgent(agents, null)).toBe('b');
  });

  it('falls back to the first agent when none is online', () => {
    expect(resolveSelectedAgent([agent('a', false), agent('b', false)], null)).toBe('a');
  });
});
