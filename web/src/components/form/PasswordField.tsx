import { useId, useState, type ChangeEvent } from 'react';
import type { UseFormRegisterReturn } from 'react-hook-form';

interface BasePasswordFieldProps {
  label: string;
  autoComplete: 'current-password' | 'new-password' | 'off';
  error?: string;
  autoFocus?: boolean;
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
          aria-label={revealed ? 'Hide password' : 'Show password'}
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
