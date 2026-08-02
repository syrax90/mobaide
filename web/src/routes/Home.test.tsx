import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import { ToastViewport } from '../components/toast/ToastViewport';
import { useToastStore } from '../stores/toastStore';
import { Home } from './Home';

describe('Home', () => {
  beforeEach(() => {
    useToastStore.setState({ toasts: [] });
  });

  afterEach(cleanup);

  it('renders the heading', () => {
    render(<Home />);

    expect(
      screen.getByRole('heading', { level: 1, name: 'Mobaide Web' }),
    ).toBeInTheDocument();
  });

  it('shows a success toast when the demo button is clicked', () => {
    render(
      <>
        <Home />
        <ToastViewport />
      </>,
    );

    fireEvent.click(screen.getByRole('button', { name: 'Show a toast' }));

    expect(screen.getByText('Toasts are wired up.')).toBeInTheDocument();
    expect(useToastStore.getState().toasts[0]?.kind).toBe('success');
  });
});
