import { useEffect, useState } from 'react';
import { Link } from 'react-router';
import { APP_PROTOCOL_VERSION } from '../../api/bootstrap';
import { useServerInfo } from '../../api/useServerInfo';
import { useSessionStore } from '../../auth/sessionStore';
import {
  notificationPermission,
  requestNotificationPermission,
  type NotificationPermissionState,
} from '../../notifications/notificationService';
import { useNotificationPrefsStore } from '../../stores/notificationPrefsStore';
import { useTerminalPrefsStore } from '../../stores/terminalPrefsStore';
import {
  PRIVACY_POLICY_URL,
  SUPPORT_COMMUNITY_URL,
  supportMailtoUrl,
} from './supportLinks';

/**
 * The settings hub.
 *
 * Mobile makes this the only way to reach agents, templates and devices,
 * because a phone has no persistent nav; the browser keeps the top nav and this
 * screen adds what has nowhere else to live — notification control, the terminal
 * preference, support, and the version footer — plus links onward, so someone
 * who learned the app on a phone still finds what they expect.
 *
 * There is no terminal display-mode picker. Web ships Full display only, a
 * platform-convention divergence documented with WEB-05: a desktop window is
 * wide and fit-to-window is the browser convention, where a phone at ~40 columns
 * needs the forced grid.
 */
export function SettingsScreen() {
  const session = useSessionStore((state) => state.session);
  const invalidateUserSession = useSessionStore((state) => state.invalidateUserSession);
  const serverInfo = useServerInfo();

  const notificationsEnabled = useNotificationPrefsStore((state) => state.enabled);
  const setNotificationsEnabled = useNotificationPrefsStore((state) => state.setEnabled);
  const passCtrlV = useTerminalPrefsStore((state) => state.passCtrlVToTerminal);
  const setPassCtrlV = useTerminalPrefsStore((state) => state.setPassCtrlVToTerminal);

  // Read once on mount and after a request resolves, never polled: the value
  // only changes through the prompt below or through browser UI that reloads
  // the page anyway.
  const [permission, setPermission] = useState<NotificationPermissionState>('unsupported');
  useEffect(() => {
    setPermission(notificationPermission());
  }, []);
  const [requesting, setRequesting] = useState(false);

  return (
    <section className="page">
      <header className="page-header">
        <h1>Settings</h1>
      </header>

      <dl className="account-summary">
        <div>
          <dt>Signed in as</dt>
          <dd>{session?.email ?? '—'}</dd>
        </div>
      </dl>

      <section className="settings-card">
        <h2 className="settings-heading">Management</h2>
        <nav className="account-links settings-links">
          <Link to="/agents">My agents</Link>
          <Link to="/projects">Projects</Link>
          <Link to="/templates">Prompt templates</Link>
          <Link to="/devices">My devices</Link>
          <Link to="/account">Account</Link>
        </nav>
      </section>

      <section className="settings-card">
        <h2 className="settings-heading">Notifications</h2>
        <p className="page-note">
        Alerts when a session needs your approval, finishes its turn, or ends. A
          background tab shows a system notification; a visible one shows an in-app
          message instead. Leaving this tab open never silences the alerts on your
          phone.
        </p>
        <label className="check-row">
          <input
            type="checkbox"
            checked={notificationsEnabled}
            onChange={(changed) => {
              setNotificationsEnabled(changed.target.checked);
            }}
          />
          <span>Notify me when a session needs attention</span>
        </label>

        {permission === 'unsupported' ? (
          <p className="page-note">
            This browser has no notification support, so alerts appear in the app only.
          </p>
        ) : permission === 'granted' ? (
          <p className="page-note">System notifications are allowed.</p>
        ) : permission === 'denied' ? (
          <p className="page-note">
            System notifications are blocked for this site. Re-allow them in your
            browser’s site settings; alerts still appear in the app.
          </p>
        ) : (
          // The prompt is only ever raised from this button. Asking on load is the
          // pattern browsers auto-suppress, and it would spend the user's one
          // chance to say yes before they know what is being asked.
          <button
            type="button"
            className="button"
            disabled={requesting}
            onClick={() => {
              setRequesting(true);
              void requestNotificationPermission()
                .then(setPermission)
                .finally(() => {
                  setRequesting(false);
                });
            }}
          >
            Allow system notifications
          </button>
        )}
      </section>

      <section className="settings-card">
        <h2 className="settings-heading">Terminal</h2>
        <label className="check-row">
          <input
            type="checkbox"
            checked={passCtrlV}
            onChange={(changed) => {
              setPassCtrlV(changed.target.checked);
            }}
          />
          <span>Send Ctrl+V to the terminal instead of pasting</span>
        </label>
        <p className="page-note">
          Claude Code binds Ctrl+V for image paste. The terminal always fits its
          columns to the window — there is no fixed-column mode on the web.
        </p>
      </section>

      <section className="settings-card">
        <h2 className="settings-heading">Support</h2>
        <nav className="account-links settings-links">
          <a href={SUPPORT_COMMUNITY_URL} target="_blank" rel="noreferrer">
            Ask the community
          </a>
          <a href={supportMailtoUrl(APP_PROTOCOL_VERSION)}>Email support</a>
          <a href={PRIVACY_POLICY_URL} target="_blank" rel="noreferrer">
            Privacy policy
          </a>
        </nav>
        <p className="page-note">
          Community threads are public and need a free GitHub account. Email support
          is private and best for account-specific issues.
        </p>
      </section>

      <button
        type="button"
        className="button button-block"
        onClick={() => {
          // `manual` is the silent reason: the user asked to sign out, so the
          // login screen must not greet them with "your session expired".
          invalidateUserSession('manual');
        }}
      >
        Sign out
      </button>

      <p className="settings-version">
        Mobaide web · protocol v{APP_PROTOCOL_VERSION}
        {serverInfo.data?.serverVersion != null && ` · server ${serverInfo.data.serverVersion}`}
      </p>
    </section>
  );
}
