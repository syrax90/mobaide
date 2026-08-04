import { useId } from 'react';

export interface SelectOption {
  /** `null` is the "Default" / "Auto" entry — the wire omits the field entirely. */
  value: string | null;
  label: string;
}

interface SelectFieldProps {
  label: string;
  value: string | null;
  options: readonly SelectOption[];
  onChange: (value: string | null) => void;
  disabled?: boolean;
  hint?: string;
}

/** `null` travels through the DOM as `''` so no sentinel string ever enters state. */
const NULL_VALUE = '';

/**
 * A native `<select>`.
 *
 * Deliberately not a custom listbox: native is keyboard- and screen-reader-
 * correct for free and behaves properly on touch, and with no design system to
 * match, a hand-rolled one would be a few hundred lines of ARIA for no gain.
 */
export function SelectField({
  label,
  value,
  options,
  onChange,
  disabled,
  hint,
}: SelectFieldProps) {
  const id = useId();
  const hintId = `${id}-hint`;

  return (
    <div className="field">
      <label className="field-label" htmlFor={id}>
        {label}
      </label>
      <select
        id={id}
        className="field-input field-select"
        value={value ?? NULL_VALUE}
        disabled={disabled}
        aria-describedby={hint !== undefined ? hintId : undefined}
        onChange={(event) => {
          onChange(event.target.value === NULL_VALUE ? null : event.target.value);
        }}
      >
        {options.map((option) => (
          <option key={option.value ?? NULL_VALUE} value={option.value ?? NULL_VALUE}>
            {option.label}
          </option>
        ))}
      </select>
      {hint !== undefined && (
        <p className="field-hint" id={hintId}>
          {hint}
        </p>
      )}
    </div>
  );
}
