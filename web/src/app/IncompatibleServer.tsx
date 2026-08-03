import type { IncompatibleServerInfo } from '../auth/authState';

/**
 * Terminal update-required takeover, mirroring Android's
 * `IncompatibleServerScreen` and its iOS twin.
 *
 * Rendered *above* the router rather than as a route, so no in-app navigation
 * can escape it. Unlike the mobile clients there is no store to send the user
 * to — the SPA is served from the same deploy as the relay, so a reload is the
 * update.
 */
interface IncompatibleServerProps {
  info: IncompatibleServerInfo;
}

export function IncompatibleServer({ info }: IncompatibleServerProps) {
  return (
    <div className="takeover">
      <h1>Update required</h1>
      <p>{info.reason}</p>
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
        If this keeps happening, the page you have loaded is older than the
        server. A hard refresh picks up the current build.
      </p>
    </div>
  );
}
