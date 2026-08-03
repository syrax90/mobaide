import { useId, useState } from 'react';
import type { UseFormRegisterReturn } from 'react-hook-form';

interface PasswordFieldProps {
  label: string;
  autoComplete: 'current-password' | 'new-password';
  registration: UseFormRegisterReturn;
  error?: string;
}

export function PasswordField({
  label,
  autoComplete,
  registration,
  error,
}: PasswordFieldProps) {
  const [revealed, setRevealed] = useState(false);
  const id = useId();
  const errorId = `${id}-error`;

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
          aria-invalid={error !== undefined}
          aria-describedby={error !== undefined ? errorId : undefined}
          {...registration}
        />
        <button
          type="button"
          className="field-affix"
          // aria-pressed carries the state; the label describes the action the
          // press performs, matching the mobile clients' content descriptions.
          aria-pressed={revealed}
          aria-label={revealed ? 'Hide password' : 'Show password'}
          onClick={() => {
            setRevealed((value) => !value);
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
