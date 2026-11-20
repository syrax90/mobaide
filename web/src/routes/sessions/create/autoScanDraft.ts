/**
 * The Auto Scan choices parked while the GitHub setup terminal is open.
 *
 * Opening that terminal leaves the wizard, and the reducer does not survive the
 * unmount, so without this a user who chose a source, an agent and labels and
 * then had to install or sign in to `gh` would come back to empty fields.
 *
 * Module memory, not storage: it lives as long as the tab's SPA does, which
 * covers Back from the terminal and a fresh New session in the same tab, and it
 * never writes the initial prompt (free text) anywhere persistent. A reload
 * loses it, which only costs re-entering what the agent had not saved yet.
 * Taken once: the restore consumes the entry.
 */
import type { AutoScanDraft } from './wizardState';

const drafts = new Map<string, AutoScanDraft>();

function draftKey(agentId: string, workspaceName: string): string {
  return JSON.stringify([agentId, workspaceName]);
}

export function parkAutoScanDraft(
  agentId: string,
  workspaceName: string,
  draft: AutoScanDraft,
): void {
  drafts.set(draftKey(agentId, workspaceName), draft);
}

export function takeAutoScanDraft(agentId: string, workspaceName: string): AutoScanDraft | null {
  const key = draftKey(agentId, workspaceName);
  const draft = drafts.get(key) ?? null;
  drafts.delete(key);
  return draft;
}
