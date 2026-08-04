import { CopyableCode } from '../../components/CopyableCode';
import { INSTALL_COMMANDS } from './hostOs';

/**
 * The installer one-liners, both shown at once.
 *
 * Not OS-detected, deliberately, and for a reason the mobile clients spell out:
 * the host being installed onto is a different machine from the one running
 * this screen.
 */
interface InstallCommandsPanelProps {
  onClose: () => void;
}

export function InstallCommandsPanel({ onClose }: InstallCommandsPanelProps) {
  return (
    <section
      className="confirm-panel"
      role="dialog"
      aria-labelledby="install-commands-title"
    >
      <h2 id="install-commands-title">Install mobaide-agent on a host</h2>
      <p>
        Run one of these on the machine you want to use as a host. After the install
        finishes, the agent prints a pairing code — bring it back here.
      </p>
      {INSTALL_COMMANDS.map((entry) => (
        <CopyableCode key={entry.label} label={entry.label} value={entry.command} />
      ))}
      <div className="button-row">
        <button type="button" className="button" onClick={onClose}>
          Close
        </button>
      </div>
    </section>
  );
}
