import {
  FLAG_KIND_LABELS,
  flagKeyError,
  flagValueError,
  newFlagEntry,
  type FlagEntry,
  type FlagKind,
} from './flagEntry';

interface FlagsEditorProps {
  flags: FlagEntry[];
  onChange: (flags: FlagEntry[]) => void;
}

const KINDS: FlagKind[] = ['string', 'boolean', 'number'];

/**
 * The typed `default_flags` map.
 *
 * Every key and value field turns autocapitalisation and autocorrect off: these
 * values are passed to the CLI verbatim, so `--model` must not become `--Model`.
 */
export function FlagsEditor({ flags, onChange }: FlagsEditorProps) {
  const update = (index: number, patch: Partial<FlagEntry>) => {
    onChange(flags.map((entry, i) => (i === index ? { ...entry, ...patch } : entry)));
  };

  return (
    <section className="option-group">
      <h3>Default flags</h3>
      <p className="field-hint">
        Key/value pairs passed when launching this project&rsquo;s agent. Types
        round-trip as typed JSON.
      </p>

      {flags.map((entry, index) => {
        const keyError = flagKeyError(flags, index);
        const valueError = flagValueError(entry);
        return (
          <div className="flag-row" key={entry.id}>
            <label className="flag-cell">
              <span className="field-label">Key</span>
              <input
                className="field-input"
                value={entry.key}
                autoCapitalize="off"
                autoCorrect="off"
                spellCheck={false}
                aria-invalid={keyError !== null}
                onChange={(event) => {
                  update(index, { key: event.target.value });
                }}
              />
              {keyError !== null && <span className="field-error">{keyError}</span>}
            </label>

            <label className="flag-cell">
              <span className="field-label">Type</span>
              <select
                className="field-input field-select"
                value={entry.kind}
                onChange={(event) => {
                  const kind = event.target.value as FlagKind;
                  // Seed a boolean with a legal literal; switching away leaves
                  // the text alone so the user does not lose what they typed.
                  update(index, {
                    kind,
                    value:
                      kind === 'boolean' && entry.value !== 'true' && entry.value !== 'false'
                        ? 'false'
                        : entry.value,
                  });
                }}
              >
                {KINDS.map((kind) => (
                  <option key={kind} value={kind}>
                    {FLAG_KIND_LABELS[kind]}
                  </option>
                ))}
              </select>
            </label>

            <label className="flag-cell">
              <span className="field-label">Value</span>
              {entry.kind === 'boolean' ? (
                <select
                  className="field-input field-select"
                  value={entry.value === 'true' ? 'true' : 'false'}
                  onChange={(event) => {
                    update(index, { value: event.target.value });
                  }}
                >
                  <option value="true">true</option>
                  <option value="false">false</option>
                </select>
              ) : (
                <input
                  className="field-input"
                  value={entry.value}
                  autoCapitalize="off"
                  autoCorrect="off"
                  spellCheck={false}
                  aria-invalid={valueError !== null}
                  onChange={(event) => {
                    update(index, { value: event.target.value });
                  }}
                />
              )}
              {valueError !== null && <span className="field-error">{valueError}</span>}
            </label>

            <button
              type="button"
              className="button button-danger"
              aria-label={`Remove flag ${entry.key}`}
              onClick={() => {
                onChange(flags.filter((_, i) => i !== index));
              }}
            >
              Remove
            </button>
          </div>
        );
      })}

      <button
        type="button"
        className="button"
        onClick={() => {
          onChange([...flags, newFlagEntry()]);
        }}
      >
        Add flag
      </button>
    </section>
  );
}
