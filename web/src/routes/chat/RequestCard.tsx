import { useId, useState, type FormEvent } from 'react';
import type { ChatResult, ChatSessionModel } from '../../chat/chatModel';
import type {
  ChatRequest,
  FormField,
  FormRequest,
  FormValue,
  PermissionOption,
  PermissionRequest,
} from '../../chat/chatWire';
import { ToolKindIcon } from './ChatIcons';
import { ContentParts } from './ChatItems';

const ALREADY_ANSWERED = 'This request was already answered';

/** A late answer from a second client is not a failure: the request is settled either way. */
function answerError(result: ChatResult): string | null {
  if (result.ok || result.error === ALREADY_ANSWERED) return null;
  return result.lost ? 'The connection dropped. Try again.' : result.error;
}

function optionClass(option: PermissionOption, primary: boolean): string {
  if (option.kind.startsWith('reject')) return 'button chat-button-reject';
  return primary ? 'button button-primary' : 'button';
}

function PermissionCard({
  model,
  request,
  live,
}: {
  model: ChatSessionModel;
  request: PermissionRequest;
  live: boolean;
}) {
  const [pending, setPending] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const headingId = useId();
  const primaryId = request.options.find((option) => option.kind === 'allow_once')?.id ?? null;
  return (
    <section className="chat-request" aria-labelledby={headingId}>
      <p className="chat-request-kicker" id={headingId}>
        <ToolKindIcon kind={request.tool_kind} />
        Permission needed
      </p>
      <div className="chat-request-body" tabIndex={0}>
        <p className="chat-request-title">{request.title === '' ? 'Allow this action?' : request.title}</p>
        {request.subtitle !== null && request.subtitle !== '' && request.subtitle !== request.title && (
          <pre className="chat-request-subtitle">{request.subtitle}</pre>
        )}
        {request.content.length > 0 && (
          <div className="chat-request-preview">
            <ContentParts parts={request.content} />
          </div>
        )}
      </div>
      <div className="chat-request-actions">
        {request.options.map((option) => (
          <button
            key={option.id}
            type="button"
            className={optionClass(option, option.id === primaryId)}
            disabled={!live || pending !== null}
            aria-busy={pending === option.id}
            onClick={() => {
              setPending(option.id);
              setError(null);
              void model.answerPermission(request.id, option.id).then((result) => {
                const message = answerError(result);
                if (message === null) return;
                setPending(null);
                setError(message);
              });
            }}
          >
            {pending === option.id && <span className="chat-spinner" aria-hidden="true" />}
            {option.name === '' ? option.id : option.name}
          </button>
        ))}
      </div>
      {error !== null && (
        <p className="field-error" role="alert">
          {error}
        </p>
      )}
    </section>
  );
}

function defaultValue(field: FormField): FormValue {
  const value = field.default;
  switch (field.type) {
    case 'boolean':
      return value === true;
    case 'multiselect':
      return Array.isArray(value) ? value.filter((entry): entry is string => typeof entry === 'string') : [];
    case 'number':
    case 'integer':
      return typeof value === 'number' ? String(value) : '';
    default:
      return typeof value === 'string' ? value : '';
  }
}

function isFilled(field: FormField, value: FormValue | undefined): boolean {
  if (value === undefined) return false;
  if (field.type === 'boolean') return true;
  if (Array.isArray(value)) return value.length > 0;
  return String(value).trim() !== '';
}

function collectValues(
  fields: readonly FormField[],
  values: Readonly<Record<string, FormValue>>,
): Record<string, FormValue> {
  const out: Record<string, FormValue> = {};
  for (const field of fields) {
    const value = values[field.key];
    if (!isFilled(field, value) || value === undefined) continue;
    if (field.type === 'number' || field.type === 'integer') {
      const number = Number(value);
      if (Number.isFinite(number)) out[field.key] = number;
      continue;
    }
    out[field.key] = value;
  }
  return out;
}

function FormFieldInput({
  field,
  value,
  disabled,
  onChange,
}: {
  field: FormField;
  value: FormValue;
  disabled: boolean;
  onChange: (value: FormValue) => void;
}) {
  const id = useId();
  const label = (
    <>
      {field.title === '' ? field.key : field.title}
      {field.required && (
        <span className="chat-required" aria-hidden="true">
          {' '}
          *
        </span>
      )}
    </>
  );
  const hint =
    field.description !== null && field.description !== '' ? (
      <p className="field-hint" id={`${id}-hint`}>
        {field.description}
      </p>
    ) : null;
  const describedBy = hint === null ? undefined : `${id}-hint`;

  if (field.type === 'select' || field.type === 'multiselect') {
    const multi = field.type === 'multiselect';
    const selected = Array.isArray(value) ? value : [String(value)];
    return (
      <fieldset className="chat-form-choices" aria-describedby={describedBy} disabled={disabled}>
        <legend className="field-label">{label}</legend>
        {hint}
        {field.options.map((option) => (
          <label key={option.value} className="check-row">
            <input
              type={multi ? 'checkbox' : 'radio'}
              name={id}
              value={option.value}
              checked={selected.includes(option.value)}
              required={!multi && field.required}
              onChange={(event) => {
                if (!multi) {
                  onChange(option.value);
                  return;
                }
                const next = event.target.checked
                  ? [...selected, option.value]
                  : selected.filter((entry) => entry !== option.value);
                onChange(next);
              }}
            />
            <span className="check-row-text">
              <span>{option.label === '' ? option.value : option.label}</span>
              {option.description !== null && option.description !== '' && (
                <span className="check-row-hint">{option.description}</span>
              )}
            </span>
          </label>
        ))}
      </fieldset>
    );
  }

  if (field.type === 'boolean') {
    return (
      <div className="check-row">
        <input
          id={id}
          type="checkbox"
          role="switch"
          checked={value === true}
          disabled={disabled}
          aria-describedby={describedBy}
          onChange={(event) => {
            onChange(event.target.checked);
          }}
        />
        <div className="check-row-text">
          <label htmlFor={id}>{label}</label>
          {hint}
        </div>
      </div>
    );
  }

  const numeric = field.type === 'number' || field.type === 'integer';
  return (
    <div className="field">
      <label className="field-label" htmlFor={id}>
        {label}
      </label>
      <input
        id={id}
        className="field-input"
        type={numeric ? 'number' : 'text'}
        inputMode={field.type === 'integer' ? 'numeric' : numeric ? 'decimal' : undefined}
        step={field.type === 'integer' ? 1 : numeric ? 'any' : undefined}
        value={typeof value === 'string' ? value : String(value)}
        required={field.required}
        disabled={disabled}
        aria-describedby={describedBy}
        onChange={(event) => {
          onChange(event.target.value);
        }}
      />
      {hint}
    </div>
  );
}

function FormCard({
  model,
  request,
  live,
}: {
  model: ChatSessionModel;
  request: FormRequest;
  live: boolean;
}) {
  const [values, setValues] = useState<Record<string, FormValue>>(() =>
    Object.fromEntries(request.fields.map((field) => [field.key, defaultValue(field)])),
  );
  const [pending, setPending] = useState<'accept' | 'decline' | 'cancel' | null>(null);
  const [error, setError] = useState<string | null>(null);
  const headingId = useId();
  const complete = request.fields.every(
    (field) => !field.required || isFilled(field, values[field.key]),
  );
  const busy = pending !== null || !live;

  const send = (action: 'accept' | 'decline' | 'cancel') => {
    setPending(action);
    setError(null);
    void model
      .answerForm(request.id, action, action === 'accept' ? collectValues(request.fields, values) : undefined)
      .then((result) => {
        const message = answerError(result);
        if (message === null) return;
        setPending(null);
        setError(message);
      });
  };

  return (
    <form
      className="chat-request"
      aria-labelledby={headingId}
      onSubmit={(event: FormEvent) => {
        event.preventDefault();
        if (complete && !busy) send('accept');
      }}
    >
      <p className="chat-request-kicker" id={headingId}>
        Question
      </p>
      <div className="chat-request-body">
        <p className="chat-request-title">{request.title}</p>
        <div className="chat-form-fields">
          {request.fields.map((field) => (
            <FormFieldInput
              key={field.key}
              field={field}
              value={values[field.key] ?? ''}
              disabled={busy}
              onChange={(value) => {
                setValues((current) => ({ ...current, [field.key]: value }));
              }}
            />
          ))}
        </div>
      </div>
      <div className="chat-request-actions">
        <button type="submit" className="button button-primary" disabled={busy || !complete}>
          {pending === 'accept' && <span className="chat-spinner" aria-hidden="true" />}
          Submit
        </button>
        <button
          type="button"
          className="button"
          disabled={busy}
          onClick={() => {
            send('decline');
          }}
        >
          Decline
        </button>
        <button
          type="button"
          className="chat-link-button"
          disabled={busy}
          onClick={() => {
            send('cancel');
          }}
        >
          Cancel
        </button>
      </div>
      {error !== null && (
        <p className="field-error" role="alert">
          {error}
        </p>
      )}
    </form>
  );
}

/** The oldest pending request, expanded; the rest wait their turn. */
export function RequestDock({
  model,
  requests,
  live,
}: {
  model: ChatSessionModel;
  requests: readonly ChatRequest[];
  live: boolean;
}) {
  const first = requests[0];
  if (first === undefined) return null;
  let card;
  if (first.kind === 'permission') {
    card = <PermissionCard key={first.id} model={model} request={first} live={live} />;
  } else if (first.kind === 'form') {
    card = <FormCard key={first.id} model={model} request={first} live={live} />;
  } else {
    card = (
      <section key={first.id} className="chat-request">
        <p className="chat-request-kicker">Waiting for an answer</p>
        <div className="chat-request-body">
          <p className="chat-request-title">{first.title}</p>
        </div>
      </section>
    );
  }
  return (
    <div className="chat-requests">
      {card}
      {requests.length > 1 && (
        <p className="chat-request-more">+{requests.length - 1} more waiting</p>
      )}
    </div>
  );
}
