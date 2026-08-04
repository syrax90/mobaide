import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { MemoryRouter, useLocation } from 'react-router';
import { afterEach, describe, expect, it } from 'vitest';
import { Home } from './Home';

/** Reports where the stand-in "open a terminal" form navigated to. */
function LocationProbe() {
  const location = useLocation();
  return <output data-testid="location">{location.pathname}</output>;
}

function renderHome() {
  render(
    <MemoryRouter initialEntries={['/']}>
      <Home />
      <LocationProbe />
    </MemoryRouter>,
  );
}

function fill(label: string, value: string) {
  fireEvent.change(screen.getByLabelText(label), { target: { value } });
}

describe('Home', () => {
  afterEach(cleanup);

  it('renders the heading', () => {
    renderHome();

    expect(
      screen.getByRole('heading', { level: 1, name: 'Mobaide Web' }),
    ).toBeInTheDocument();
  });

  it('keeps the attach button disabled until both ids are filled in', () => {
    renderHome();
    const attach = screen.getByRole('button', { name: 'Attach' });

    expect(attach).toBeDisabled();

    fill('Agent id', 'agent-1');
    expect(attach).toBeDisabled();

    fill('Session id', '   ');
    expect(attach).toBeDisabled();

    fill('Session id', 'session-1');
    expect(attach).toBeEnabled();
  });

  it('opens the terminal route for the ids it was given', () => {
    renderHome();
    fill('Agent id', ' agent-1 ');
    fill('Session id', 'session-1');

    fireEvent.click(screen.getByRole('button', { name: 'Attach' }));

    expect(screen.getByTestId('location')).toHaveTextContent(
      '/agents/agent-1/sessions/session-1',
    );
  });

  it('escapes ids that would otherwise change the shape of the path', () => {
    renderHome();
    fill('Agent id', 'a/b');
    fill('Session id', 'x y');

    fireEvent.click(screen.getByRole('button', { name: 'Attach' }));

    expect(screen.getByTestId('location')).toHaveTextContent(
      '/agents/a%2Fb/sessions/x%20y',
    );
  });
});
