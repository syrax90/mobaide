import { cleanup, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it } from 'vitest';
import { IncompatibleServer } from './IncompatibleServer';

afterEach(cleanup);

describe('IncompatibleServer', () => {
  it('states the one action that escapes the takeover', () => {
    render(<IncompatibleServer />);

    expect(screen.getByRole('heading', { name: 'Update required' })).toBeInTheDocument();
    expect(screen.getByText(/reload to pick up the current build/i)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Reload' })).toBeInTheDocument();
    expect(screen.getByText(/hard refresh/i)).toBeInTheDocument();
  });

  it('offers support as the second escape route, at a version-free address', () => {
    // The mobile takeovers hand the user a store button that actually resolves
    // the gate; a reload against a stale cached SPA can leave this screen with
    // no next step and no navigation off it. The mailto is that next step, and
    // the href is the one place a version could survive this screen — no
    // `getByText` absence assertion reaches into an attribute.
    render(<IncompatibleServer />);

    expect(screen.getByRole('link', { name: 'email support' })).toHaveAttribute(
      'href',
      'mailto:support@mobaide.com?subject=Mobaide%20web%20support',
    );
  });

  it('shows none of the gate string that produced it', () => {
    // `AuthState.info.reason` reads "Server requires protocol v8+, this app
    // speaks v7. Server protocols: 8, 9." — protocol jargon aimed at a user who
    // can only reload either way. No digit belongs on this screen.
    render(<IncompatibleServer />);

    expect(screen.queryByText(/protocol/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/\d/)).not.toBeInTheDocument();
  });
});
