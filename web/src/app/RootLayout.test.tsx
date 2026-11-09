import { cleanup, render, screen, within } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router';
import { afterEach, describe, expect, it } from 'vitest';
import { stubLocalStorage } from '../test/localStorageStub';

// Before importing the layout, so the theme store's `persist` captures the stand-in.
stubLocalStorage();

const { RootLayout } = await import('./RootLayout');

afterEach(() => {
  cleanup();
});

function renderAt(path: string) {
  render(
    <MemoryRouter initialEntries={[path]}>
      <Routes>
        <Route element={<RootLayout />}>
          <Route index element={null} />
          <Route path="*" element={null} />
        </Route>
      </Routes>
    </MemoryRouter>,
  );
  return within(screen.getByRole('navigation', { name: 'Main' }));
}

describe('RootLayout', () => {
  it('lists Sessions first among the tabs, before Agents and Projects', () => {
    const nav = renderAt('/');
    const tabs = nav
      .getAllByRole('link')
      .map((link) => link.textContent)
      .filter((text) => text !== '');

    expect(tabs).toEqual(['Sessions', 'Agents', 'Projects', 'Templates', 'Account', 'Settings']);
    expect(nav.getByRole('link', { name: 'Sessions' })).toHaveAttribute('href', '/');
  });

  it('marks Sessions current only on the sessions screen', () => {
    const home = renderAt('/');
    expect(home.getByRole('link', { name: 'Sessions' })).toHaveAttribute('aria-current', 'page');
    cleanup();

    const projects = renderAt('/projects');
    expect(projects.getByRole('link', { name: 'Sessions' })).not.toHaveAttribute('aria-current');
    expect(projects.getByRole('link', { name: 'Projects' })).toHaveAttribute('aria-current', 'page');
  });
});
