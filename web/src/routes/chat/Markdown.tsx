import { memo, useEffect, useMemo, useRef, useState, type ReactNode } from 'react';
import { parseMarkdown, type MdBlock, type MdInline } from '../../chat/markdown';
import { writeClipboardText } from '../../lib/clipboard';

function renderInline(nodes: readonly MdInline[]): ReactNode[] {
  return nodes.map((node, index) => {
    switch (node.t) {
      case 'text':
        return node.v;
      case 'br':
        return <br key={index} />;
      case 'code':
        return (
          <code key={index} className="md-inline-code">
            {node.v}
          </code>
        );
      case 'strong':
        return <strong key={index}>{renderInline(node.c)}</strong>;
      case 'em':
        return <em key={index}>{renderInline(node.c)}</em>;
      case 'del':
        return <del key={index}>{renderInline(node.c)}</del>;
      case 'link':
        return (
          <a key={index} href={node.href} target="_blank" rel="noopener noreferrer">
            {renderInline(node.c)}
          </a>
        );
    }
  });
}

export function CodeBlock({ lang, text }: { lang: string; text: string }) {
  const [copied, setCopied] = useState(false);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);
  useEffect(
    () => () => {
      if (timer.current !== null) clearTimeout(timer.current);
    },
    [],
  );
  return (
    <div className="md-code">
      <div className="md-code-head">
        <span className="md-code-lang">{lang}</span>
        <button
          type="button"
          className="md-code-copy"
          aria-label={lang === '' ? 'Copy code' : `Copy ${lang} code`}
          onClick={() => {
            void writeClipboardText(text).then((ok) => {
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
      </div>
      <pre className="md-code-body" tabIndex={0}>
        <code>{text}</code>
      </pre>
    </div>
  );
}

function renderBlocks(blocks: readonly MdBlock[]): ReactNode[] {
  return blocks.map((block, index) => {
    switch (block.t) {
      case 'p':
        return <p key={index}>{renderInline(block.c)}</p>;
      case 'h':
        return block.level <= 2 ? (
          <h3 key={index} className="md-h md-h-lg">
            {renderInline(block.c)}
          </h3>
        ) : (
          <h4 key={index} className="md-h md-h-md">
            {renderInline(block.c)}
          </h4>
        );
      case 'code':
        return <CodeBlock key={index} lang={block.lang} text={block.text} />;
      case 'quote':
        return <blockquote key={index}>{renderBlocks(block.c)}</blockquote>;
      case 'hr':
        return <hr key={index} />;
      case 'list': {
        const items = block.items.map((item, itemIndex) => (
          <li key={itemIndex} className={item.task === null ? undefined : 'md-task'}>
            {item.task !== null && (
              <input
                type="checkbox"
                checked={item.task}
                readOnly
                disabled
                aria-label={item.task ? 'Done' : 'Not done'}
              />
            )}
            <div className="md-li-body">{renderBlocks(item.c)}</div>
          </li>
        ));
        return block.ordered ? (
          <ol key={index} start={block.start === 1 ? undefined : block.start}>
            {items}
          </ol>
        ) : (
          <ul key={index}>{items}</ul>
        );
      }
      case 'table':
        return (
          <div key={index} className="md-table-wrap" tabIndex={0}>
            <table>
              <thead>
                <tr>
                  {block.head.map((cell, cellIndex) => (
                    <th key={cellIndex} style={{ textAlign: block.align[cellIndex] ?? undefined }}>
                      {renderInline(cell)}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {block.rows.map((row, rowIndex) => (
                  <tr key={rowIndex}>
                    {row.map((cell, cellIndex) => (
                      <td
                        key={cellIndex}
                        style={{ textAlign: block.align[cellIndex] ?? undefined }}
                      >
                        {renderInline(cell)}
                      </td>
                    ))}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        );
    }
  });
}

interface MarkdownProps {
  text: string;
  className?: string;
}

/** Parsed once per text: the rows that did not change never re-parse. */
export const Markdown = memo(function Markdown({ text, className }: MarkdownProps) {
  const blocks = useMemo(() => parseMarkdown(text), [text]);
  return (
    <div className={className === undefined ? 'md' : `md ${className}`}>{renderBlocks(blocks)}</div>
  );
});
