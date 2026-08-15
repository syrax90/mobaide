import { describe, expect, it } from 'vitest';
import { postAuthPath, safeNextPath } from './guards';

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

describe('postAuthPath', () => {
  const SCHEDULED = '2026-09-01T00:00:00+00:00';

  it('sends an account inside its deletion grace window to the screen that cancels', () => {
    // `/account` is the only route that mounts `GraceBanner`. Landing on `/`
    // would make the login screen's `deletionScheduled` notice promise a
    // cancellation the destination does not offer.
    expect(postAuthPath('', SCHEDULED)).toBe('/account');
  });

  it('sends everyone else to the root', () => {
    expect(postAuthPath('', null)).toBe('/');
    expect(postAuthPath('?other=1', null)).toBe('/');
  });

  it('lets a captured deep link win over the grace-window default', () => {
    expect(postAuthPath('?next=%2Fsettings', SCHEDULED)).toBe('/settings');
    expect(postAuthPath('?next=%2Fsettings', null)).toBe('/settings');
  });

  it('keeps the open-redirect defences intact through the wrapper', () => {
    // A rejected target collapses to the root first, so inside the grace window
    // it goes on to `/account`. Either way what must never survive is the
    // attacker's host or a path a browser re-reads as an authority.
    expect(postAuthPath('?next=%2F%2Fevil.example', null)).toBe('/');
    expect(postAuthPath('?next=%2F%2Fevil.example', SCHEDULED)).toBe('/account');
    expect(postAuthPath('?next=https%3A%2F%2Fevil.example%2Fsteal', null)).toBe('/');
    expect(postAuthPath('?next=https%3A%2F%2Fevil.example%2Fsteal', SCHEDULED)).toBe(
      '/account',
    );
    expect(postAuthPath('?next=%2F..%2F%2Fevil.example', null)).toBe('/');
    expect(postAuthPath('?next=%2F..%2F%2Fevil.example', SCHEDULED)).toBe('/account');
  });
});
