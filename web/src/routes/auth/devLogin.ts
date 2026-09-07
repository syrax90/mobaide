export interface LoginDefaults {
  email: string;
  password: string;
}

const EMPTY: LoginDefaults = { email: '', password: '' };

/**
 * Manual-stack convenience: `tests/e2e/manual_stack.py up --web` pre-fills the
 * login form with its throwaway account so a tester only has to press Sign In.
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
