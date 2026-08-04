import { useId } from 'react';

interface CheckboxFieldProps {
  label: string;
  checked: boolean;
  onChange: (checked: boolean) => void;
  disabled?: boolean;
  /** Caption under the label — used for the wizard's off-state warnings. */
  hint?: string;
}

export function CheckboxField({
  label,
  checked,
  onChange,
  disabled,
  hint,
}: CheckboxFieldProps) {
  const id = useId();
  const hintId = `${id}-hint`;

  return (
    <div className="check-row">
      <input
        id={id}
        type="checkbox"
        checked={checked}
        disabled={disabled}
        aria-describedby={hint !== undefined ? hintId : undefined}
        onChange={(event) => {
          onChange(event.target.checked);
        }}
      />
      <div className="check-row-text">
        <label htmlFor={id}>{label}</label>
        {hint !== undefined && (
          <p className="check-row-hint" id={hintId}>
            {hint}
          </p>
        )}
      </div>
    </div>
  );
}
