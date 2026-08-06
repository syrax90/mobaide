/**
 * Reserved-key sweep harness. **Development builds only** — the route is
 * registered behind `import.meta.env.DEV`, so Rollup drops both it and this
 * module from the production bundle.
 *
 * `reservedKeys.ts` ships every row at `confidence: 'documented'` because
 * whether `preventDefault()` actually wins is undocumented for almost every
 * chord. This measures it, one chord at a time, and prints JSON to paste back
 * into the table. The procedure and the results matrix live in
 * `docs/web-keyboard-reserved-keys.md`; running it is CLA-251.
 *
 * Two facts per chord, and they are independent — Firefox delivers Ctrl+W to the
 * page *and* closes the tab anyway:
 *
 * - **delivered** — a `keydown` reached the page at all.
 * - **survived** — the page was still here afterwards, which is what the
 *   operator confirms by pressing the chord and then answering.
 *
 * `cancelable` comes straight off the event, and is the closest thing to an
 * automatic answer: `cancelable === false` means the browser had already
 * decided.
 */
import { useEffect, useMemo, useState } from 'react';
import { currentKeyboardEnv } from '../../keyboard/browserEnv';
import { RESERVED_KEYS } from '../../keyboard/reservedKeys';
import { recordChordFromEvent } from '../../keyboard/recordChord';
import { chordToken } from '../../protocol/keyChord';

interface Observation {
  token: string;
  delivered: boolean;
  cancelable: boolean;
  /** Filled in by the operator; the page cannot see its own death. */
  survived: boolean | null;
}

export function KeySweepScreen() {
  const env = useMemo(currentKeyboardEnv, []);
  const [observations, setObservations] = useState<Record<string, Observation>>({});
  const [suppress, setSuppress] = useState(true);

  const expected = useMemo(
    () =>
      [
        ...new Set(
          RESERVED_KEYS.filter(
            (entry) =>
              entry.browsers.includes(env.browser) && entry.platforms.includes(env.os),
          ).map((entry) => entry.token),
        ),
      ].sort(),
    [env],
  );

  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent): void => {
      const outcome = recordChordFromEvent(event);
      if (outcome.status !== 'chord') return;
      const token = chordToken(outcome.chord);
      // Suppression is a knob, not a default: the useful measurement is whether
      // the browser acts *despite* preventDefault, and turning it off shows what
      // the chord does when the page does not fight it.
      if (suppress) event.preventDefault();
      setObservations((current) => ({
        ...current,
        [token]: {
          token,
          delivered: true,
          cancelable: event.cancelable,
          survived: current[token]?.survived ?? null,
        },
      }));
    };
    document.addEventListener('keydown', onKeyDown, true);
    return () => {
      document.removeEventListener('keydown', onKeyDown, true);
    };
  }, [suppress]);

  const answer = (token: string, survived: boolean): void => {
    setObservations((current) => ({
      ...current,
      [token]: {
        token,
        delivered: current[token]?.delivered ?? false,
        cancelable: current[token]?.cancelable ?? false,
        survived,
      },
    }));
  };

  const report = JSON.stringify(
    {
      browser: env.browser,
      os: env.os,
      standalone: env.standalone,
      userAgent: typeof navigator === 'undefined' ? '' : navigator.userAgent,
      suppressed: suppress,
      observations: Object.values(observations).sort((a, b) => a.token.localeCompare(b.token)),
    },
    null,
    2,
  );

  return (
    <section className="page">
      <h1>Reserved-key sweep</h1>
      <p>
        {env.browser} on {env.os}
        {env.standalone ? ' (installed app)' : ''}. Press each chord below, then
        record whether this page survived.
      </p>

      <label className="terminal-toggle">
        <input
          type="checkbox"
          checked={suppress}
          onChange={(event) => {
            setSuppress(event.target.checked);
          }}
        />
        Call preventDefault() on every chord
      </label>

      <ul className="sweep-list">
        {expected.map((token) => {
          const seen = observations[token];
          return (
            <li key={token} className="sweep-row">
              <code>{token}</code>
              <span>{seen === undefined ? 'not pressed' : 'delivered'}</span>
              <span>
                {seen === undefined
                  ? ''
                  : seen.cancelable
                    ? 'cancelable'
                    : 'not cancelable'}
              </span>
              <button
                type="button"
                className="button"
                onClick={() => {
                  answer(token, true);
                }}
              >
                Page survived
              </button>
              <button
                type="button"
                className="button"
                onClick={() => {
                  answer(token, false);
                }}
              >
                Browser acted
              </button>
              <span>{seen?.survived === null ? '' : String(seen?.survived)}</span>
            </li>
          );
        })}
      </ul>

      <h2>Result</h2>
      <pre className="sweep-report">{report}</pre>
    </section>
  );
}
