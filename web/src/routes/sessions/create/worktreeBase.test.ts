import { describe, expect, it } from 'vitest';
import type { WorktreeBases } from '../../../api/gitRepository';
import {
  commitAge,
  effectiveWorktreeBase,
  filterWorktreeBases,
  preselectedWorktreeBase,
} from './worktreeBase';

const BASES: WorktreeBases = {
  default: 'origin/main',
  last_used: 'origin/develop',
  refs: [
    { ref: 'origin/main', kind: 'remote', subject: 'Merge', committed_at: '2026-11-08T10:15:00+00:00' },
    { ref: 'origin/develop', kind: 'remote', subject: null, committed_at: null },
    { ref: 'wip/Local', kind: 'local', subject: null, committed_at: null },
  ],
};

describe('preselectedWorktreeBase', () => {
  it('prefers last_used, then default, then nothing', () => {
    expect(preselectedWorktreeBase(BASES)).toBe('origin/develop');
    expect(preselectedWorktreeBase({ ...BASES, last_used: null })).toBe('origin/main');
    expect(preselectedWorktreeBase({ ...BASES, last_used: null, default: null })).toBeNull();
    expect(preselectedWorktreeBase(undefined)).toBeNull();
  });
});

describe('effectiveWorktreeBase', () => {
  it('keeps a manual pick for the same project, including the default row', () => {
    expect(effectiveWorktreeBase({ projectName: 'p', ref: 'wip/Local' }, 'p', BASES)).toBe(
      'wip/Local',
    );
    expect(effectiveWorktreeBase({ projectName: 'p', ref: null }, 'p', BASES)).toBeNull();
  });

  it('ignores a pick made for another project', () => {
    expect(effectiveWorktreeBase({ projectName: 'other', ref: 'wip/Local' }, 'p', BASES)).toBe(
      'origin/develop',
    );
  });

  it('falls back to the preselection when the picked ref is gone', () => {
    expect(effectiveWorktreeBase({ projectName: 'p', ref: 'deleted' }, 'p', BASES)).toBe(
      'origin/develop',
    );
  });

  it('sends the agent default while the list is not loaded', () => {
    expect(effectiveWorktreeBase(null, 'p', undefined)).toBeNull();
    expect(effectiveWorktreeBase(null, null, BASES)).toBeNull();
  });
});

describe('filterWorktreeBases', () => {
  it('matches a case-insensitive substring and keeps the agent order', () => {
    expect(filterWorktreeBases(BASES.refs, 'ORIGIN').map((entry) => entry.ref)).toEqual([
      'origin/main',
      'origin/develop',
    ]);
    expect(filterWorktreeBases(BASES.refs, 'local').map((entry) => entry.ref)).toEqual([
      'wip/Local',
    ]);
    expect(filterWorktreeBases(BASES.refs, '  ')).toHaveLength(3);
  });
});

describe('commitAge', () => {
  it('formats a tip commit time relative to now and drops unusable ones', () => {
    const now = Date.parse('2026-11-11T10:15:00+00:00');
    expect(commitAge('2026-11-08T10:15:00+00:00', now)).toBe('3d ago');
    expect(commitAge(null, now)).toBe('');
    expect(commitAge('2026-11-08T10:15:00', now)).toBe('');
  });
});
