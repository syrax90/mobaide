import { AUTO_SCAN_ASK_HINT } from '../autoScanSetup';

interface ScanAskHintProps {
  show: boolean;
  /** Replaces the "When off, …" sentence, for an agent without an on/off switch. */
  text?: string;
  /** Not indented under a checkbox label: for a hint under a row of presets. */
  flush?: boolean;
}

/**
 * Under an agent's "run without asking" control in Auto Scan, while the agent
 * can ask: nobody watches a scan's sessions, so a session that asks waits.
 */
export function ScanAskHint({ show, text = AUTO_SCAN_ASK_HINT, flush = false }: ScanAskHintProps) {
  if (!show) return null;
  return <p className={`autoscan-ask-hint${flush ? ' autoscan-ask-hint-flush' : ''}`}>{text}</p>;
}
