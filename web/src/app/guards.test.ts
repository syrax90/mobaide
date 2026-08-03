import { describe, expect, it } from 'vitest';
import { safeNextPath } from './guards';

describe('safeNextPath', () => {
  it('returns the captured deep link', () => {
    expect(safeNextPath('?next=%2Fdevices')).toBe('/devices');
  });

  it('preserves the query string of the captured link', () => {
    expect(safeNextPath('?next=%2Fpair%3Fcode%3Dabc123')).toBe('/pair?code=abc123');
  });

  it('falls back to the root when no target was captured', () => {
    expect(safeNextPath('')).toBe('/');
    expect(safeNextPath('?other=1')).toBe('/');
  });

  it('rejects an absolute URL', () => {
    expect(safeNextPath('?next=https%3A%2F%2Fevil.example%2Fsteal')).toBe('/');
  });

  it('rejects a protocol-relative URL', () => {
    // `//evil.example` is absolute to the browser even though it starts with
    // a slash — honouring it would make sign-in an open redirect.
    expect(safeNextPath('?next=%2F%2Fevil.example')).toBe('/');
  });

  it('rejects a target whose dot-segments normalise to a protocol-relative path', () => {
    // The parser resolves `..` before serialising, so a rooted target can
    // still come out with two leading slashes — same-origin to the origin
    // check, an authority to pushState/location.assign.
    expect(safeNextPath('?next=%2F..%2F%2Fevil.example')).toBe('/');
    expect(safeNextPath('?next=%2F.%2F%5C%2Fevil.example')).toBe('/');
  });

  it('rejects a bare path with no leading slash', () => {
    expect(safeNextPath('?next=evil.example')).toBe('/');
  });

  it('rejects an empty target', () => {
    expect(safeNextPath('?next=')).toBe('/');
  });
});
