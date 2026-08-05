import { memo, useMemo, type CSSProperties } from 'react';
import { ansiCss, parseAnsiRuns, type AnsiRun } from '../../protocol/ansi';

interface AnsiLineProps {
  text: string;
  ansi: boolean;
}

function runStyle(run: AnsiRun): CSSProperties {
  const style: CSSProperties = {};
  if (run.foreground !== undefined) style.color = ansiCss(run.foreground);
  if (run.background !== undefined) style.backgroundColor = ansiCss(run.background);
  if (run.bold) style.fontWeight = 700;
  if (run.italic) style.fontStyle = 'italic';
  if (run.underline) style.textDecoration = 'underline';
  return style;
}

/**
 * One log line.
 *
 * Colours ride the `style` prop rather than generated classes. React writes
 * those through CSSOM (`node.style.setProperty`), which CSP does not gate at
 * all — and this SPA's `style-src` carries `'unsafe-inline'` regardless, forced
 * by Google Identity Services. A class-per-colour stylesheet would buy nothing
 * and could not express truecolor.
 *
 * Memoised on `(text, ansi)`, and log lines are immutable once appended, so a
 * line is parsed exactly once no matter how many more arrive after it.
 */
export const AnsiLine = memo(function AnsiLine({ text, ansi }: AnsiLineProps) {
  const runs = useMemo(() => (ansi ? parseAnsiRuns(text) : null), [text, ansi]);

  if (runs === null) return <>{text}</>;
  return (
    <>
      {runs.map((run, index) => (
        // Index keys are correct here and only here: runs are derived from one
        // immutable string, so the list can never reorder or splice.
        <span key={index} style={runStyle(run)}>
          {run.text}
        </span>
      ))}
    </>
  );
});
