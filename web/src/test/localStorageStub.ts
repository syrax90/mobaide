import { vi } from 'vitest';

function memoryStorage(backing: Map<string, string>) {
  return {
    getItem: (key: string) => backing.get(key) ?? null,
    setItem: (key: string, value: string) => {
      backing.set(key, String(value));
    },
    removeItem: (key: string) => {
      backing.delete(key);
    },
    clear: () => {
      backing.clear();
    },
    key: (index: number) => [...backing.keys()][index] ?? null,
    get length() {
      return backing.size;
    },
  };
}

/**
 * Install a Map-backed stand-in for one of the `Storage` globals.
 *
 * Node 22+ defines its own `localStorage` and `sessionStorage`, which shadow
 * jsdom's and throw unless the process was started with `--localstorage-file`.
 * Every suite that touches a persisted Zustand store or the session transport
 * therefore needs one of these — this is the one, rather than a copy per suite.
 *
 * Call at module scope, before the first test: `persist` reads storage when the
 * store module is first imported.
 *
 * Returns the backing map so a test can seed or inspect raw values.
 */
export function stubWebStorage(name: 'localStorage' | 'sessionStorage'): Map<string, string> {
  const backing = new Map<string, string>();
  vi.stubGlobal(name, memoryStorage(backing));
  return backing;
}

/** `stubWebStorage('localStorage')`, which is what nearly every caller wants. */
export function stubLocalStorage(): Map<string, string> {
  return stubWebStorage('localStorage');
}
