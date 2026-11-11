import { cleanup, render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router';
import { afterEach, describe, expect, it } from 'vitest';
import { stubLocalStorage } from '../test/localStorageStub';

// Before importing anything that pulls in a persisted store (the theme toggle).
stubLocalStorage();

const { AuthLayout } = await import('./AuthLayout');

// Spelled out rather than imported from `supportLinks`: importing the constants
// the component renders would assert only that it renders them.
const MARKETING = 'https://mobaide.com/';

const renderShell = () =>
  render(
    <MemoryRouter initialEntries={['/login']}>
      <AuthLayout />
    </MemoryRouter>,
  );

afterEach(cleanup);

describe('AuthLayout site chrome', () => {
  it('sends the wordmark back to the site the visitor came from', () => {
    // mobaide.com's Sign in now lands here, so this shell is the far end of a
    // cross-origin hop: an unlinked brand leaves a visitor who arrived by
    // mistake with the back button as their only way out.
    renderShell();

    expect(screen.getByRole('link', { name: 'Mobaide home' })).toHaveAttribute(
      'href',
      MARKETING,
    );
  });
});
