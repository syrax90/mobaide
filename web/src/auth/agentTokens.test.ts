import { beforeEach, describe, expect, it, vi } from 'vitest';
import { stubLocalStorage } from '../test/localStorageStub';

// Before any persisted store is imported.
stubLocalStorage();

const { clearAgentToken, clearAgentTokens, getAgentToken } = await import('./agentTokens');

const STORAGE_KEY = 'mobaide-agent-tokens';

function pair(agentId: string) {
  return { accessToken: `access-${agentId}`, refreshToken: `refresh-${agentId}` };
}

/** Seed through storage alone, the way a peer tab's rotation arrives. */
function seed(...agentIds: string[]): void {
  const map = Object.fromEntries(agentIds.map((agentId) => [agentId, pair(agentId)]));
  window.localStorage.setItem(STORAGE_KEY, JSON.stringify(map));
}

function storedAgentIds(): string[] {
  const raw = window.localStorage.getItem(STORAGE_KEY);
  if (raw === null) return [];
  const parsed: unknown = JSON.parse(raw);
  return Object.keys(parsed as Record<string, unknown>);
}

beforeEach(() => {
  clearAgentTokens();
});

describe('clearAgentToken', () => {
  it('drops one agent pair and leaves every other agent connected', () => {
    // The close policy calls this per agent on a 4001/4003; taking the whole map
    // down would sign every other agent's channel out of its credential.
    seed('a1', 'a2');

    clearAgentToken('a1');

    expect(getAgentToken('a1')).toBeNull();
    expect(getAgentToken('a2')).toBe('access-a2');
    expect(storedAgentIds()).toEqual(['a2']);
  });

  it('leaves the cache untouched for an agent it never held', () => {
    seed('a1');

    clearAgentToken('unknown-agent');

    expect(getAgentToken('a1')).toBe('access-a1');
  });
});

describe('cross-tab deletion', () => {
  it('does not resurrect a pair a peer tab deleted from shared storage', () => {
    // Storage is the shared surface. Memory used to gap-fill any key missing
    // from it, so a revoked credential came straight back — and a
    // mobile_access JWT minted while its TTL was 0 has no `exp`, so nothing
    // would ever rotate it away again.
    seed('a1', 'a2');
    // A write in this tab, which is what populates its in-memory mirror.
    clearAgentToken('a2');
    expect(getAgentToken('a1')).toBe('access-a1');

    // The peer tab's delete, landing in storage behind this tab's back.
    seed();

    expect(getAgentToken('a1')).toBeNull();
  });
});

describe('when storage refuses writes', () => {
  it('keeps a deletion that only memory could record', () => {
    // Safari-style "reads work, writes throw": the delete lands in memory, and
    // merging stale storage back over it would resurrect the credential.
    // Kept last in the file: once a write throws, the module latches storage as
    // unusable for the rest of the module's life, and Vitest's per-file
    // isolation is what contains that.
    seed('a1', 'a2');
    vi.spyOn(window.localStorage, 'setItem').mockImplementation(() => {
      throw new Error('quota exceeded');
    });

    clearAgentToken('a1');

    expect(getAgentToken('a1')).toBeNull();
    expect(getAgentToken('a2')).toBe('access-a2');
  });
});
