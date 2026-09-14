import { useId } from 'react';

interface TextAreaFieldProps {
  label: string;
  value: string;
  onChange: (value: string) => void;
  rows?: number;
  placeholder?: string;
  hint?: string;
  error?: string;
  disabled?: boolean;
  /** Appended to the base classes, for a field that needs its own type styling. */
  className?: string;
  /**
   * Off for machine-readable input. A phone keyboard capitalising the first
   * letter of a pasted pairing payload silently corrupts it.
   */
  autoCapitalize?: 'off' | 'on';
  autoCorrect?: 'off' | 'on';
}

export function TextAreaField({
  label,
  value,
  onChange,
  rows = 3,
  placeholder,
  hint,
  error,
  disabled,
  className,
  autoCapitalize,
  autoCorrect,
}: TextAreaFieldProps) {
  const id = useId();
  const errorId = `${id}-error`;
  const hintId = `${id}-hint`;

  return (
    <div className="field">
      <label className="field-label" htmlFor={id}>
        {label}
      </label>
      <textarea
        id={id}
        className={
          className === undefined
            ? 'field-input field-textarea'
            : `field-input field-textarea ${className}`
        }
        rows={rows}
        value={value}
        placeholder={placeholder}
        disabled={disabled}
        spellCheck={false}
        autoCapitalize={autoCapitalize}
        autoCorrect={autoCorrect}
        aria-invalid={error !== undefined}
        aria-describedby={error !== undefined ? errorId : hint !== undefined ? hintId : undefined}
        onChange={(event) => {
          onChange(event.target.value);
        }}
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
