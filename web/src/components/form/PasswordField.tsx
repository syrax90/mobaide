import { useId, useState, type ChangeEvent } from 'react';
import type { UseFormRegisterReturn } from 'react-hook-form';

interface BasePasswordFieldProps {
  label: string;
  autoComplete: 'current-password' | 'new-password' | 'off';
  error?: string;
  autoFocus?: boolean;
  placeholder?: string;
  /**
   * What the reveal button calls the secret. Defaults to "password"; the
   * project form passes "key", which is what both mobile clients' content
   * descriptions say for the same field.
   */
  secretNoun?: string;
}

/**
 * Same union as `TextField`: the auth forms drive this through react-hook-form,
 * while the Codex login dialog holds its value in local state.
 */
type PasswordFieldProps = BasePasswordFieldProps &
  (
    | { registration: UseFormRegisterReturn; value?: never; onChange?: never }
    | { registration?: never; value: string; onChange: (value: string) => void }
  );

export function PasswordField({
  label,
  autoComplete,
  error,
  autoFocus,
  placeholder,
  secretNoun = 'password',
  registration,
  value,
  onChange,
}: PasswordFieldProps) {
  const [revealed, setRevealed] = useState(false);
  const id = useId();
  const errorId = `${id}-error`;

  const controlled =
    registration === undefined
      ? {
          value,
          onChange: (event: ChangeEvent<HTMLInputElement>) => {
            onChange?.(event.target.value);
          },
        }
      : registration;

  return (
    <div className="field">
      <label className="field-label" htmlFor={id}>
        {label}
      </label>
      <div className="field-with-affix">
        <input
          id={id}
          className="field-input"
          type={revealed ? 'text' : 'password'}
          autoComplete={autoComplete}
          autoFocus={autoFocus}
          placeholder={placeholder}
          // Browser spellcheck ships the contents of ordinary text inputs to
          // Google/Microsoft; a `password` input is exempt, but this one stops
          // being one the moment Show is pressed.
          spellCheck={false}
          aria-invalid={error !== undefined}
          aria-describedby={error !== undefined ? errorId : undefined}
          {...controlled}
        />
        <button
          type="button"
          className="field-affix"
          // aria-pressed carries the state; the label describes the action the
          // press performs, matching the mobile clients' content descriptions.
          aria-pressed={revealed}
          aria-label={revealed ? `Hide ${secretNoun}` : `Show ${secretNoun}`}
          onClick={() => {
            setRevealed((current) => !current);
          }}
        >
          {revealed ? 'Hide' : 'Show'}
        </button>
      </div>
      {error !== undefined && (
        <p className="field-error" id={errorId}>
          {error}
        </p>
      )}
    </div>
  );
}
