import { writeClipboardText } from '../lib/clipboard';
import { useToastStore } from '../stores/toastStore';

interface CopyableCodeProps {
  label: string;
  value: string;
  /** Renders in a `<pre>` so embedded newlines survive — for the `.env` block. */
  multiline?: boolean;
  testId?: string;
}

/**
 * A labelled block of literal text with a copy button.
 *
 * The mobile clients' `CopyableCodeBlock`. Their `isSensitive` flag has no
 * browser analogue — Android sets `ClipDescription.EXTRA_IS_SENSITIVE` so the
 * clipboard preview redacts a token, and no web API exposes that — so a copied
 * agent token lands in whatever clipboard history the OS keeps. Accepted, and
 * the reason the panel tells the user to prefer pairing over a manual token.
 */
export function CopyableCode({ label, value, multiline, testId }: CopyableCodeProps) {
  const copy = () => {
    void writeClipboardText(value).then((ok) => {
      useToastStore
        .getState()
        .show(ok ? `${label} copied` : 'Could not copy — select the text instead', ok ? 'success' : 'error');
    });
  };

  return (
    <div className="code-block" data-testid={testId}>
      <div className="code-block-head">
        <span className="code-block-label">{label}</span>
        <button type="button" className="button code-block-copy" onClick={copy}>
          Copy
        </button>
      </div>
      {multiline === true ? (
        <pre className="code-block-value code-block-value-multiline">{value}</pre>
      ) : (
        <code className="code-block-value">{value}</code>
      )}
    </div>
  );
}
