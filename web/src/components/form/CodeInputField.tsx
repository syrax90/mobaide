import { useId } from 'react';

interface CodeInputFieldProps {
  value: string;
  onChange: (value: string) => void;
  label: string;
  error?: string;
}

const CODE_LENGTH = 6;

/**
 * Six-digit OTP entry.
 *
 * One real input stretched across six boxes rather than six inputs, matching
 * Android's `CodeInputField`: a single field keeps paste working (the whole
 * code arrives as one change) and leaves autofill able to see one target.
 *
 * Filtering to digits and capping length happen here so every caller gets the
 * same rule — on Android that lives in the ViewModel, but there is no separate
 * layer here worth the indirection.
 */
export function CodeInputField({
  value,
  onChange,
  label,
  error,
}: CodeInputFieldProps) {
  const id = useId();
  const errorId = `${id}-error`;
  const digits = Array.from({ length: CODE_LENGTH }, (_, i) => value[i] ?? '');

  return (
    <div className="field">
      <label className="field-label" htmlFor={id}>
        {label}
      </label>
      <div className="code-input">
        <input
          id={id}
          className="code-input-control"
          // `inputMode` gives mobile keyboards the numeric pad while leaving
          // this a text input, so a pasted code is not silently rejected the
          // way type="number" would reject it.
          inputMode="numeric"
          autoComplete="one-time-code"
          // Deliberately no maxLength: the browser applies it to pasted text
          // before React sees the change, so pasting a formatted code like
          // "123-456" would be clipped to "123-45" and then filtered down to
          // five digits. The slice below caps the value after non-digits are
          // stripped, which is what lets a formatted paste survive.
          value={value}
          aria-invalid={error !== undefined}
          aria-describedby={error !== undefined ? errorId : undefined}
          onChange={(event) => {
            onChange(
              event.target.value.replace(/\D/g, '').slice(0, CODE_LENGTH),
            );
          }}
        />
        <div className="code-input-boxes" aria-hidden="true">
          {digits.map((digit, index) => (
            <span
              key={index}
              className={`code-input-box${digit === '' ? '' : ' code-input-box-filled'}`}
            >
              {digit}
            </span>
          ))}
        </div>
      </div>
      {error !== undefined && (
        <p className="field-error" id={errorId}>
          {error}
        </p>
      )}
    </div>
  );
}
