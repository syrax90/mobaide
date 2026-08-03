import { useId } from 'react';
import type { UseFormRegisterReturn } from 'react-hook-form';

interface TextFieldProps {
  label: string;
  type?: 'text' | 'email';
  placeholder?: string;
  autoComplete?: string;
  autoFocus?: boolean;
  registration: UseFormRegisterReturn;
  error?: string;
}

export function TextField({
  label,
  type = 'text',
  placeholder,
  autoComplete,
  autoFocus,
  registration,
  error,
}: TextFieldProps) {
  const id = useId();
  const errorId = `${id}-error`;

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
        aria-invalid={error !== undefined}
        aria-describedby={error !== undefined ? errorId : undefined}
        {...registration}
      />
      {error !== undefined && (
        <p className="field-error" id={errorId}>
          {error}
        </p>
      )}
    </div>
  );
}
