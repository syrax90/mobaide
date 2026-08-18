import { useId } from 'react';
import { useThemeStore, type ThemePreference } from '../stores/themeStore';
import { useSystemTheme } from './useResolvedTheme';

const OPTIONS: readonly { value: ThemePreference; label: string }[] = [
  { value: 'system', label: 'Auto' },
  { value: 'light', label: 'Light' },
  { value: 'dark', label: 'Dark' },
];

/**
 * Shared by both shells so the preference is reachable while signed out, and
 * repeated in the Settings Appearance card.
 *
 * Direct selection rather than the cycle button this replaces. `system` always
 * paints identically to whichever of light/dark the OS currently is, so a cycle
 * had one step per lap that moved the preference without moving a pixel and read
 * as a dead click. Three radios cannot: the checked segment moves on every
 * choice, which is the feedback a colour-neutral change otherwise has none of.
 *
 * Native radios because `SelectField` already settled that argument for this
 * codebase — roving arrow-key selection and correct announcements for free.
 */
export function ThemePicker() {
  const preference = useThemeStore((state) => state.preference);
  const setPreference = useThemeStore((state) => state.setPreference);
  const systemTheme = useSystemTheme();
  // Header and Settings card mount at once, and radios group by `name` across
  // the whole document — a literal would make the two pickers one group.
  const name = useId();

  return (
    // A bare fieldset maps to `group`, not `radiogroup` — the role has to be
    // spelled out. The legend still supplies the name.
    <fieldset className="theme-picker" role="radiogroup">
      <legend className="visually-hidden">Theme</legend>
      {OPTIONS.map((option) => {
        // Only Auto is ambiguous: it is the one label that does not say what
        // the page will look like. Naming the OS value is what explains why Auto
        // and Dark can be the same picture. Deliberately the system theme and
        // not the painted one — while Light is selected, what is on screen says
        // nothing about what picking Auto would do.
        const autoHint =
          option.value === 'system'
            ? `Auto — follows your system (currently ${systemTheme})`
            : undefined;

        return (
          <label key={option.value} className="theme-seg" title={autoHint}>
            <input
              className="visually-hidden"
              type="radio"
              name={name}
              value={option.value}
              checked={preference === option.value}
              aria-label={autoHint}
              onChange={() => {
                setPreference(option.value);
              }}
            />
            <span>{option.label}</span>
          </label>
        );
      })}
    </fieldset>
  );
}
