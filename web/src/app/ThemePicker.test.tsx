import { act, cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import { stubLocalStorage } from '../test/localStorageStub';
import { stubPrefersDark } from '../test/matchMediaStub';

// Before importing the store, so zustand's `persist` captures the stand-in.
stubLocalStorage();

const { ThemePicker } = await import('./ThemePicker');
const { useThemeStore } = await import('../stores/themeStore');

const AUTO_LIGHT = 'Auto — follows your system (currently light)';
const AUTO_DARK = 'Auto — follows your system (currently dark)';

beforeEach(() => {
  useThemeStore.setState({ preference: 'system' });
  stubPrefersDark(false);
});

afterEach(() => {
  cleanup();
});

describe('ThemePicker', () => {
  it('offers the three preferences as one radio group', () => {
    render(<ThemePicker />);

    expect(screen.getByRole('radiogroup', { name: 'Theme' })).toBeInTheDocument();
    expect(screen.getAllByRole('radio')).toHaveLength(3);
    expect(screen.getByRole('radio', { name: AUTO_LIGHT })).toBeChecked();
  });

  it('checks the segment matching the stored preference', () => {
    useThemeStore.setState({ preference: 'dark' });

    render(<ThemePicker />);

    expect(screen.getByRole('radio', { name: 'Dark' })).toBeChecked();
    expect(screen.getByRole('radio', { name: AUTO_LIGHT })).not.toBeChecked();
  });

  it('writes the chosen preference to the store', () => {
    render(<ThemePicker />);

    fireEvent.click(screen.getByRole('radio', { name: 'Light' }));

    expect(useThemeStore.getState().preference).toBe('light');
  });

  /**
   * The regression the cycle button had: under a light OS, `system -> light`
   * repaints nothing, so the moving selection is the only feedback the click can
   * give. If that stopped happening the control would read as dead again.
   */
  it('moves the selection even when the resolved theme does not change', () => {
    render(<ThemePicker />);

    fireEvent.click(screen.getByRole('radio', { name: 'Light' }));

    expect(screen.getByRole('radio', { name: 'Light' })).toBeChecked();
    expect(screen.getByRole('radio', { name: AUTO_LIGHT })).not.toBeChecked();
  });

  it('names the theme Auto resolves to and follows the OS', () => {
    const media = stubPrefersDark(false);
    render(<ThemePicker />);

    expect(screen.getByRole('radio', { name: AUTO_LIGHT })).toBeInTheDocument();

    act(() => {
      media.set(true);
    });

    expect(screen.getByRole('radio', { name: AUTO_DARK })).toBeInTheDocument();
  });

  /**
   * `aria-label` covers assistive tech; the pointer needs the same sentence on
   * something a pointer can rest on. The input is clipped to zero area behind
   * the label, so a `title` moved onto it would be a tooltip nobody can summon —
   * and no accessible-name assertion would notice, because `aria-label` wins the
   * name computation either way.
   */
  it('hangs the Auto tooltip on the visible segment rather than the hidden input', () => {
    render(<ThemePicker />);

    expect(screen.getByTitle(AUTO_LIGHT)).toHaveTextContent('Auto');
  });

  /**
   * Radios group by `name` across the whole document, and the header picker and
   * the Settings card one are mounted together. A shared literal name would fuse
   * them into a single group, so checking one would silently uncheck the other.
   */
  it('keeps two mounted pickers in separate radio groups', () => {
    render(
      <>
        <ThemePicker />
        <ThemePicker />
      </>,
    );

    const names = screen
      .getAllByRole('radio')
      .map((radio) => (radio as HTMLInputElement).name);

    expect(names).toHaveLength(6);
    expect(new Set(names).size).toBe(2);
  });
});
