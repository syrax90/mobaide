// @vitest-environment node
import { describe, expect, it } from 'vitest';
import { COVERED_FIXTURES, DEFERRED_FIXTURES } from './__fixtures__/coverage';
import { listFixtureFiles } from './__fixtures__/load';

/**
 * The bidirectional gate, ported from iOS `DTOFixtureTests`.
 *
 * iOS asserts every fixture maps to a registered type and every registered type
 * has a fixture. TypeScript cannot claim all 80 DTOs yet, so the second half is
 * expressed as an explicit deferral list: a fixture file that is neither
 * asserted nor assigned an owner fails here, which is what stops a new shared
 * fixture from landing with no TypeScript arm noticing.
 */
describe('fixture coverage', () => {
  const onDisk = listFixtureFiles();

  it('finds the fixture tree', () => {
    expect(onDisk.length).toBeGreaterThan(0);
  });

  it('classifies every fixture file exactly once', () => {
    const covered = new Set(COVERED_FIXTURES);
    const deferred = new Set(Object.keys(DEFERRED_FIXTURES));

    const overlap = [...covered].filter((path) => deferred.has(path));
    expect(overlap, 'a fixture cannot be both covered and deferred').toEqual([]);

    const classified = [...covered, ...deferred].sort();
    const unclassified = onDisk.filter((path) => !covered.has(path) && !deferred.has(path));
    const missing = classified.filter((path) => !onDisk.includes(path));

    expect(
      unclassified,
      'new fixture files must be added to COVERED_FIXTURES or DEFERRED_FIXTURES',
    ).toEqual([]);
    expect(missing, 'these classified fixtures no longer exist on disk').toEqual([]);
    expect(classified).toEqual([...onDisk].sort());
  });

  it('names an owner for every deferred fixture', () => {
    for (const [path, owner] of Object.entries(DEFERRED_FIXTURES)) {
      expect(owner, `${path} has no owner`).not.toBe('');
    }
  });
});
