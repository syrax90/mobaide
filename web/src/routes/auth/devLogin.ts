export interface LoginDefaults {
  email: string;
  password: string;
}

const EMPTY: LoginDefaults = { email: '', password: '' };

/**
 * Manual-stack convenience: `tests/e2e/manual_stack.py up --web` pre-fills the
 * login form with its throwaway account, and the form submits it once per
 * page load (see takeDevAutoLogin) -- the iOS and Android debug builds sign
 * in the same way.
 *
 * The fill has to happen here rather than from outside the page: the form is
 * react-hook-form with uncontrolled inputs, so an assigned `input.value` would
 * show up but leave the form store empty and fail the submit guard.
 *
 * Two independent gates have to fail before a credential can reach a shipped
 * bundle -- `vite build` bakes `__MOBAIDE_DEV_LOGIN__` as null, and `DEV` is
 * false there anyway.
 */
export function devLoginDefaults(): LoginDefaults {
  if (!import.meta.env.DEV || __MOBAIDE_DEV_LOGIN__ === null) return EMPTY;
  return __MOBAIDE_DEV_LOGIN__;
}

let autoLoginTaken = false;

/**
 * True exactly once per page load when dev credentials are present, and never
 * after a manual sign-out (discardDevAutoLogin, called by the session store):
 * a tab that restored a session reaches /login for the first time only when
 * the user signs out. Module state also absorbs React StrictMode's double-run
 * of mount effects.
 */
export function takeDevAutoLogin(): boolean {
  if (autoLoginTaken || devLoginDefaults().email === '') return false;
  autoLoginTaken = true;
  return true;
}

export function discardDevAutoLogin(): void {
  autoLoginTaken = true;
}
