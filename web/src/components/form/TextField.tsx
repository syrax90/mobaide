import { useId, type ChangeEvent } from 'react';
import type { UseFormRegisterReturn } from 'react-hook-form';

interface BaseTextFieldProps {
  label: string;
  type?: 'text' | 'email';
  placeholder?: string;
  autoComplete?: string;
  autoFocus?: boolean;
  error?: string;
  hint?: string;
  disabled?: boolean;
}

/**
 * Either react-hook-form drives the input, or the caller does.
 *
 * The auth forms use `registration`; the create wizard holds its state in a
 * reducer instead, so it needs the controlled form. A union rather than two
 * optional props so neither can be omitted or both supplied.
 */
type TextFieldProps = BaseTextFieldProps &
  (
    | { registration: UseFormRegisterReturn; value?: never; onChange?: never }
    | { registration?: never; value: string; onChange: (value: string) => void }
  );

export function TextField({
  label,
  type = 'text',
  placeholder,
  autoComplete,
  autoFocus,
  error,
  hint,
  disabled,
  registration,
  value,
  onChange,
}: TextFieldProps) {
  const id = useId();
  const errorId = `${id}-error`;
  const hintId = `${id}-hint`;
  const describedBy =
    error !== undefined ? errorId : hint !== undefined ? hintId : undefined;

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
      <input
        id={id}
        className="field-input"
        type={type}
        placeholder={placeholder}
        autoComplete={autoComplete}
        autoFocus={autoFocus}
        disabled={disabled}
        aria-invalid={error !== undefined}
        aria-describedby={describedBy}
        {...controlled}
      />
      {hint !== undefined && error === undefined && (
        <p className="field-hint" id={hintId}>
          {hint}
        </p>
      )}
      {error !== undefined && (
        <p className="field-error" id={errorId}>
          {error}
        </p>
      )}
    </div>
  );
}
