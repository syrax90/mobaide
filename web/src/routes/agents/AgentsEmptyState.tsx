import { useState, type ReactNode } from 'react';
import { Link, useNavigate } from 'react-router';
import { CopyableCode } from '../../components/CopyableCode';
import { TextAreaField } from '../../components/form/TextAreaField';
import { encodePairingFragment, readPairingInput } from '../../protocol/pairingPayload';
import { MALFORMED_PAYLOAD_MESSAGE } from '../pair/pairCopy';
import { INSTALL_COMMANDS } from './hostOs';
import {
  pairingHintExample,
  pairingLinkPrefix,
  SHOW_QR_COMMAND,
} from './setupCopy';

interface AgentsEmptyStateProps {
  /** Opens the parent's `AddAgentDialog` — the last-resort token path. */
  onAddManually: () => void;
}

/**
 * What an account with no agents sees instead of the list.
 *
 * The web arm of Android `EmptyAgentsState` and iOS `EmptyAgentsState`: install
 * first, pair second, escape hatches last, because nothing is pairable until
 * `mobaide-agent` runs somewhere. Mobile's step 2 is "scan the QR"; a browser
 * has no camera, so the two lines the agent prints *under* that QR
 * (`pairing_qr.write_pairing_hints`) are the web equivalent, and they are the
 * reason step 2 and step 3 are alternatives rather than a sequence.
 *
 * The install one-liners are inline rather than behind mobile's bottom sheet.
 * A sheet is a phone convention for a screen that has no room; this one does,
 * and the command is the only thing a first-run user can act on at all.
 */
export function AgentsEmptyState({ onAddManually }: AgentsEmptyStateProps) {
  // The agent's `MOBAIDE_AGENT_PAIR_WEB_ORIGIN` names the web client its
  // printed link points at, which is this one — so a correctly configured
  // deployment prints exactly this origin, and a self-hoster is not told to
  // look for an `app.mobaide.com` line their agent never wrote.
  const webOrigin = window.location.origin;

  return (
    <section className="setup-guide" aria-labelledby="agents-setup-title">
      <h2 id="agents-setup-title" className="setup-title">
        No agents yet
      </h2>
      <p className="page-note">
        An agent is the Mobaide process running on the computer you want to work on.
      </p>

      <ol className="setup-steps">
        <SetupStep number={1} title="Install the agent on your computer">
          <p className="setup-step-body">
            Run one of these on the machine you want to work on — not here.
          </p>
          {INSTALL_COMMANDS.map((entry) => (
            <CopyableCode key={entry.label} label={entry.label} value={entry.command} />
          ))}
          <p className="setup-step-body">
            When the install finishes the agent prints a pairing QR — that one is for
            the Android and iOS apps — and, under it, these two lines:
          </p>
          {/*
            A sample, never a control: no copy button, and the payload carries
            `<placeholders>` that `parseMoba1` rejects outright, so copying it by
            hand cannot produce something that looks like a real code.
          */}
          <figure className="terminal-sample" data-testid="setup-example-output">
            <figcaption className="terminal-sample-label">Example output</figcaption>
            <pre className="terminal-sample-body">{pairingHintExample(webOrigin)}</pre>
          </figure>
        </SetupStep>

        <SetupStep number={2} title="Open the link">
          <p className="setup-step-body">
            Copy the <code>{pairingLinkPrefix(webOrigin)}…</code> line and open it in a
            browser. It goes straight to the pairing confirmation.
          </p>
        </SetupStep>

        <SetupStep number={3} title="Or paste the pairing code">
          <PairingPasteForm />
        </SetupStep>
      </ol>

      <p className="page-note">
        Both expire a few minutes after the agent prints them. To print a fresh pair,
        run this on the host:
      </p>
      <CopyableCode label="Re-print the pairing code" value={SHOW_QR_COMMAND} />

      <div className="setup-fallbacks">
        <h3 className="setup-fallbacks-title">Other ways to add an agent</h3>
        <ul>
          <li>
            <Link to="/agents/pair">Type a code into the host instead</Link> — for a
            machine whose screen you cannot copy off, like an SSH session.
          </li>
          <li>
            <button type="button" className="link-button" onClick={onAddManually}>
              Add manually with a token
            </button>{' '}
            — you then paste the token into the agent&rsquo;s <code>.env</code> yourself.
          </li>
        </ul>
      </div>
    </section>
  );
}

interface SetupStepProps {
  number: number;
  title: string;
  children: ReactNode;
}

/**
 * One numbered step. The ordinal is its own element rather than `list-style`
 * text so it can be the filled brand square iOS `PixelNumberedStep` draws — the
 * pixel kit has no circles, and a CSS marker cannot be given a background.
 */
function SetupStep({ number, title, children }: SetupStepProps) {
  return (
    <li className="setup-step">
      <span className="setup-step-ordinal" aria-hidden="true">
        {number}
      </span>
      <div className="setup-step-main">
        <h3 className="setup-step-title">
          <span className="visually-hidden">{`Step ${String(number)}: `}</span>
          {title}
        </h3>
        {children}
      </div>
    </li>
  );
}

/**
 * The paste box, which hands off to `/pair` rather than pairing here.
 *
 * `/pair` already owns the confirm → claim → wait machine and the trust-pin
 * ordering that machine's tests pin; a second copy of it on this screen is the
 * kind of duplicate that drifts into a security bug. So this validates only far
 * enough to avoid navigating on garbage, then re-encodes the payload into the
 * fragment shape the agent's own printed link uses — the deep-link path `/pair`
 * already captures, stashes and scrubs.
 */
function PairingPasteForm() {
  const navigate = useNavigate();
  const [pasted, setPasted] = useState('');
  const [error, setError] = useState<string | undefined>(undefined);

  return (
    <form
      onSubmit={(event) => {
        event.preventDefault();
        // Accepts the payload, the whole link, or a bare fragment — all three
        // land on clipboards, because the agent prints two of them together.
        const payload = readPairingInput(pasted);
        if (payload === null) {
          setError(MALFORMED_PAYLOAD_MESSAGE);
          return;
        }
        setError(undefined);
        void navigate(`/pair#${encodePairingFragment(payload)}`);
      }}
    >
      <TextAreaField
        label="Pairing code or link"
        value={pasted}
        onChange={(value) => {
          setPasted(value);
          setError(undefined);
        }}
        error={error}
        placeholder="MOBA1|https://…"
        hint="Paste the MOBA1|… line, or the link — either works. Line breaks from a wrapped terminal are fine."
        className="pair-input"
        autoCapitalize="off"
        autoCorrect="off"
      />
      <div className="button-row">
        <button
          type="submit"
          className="button button-primary"
          disabled={pasted.trim() === ''}
        >
          Pair
        </button>
      </div>
    </form>
  );
}
