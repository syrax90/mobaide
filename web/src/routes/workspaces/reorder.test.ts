import { describe, expect, it } from 'vitest';
import { workspaceInfoSchema, type WorkspaceInfo } from '../../protocol/dto/workspaces';
import { moveItem, reconcileOrder } from './reorder';

const workspace = (name: string, order: number): WorkspaceInfo =>
  workspaceInfoSchema.parse({ name, path: `/srv/${name}`, order });

/** Server order — what the repository hands the list after sorting. */
const snapshot = (...names: string[]): WorkspaceInfo[] =>
  names.map((name, index) => workspace(name, index));

const names = (workspaces: readonly WorkspaceInfo[]): string[] =>
  workspaces.map((entry) => entry.name);

describe('moveItem', () => {
  it('moves an item down and up without mutating the input', () => {
    const items = ['a', 'b', 'c'];

    expect(moveItem(items, 0, 2)).toEqual(['b', 'c', 'a']);
    expect(moveItem(items, 2, 0)).toEqual(['c', 'a', 'b']);
    expect(items).toEqual(['a', 'b', 'c']);
  });

  it('returns an unchanged copy when the move cannot happen', () => {
    const items = ['a', 'b', 'c'];

    expect(moveItem(items, 1, 1)).toEqual(items);
    expect(moveItem(items, 0, 3)).toEqual(items);
    expect(moveItem(items, -1, 0)).toEqual(items);
    expect(moveItem([], 0, 0)).toEqual([]);
  });
});

describe('reconcileOrder', () => {
  it('holds the override while the server still reports the old order', () => {
    const result = reconcileOrder(snapshot('a', 'b', 'c'), ['c', 'a', 'b']);

    expect(names(result.workspaces)).toEqual(['c', 'a', 'b']);
    expect(result.pendingOrder).toEqual(['c', 'a', 'b']);
  });

  it('releases the override only once the snapshot itself carries that order', () => {
    const result = reconcileOrder(snapshot('c', 'a', 'b'), ['c', 'a', 'b']);

    expect(names(result.workspaces)).toEqual(['c', 'a', 'b']);
    expect(result.pendingOrder).toBeNull();
  });

  it('keeps holding when the snapshot has the right names in the wrong order', () => {
    // An unrelated `projects.changed` — someone renaming a workspace elsewhere —
    // must not be mistaken for the echo of this reorder.
    const result = reconcileOrder(snapshot('a', 'b'), ['b', 'a']);

    expect(names(result.workspaces)).toEqual(['b', 'a']);
    expect(result.pendingOrder).toEqual(['b', 'a']);
  });

  it('sorts a name the override does not mention last, and keeps holding', () => {
    // A workspace created from another client while the reorder is in flight.
    const result = reconcileOrder(snapshot('a', 'b', 'new'), ['b', 'a']);

    expect(names(result.workspaces)).toEqual(['b', 'a', 'new']);
    expect(result.pendingOrder).toEqual(['b', 'a']);
  });

  it('keeps holding when the snapshot no longer lists a pending name', () => {
    const result = reconcileOrder(snapshot('b', 'a'), ['b', 'a', 'deleted']);

    expect(names(result.workspaces)).toEqual(['b', 'a']);
    expect(result.pendingOrder).toEqual(['b', 'a', 'deleted']);
  });
});
