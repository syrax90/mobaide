import { useEffect, useRef, useState } from 'react';
import { Link } from 'react-router';
import { CopyableCode } from '../../components/CopyableCode';
import type { AgentCredentials } from '../../api/agentRepository';
import {
  AGENT_HOST_OSES,
  DEFAULT_AGENT_HOST_OS,
  envSnippet,
  type AgentHostOs,
} from './hostOs';

interface AgentCredentialsPanelProps {
  title: string;
  credentials: AgentCredentials;
  onDone: () => void;
}

/**
 * The one and only showing of an agent token.
 *
 * Both mobile clients block every dismissal route except the explicit Done
 * button (`confirmValueChange` on Android, `interactiveDismissDisabled` on
 * iOS) because the token is never retrievable again. The web equivalent is
 * this being an inline panel with no backdrop and no Escape handler rather
 * than a `<dialog>` — a modal here would ship a focus trap and an `onCancel`
 * that a stray Escape closes, which is exactly the loss mobile designs against.
 */
export function AgentCredentialsPanel({
  title,
  credentials,
  onDone,
}: AgentCredentialsPanelProps) {
  const [hostOs, setHostOs] = useState<AgentHostOs>(DEFAULT_AGENT_HOST_OS);

  const panelRef = useRef<HTMLDivElement>(null);
  useEffect(() => {
    panelRef.current?.scrollIntoView({
      block: 'nearest',
      behavior: window.matchMedia('(prefers-reduced-motion: reduce)').matches
        ? 'auto'
        : 'smooth',
    });
  }, []);

  return (
    <section
      ref={panelRef}
      className="confirm-panel credentials-panel"
      role="dialog"
      aria-labelledby="agent-credentials-title"
    >
      <h2 id="agent-credentials-title">{title}</h2>
      <p>
        Add these to your Mobaide agent to finish connecting it. The token is shown only
        once, so copy it now.
      </p>

      <CopyableCode label="Agent ID" value={credentials.agentId} testId="cred-agent-id" />
      <CopyableCode
        label="Token (shown once)"
        value={credentials.token}
        testId="cred-token"
      />

      <h3>Apply on the agent host</h3>
      <ol className="credential-steps">
        <li>
          Set both values in the agent&rsquo;s <code>.env</code> file:
          <CopyableCode
            label=".env"
            value={envSnippet(credentials.agentId, credentials.token)}
            multiline
            testId="cred-env-block"
          />
        </li>
        <li>
          Open <code>.env</code> and restart the agent on your host OS:
          <div className="os-picker" role="group" aria-label="Host OS">
            {AGENT_HOST_OSES.map((option) => (
              <button
                key={option.id}
                type="button"
                className={`preset-card${option.id === hostOs.id ? ' preset-card-active' : ''}`}
                aria-pressed={option.id === hostOs.id}
                onClick={() => {
                  setHostOs(option);
                }}
              >
                {option.label}
              </button>
            ))}
          </div>
          <CopyableCode
            label="File location (default install)"
            value={hostOs.envPath}
            testId="cred-env-path"
          />
          <CopyableCode
            label="Restart command"
            value={hostOs.restartCommand}
            testId="cred-restart"
          />
        </li>
      </ol>
      <p className="page-note">
        Paths assume a default install. A <code>--prefix</code> install differs.
      </p>
      <p className="page-note">
        Can the agent show a pairing code? <Link to="/pair">Pairing</Link> delivers
        credentials to the agent without ever putting a token on your screen.
      </p>

      <div className="button-row">
        <button
          type="button"
          className="button button-primary"
          onClick={onDone}
          data-testid="cred-done"
        >
          Done
        </button>
      </div>
    </section>
  );
}
