import { useEffect, useId, useRef, useState } from 'react';
import { Modal } from '../../components/Modal';
import { writeClipboardText } from '../../lib/clipboard';

interface InfoRow {
  label: string;
  value: string;
}

function CopyRow({ row }: { row: InfoRow }) {
  const [copied, setCopied] = useState(false);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);
  useEffect(
    () => () => {
      if (timer.current !== null) clearTimeout(timer.current);
    },
    [],
  );
  return (
    <div className="chat-info-row">
      <dt className="field-label">{row.label}</dt>
      <dd>
        <code className="chat-info-value">{row.value}</code>
        <button
          type="button"
          className="button button-icon"
          aria-label={`Copy ${row.label}`}
          onClick={() => {
            void writeClipboardText(row.value).then((ok) => {
              if (!ok) return;
              setCopied(true);
              if (timer.current !== null) clearTimeout(timer.current);
              timer.current = setTimeout(() => {
                setCopied(false);
              }, 1500);
            });
          }}
        >
          {copied ? 'Copied' : 'Copy'}
        </button>
      </dd>
    </div>
  );
}

/** Agent, folder and ACP session id — each copyable, the copy confirmed inline. */
export function SessionInfoDialog({
  rows,
  onClose,
}: {
  rows: readonly InfoRow[];
  onClose: () => void;
}) {
  const titleId = useId();
  return (
    <Modal labelledBy={titleId} onClose={onClose} className="chat-info-dialog">
      <h2 id={titleId}>Session info</h2>
      {rows.length === 0 ? (
        <p className="page-note">The agent has not reported any details yet.</p>
      ) : (
        <dl className="chat-info">
          {rows.map((row) => (
            <CopyRow key={row.label} row={row} />
          ))}
        </dl>
      )}
      <div className="button-row">
        <button type="button" className="button" onClick={onClose}>
          Close
        </button>
      </div>
    </Modal>
  );
}
