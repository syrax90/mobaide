import { SUPPORT_MAILTO_URL } from '../routes/settings/supportLinks';

/**
 * Terminal update-required takeover, mirroring Android's
 * `IncompatibleServerScreen` and its iOS twin.
 *
 * Rendered *above* the router rather than as a route, so no in-app navigation
 * can escape it. Unlike the mobile clients there is no store to send the user
 * to — the SPA is served from the same deploy as the relay, so a reload is the
 * update.
 *
 * `AuthState.info.reason` is deliberately not rendered, on all three clients. It
 * is the raw gate string ("Server requires protocol v8+, this app speaks v7…"),
 * and reloading is the only action available whatever the numbers say.
 *
 * The gate is set membership, not a floor (`checkVersionGate`), so it also fires
 * when this build speaks a protocol the relay has dropped — the copy must not
 * name a direction. A reload resolves both, because the SPA ships with the relay.
 *
 * The support link is web-only, and it is what the removed reason string paid
 * for: the mobile takeovers hand the user a store button that actually resolves
 * the gate, while a reload that does not clear a stale cached SPA leaves this
 * screen with no next step and no navigation to one.
 */
export function IncompatibleServer() {
  return (
    <div className="takeover">
      <h1>Update required</h1>
      <p>
        This page can no longer talk to the server. Reload to pick up the current
        build.
      </p>
      <button
        type="button"
        className="button button-primary"
        onClick={() => {
          window.location.reload();
        }}
      >
        Reload
      </button>
      <p className="takeover-note">
        If this keeps happening, a hard refresh clears the cached page, or{' '}
        <a href={SUPPORT_MAILTO_URL}>email support</a>.
      </p>
    </div>
  );
}
