import { beforeEach, describe, expect, it } from 'vitest';
import { stubLocalStorage } from '../test/localStorageStub';

const storage = stubLocalStorage();

const { adoptSessionDeviceId, getDeviceId, resetDeviceIdCache } = await import(
  './deviceIdentity'
);

const DEVICE_ID_KEY = 'mobaide-device-id';
const STORED = '11111111-1111-4111-8111-111111111111';
const BOUND = '22222222-2222-4222-8222-222222222222';

function base64Url(value: string): string {
  return btoa(value).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '');
}

/** A JWT with only the parts the client parses — the signature is never read. */
function jwt(claims: Record<string, unknown>): string {
  return `${base64Url('{"alg":"HS256"}')}.${base64Url(JSON.stringify(claims))}.sig`;
}

beforeEach(() => {
  storage.clear();
  storage.set(DEVICE_ID_KEY, STORED);
  resetDeviceIdCache();
});

describe('adoptSessionDeviceId', () => {
  it("takes the id the session is bound to, so minting matches the server's binding", () => {
    // Safari's ITP wiped the stored id while the refresh cookie survived: the
    // restored session is still bound to the id it was signed in with.
    expect(getDeviceId()).toBe(STORED);

    adoptSessionDeviceId(jwt({ sub: 'user-1', device_id: BOUND }));

    expect(getDeviceId()).toBe(BOUND);
    expect(storage.get(DEVICE_ID_KEY)).toBe(BOUND);
  });

  it('keeps the stored id for a session bound to no device', () => {
    adoptSessionDeviceId(jwt({ sub: 'user-1' }));

    expect(getDeviceId()).toBe(STORED);
  });

  it('ignores a claim that is not a device id, and a token it cannot read', () => {
    adoptSessionDeviceId(jwt({ sub: 'user-1', device_id: 'not-a-uuid' }));
    adoptSessionDeviceId('garbage');

    expect(getDeviceId()).toBe(STORED);
    expect(storage.get(DEVICE_ID_KEY)).toBe(STORED);
  });
});
