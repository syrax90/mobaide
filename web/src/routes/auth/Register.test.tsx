import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { stubWebStorage } from '../../test/localStorageStub';

// Before importing anything that captures either global.
stubWebStorage('sessionStorage');
stubWebStorage('localStorage');

const registered = vi.hoisted(() => ({ passwords: [] as string[] }));

vi.mock('../../api/authRepository', () => ({
  register: (_email: string, password: string) => {
    registered.passwords.push(password);
    return Promise.resolve({ kind: 'verificationRequired', email: 'new@example.com' });
  },
  loginWithGoogle: () => Promise.reject(new Error('unused')),
  loginWithApple: () => Promise.reject(new Error('unused')),
}));

// No server info: the screen renders its password form alone, with no Google or
// Apple button and no network call behind either.
vi.mock('../../api/useServerInfo', () => ({
  useServerInfo: () => ({ data: undefined }),
}));

const { Register } = await import('./Register');

function submit(password: string, confirmPassword: string): void {
  fireEvent.change(screen.getByLabelText('Email'), {
    target: { value: 'new@example.com' },
  });
  fireEvent.change(screen.getByLabelText('Password'), {
    target: { value: password },
  });
  fireEvent.change(screen.getByLabelText('Confirm Password'), {
    target: { value: confirmPassword },
  });
  fireEvent.click(screen.getByRole('button', { name: 'Create Account' }));
}

beforeEach(() => {
  registered.passwords = [];
  render(
    <MemoryRouter initialEntries={['/register']}>
      <Register />
    </MemoryRouter>,
  );
});

afterEach(cleanup);

describe('Register password length', () => {
  it('rejects a password shorter than eight characters without calling the server', async () => {
    submit('1234567', '1234567');

    expect(await screen.findByRole('alert')).toHaveTextContent(
      'Password must be at least 8 characters',
    );
    expect(registered.passwords).toEqual([]);
  });

  it('reports the length before a mismatch', async () => {
    // The server would refuse the short password anyway, so fixing the
    // confirmation first would only lead to a second error.
    submit('short', 'other');

    expect(await screen.findByRole('alert')).toHaveTextContent(
      'Password must be at least 8 characters',
    );
  });

});
