import { describe, expect, it } from 'vitest';
import { discardDevAutoLogin, devLoginDefaults, takeDevAutoLogin } from './devLogin';

describe('the manual-stack login pre-fill', () => {
  it('yields empty fields when no dev credentials were injected', () => {
    // Vitest evaluates vite.config.ts without the manual stack's
    // MOBAIDE_WEB_DEV_LOGIN_* vars, so the define resolves to null -- the same
    // state every production build is in. A regression that leaked credentials
    // into the default bundle would surface here.
    expect(devLoginDefaults()).toEqual({ email: '', password: '' });
  });

  it('never auto-submits without dev credentials', () => {
    expect(takeDevAutoLogin()).toBe(false);
  });

  it('stays off after a manual sign-out', () => {
    discardDevAutoLogin();
    expect(takeDevAutoLogin()).toBe(false);
  });
});
