import { useState } from 'react';
import { useNavigate } from 'react-router';

export function Home() {
  const navigate = useNavigate();
  const [agentId, setAgentId] = useState('');
  const [sessionId, setSessionId] = useState('');
  const ready = agentId.trim() !== '' && sessionId.trim() !== '';

  return (
    <section className="home">
      <h1>Mobaide Web</h1>
      <p>
        Browser client for controlling Claude Code and other CLI agents through
        the Mobaide relay. Feature screens arrive in upcoming milestones; this
        shell carries the routing, theming, data, and API foundations they
        build on.
      </p>

      {/*
        Stand-in for the sessions list (WEB-07), which will link straight to a
        terminal. Until it exists the terminal screen has no entry point, so
        this form is the only way to reach — and verify — it.
      */}
      <form
        className="home-open-session"
        onSubmit={(event) => {
          event.preventDefault();
          if (!ready) return;
          void navigate(
            `/agents/${encodeURIComponent(agentId.trim())}/sessions/${encodeURIComponent(sessionId.trim())}`,
          );
        }}
      >
        <h2>Open a terminal</h2>
        <label className="field">
          <span className="field-label">Agent id</span>
          <input
            className="field-input"
            value={agentId}
            onChange={(event) => {
              setAgentId(event.target.value);
            }}
          />
        </label>
        <label className="field">
          <span className="field-label">Session id</span>
          <input
            className="field-input"
            value={sessionId}
            onChange={(event) => {
              setSessionId(event.target.value);
            }}
          />
        </label>
        <button type="submit" className="button button-primary" disabled={!ready}>
          Attach
        </button>
      </form>
    </section>
  );
}
