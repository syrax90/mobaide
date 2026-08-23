import { describe, expect, it } from 'vitest';
import type { BusyHolder } from '../protocol/dto/gitConflict';
import { busyHolderLine, busyHolders, conflictDetailString, isConflict } from './conflicts';
import { toApiError } from './errors';

const conflict = (body: unknown) => toApiError(new Response(null, { status: 409 }), body);

/** Every optional key is present-but-undefined, as `optionalWireString` decodes it. */
const holder = (over: Partial<BusyHolder> & { kind: string }): BusyHolder => ({
  session_id: undefined,
  project_name: undefined,
  cwd: undefined,
  worktree_path: undefined,
  ...over,
});

describe('isConflict', () => {
  it('recognises a 409 and nothing else', () => {
    expect(isConflict(conflict({ detail: 'nope' }))).toBe(true);
    expect(isConflict(toApiError(new Response(null, { status: 404 }), {}))).toBe(false);
    expect(isConflict(new Error('offline'))).toBe(false);
  });
});

describe('conflictDetailString', () => {
  it("returns the agent's human detail verbatim", () => {
    const message = "Cannot delete project 'proj': auto-scan is active";

    expect(conflictDetailString(conflict({ detail: message }))).toBe(message);
  });

  it('falls back to the generic label for an error that is not an API error at all', () => {
    expect(conflictDetailString(new Error('network down'))).toBe('Conflict');
    expect(conflictDetailString(undefined)).toBe('Conflict');
  });

  it('falls back to the normalised message when detail is structured', () => {
    expect(conflictDetailString(conflict({ detail: { error: 'busy', message: 'In use' } }))).toBe(
      'In use',
    );
  });
});

describe('busyHolders', () => {
  it('reads the holders out of a git busy conflict', () => {
    const holders = busyHolders(
      conflict({ detail: { busy_by: [{ kind: 'session', session_id: 's1', cwd: '/wt/x' }] } }),
    );

    expect(holders).toEqual([{ kind: 'session', session_id: 's1', cwd: '/wt/x' }]);
  });

  it('distinguishes "no holders reported" from "not a busy conflict"', () => {
    // `[]` opens an (empty) holders dialog; `null` must report a plain message
    // instead, which is why the two may not collapse into one another.
    expect(busyHolders(conflict({ detail: { busy_by: [] } }))).toEqual([]);
    expect(busyHolders(conflict({ detail: "Cannot delete project 'proj'" }))).toBeNull();
    expect(busyHolders(conflict(null))).toBeNull();
    expect(busyHolders(new Error('network down'))).toBeNull();
  });

  it('survives a holder kind the server does not emit today', () => {
    const holders = busyHolders(conflict({ detail: { busy_by: [{ kind: 'autoscan' }] } }));

    expect(holders?.[0]?.kind).toBe('autoscan');
  });
});

describe('busyHolderLine', () => {
  it('names a session holder by working directory, never by id', () => {
    expect(busyHolderLine(holder({ kind: 'session', session_id: 's1', cwd: '/wt/x' }))).toBe(
      'Session: /wt/x',
    );
  });

  it('names an auto-scan holder by project and worktree', () => {
    expect(
      busyHolderLine(holder({ kind: 'auto_scan', project_name: 'proj', worktree_path: '/wt/x' })),
    ).toBe('Auto-scan proj: /wt/x');
  });

  it('still says something useful for an unknown kind or a missing field', () => {
    expect(busyHolderLine(holder({ kind: 'autoscan', worktree_path: '/wt/x' }))).toBe(
      'autoscan: /wt/x',
    );
    expect(busyHolderLine(holder({ kind: 'session' }))).toBe('Session: ?');
  });
});
