/**
 * The seam between the wizard's per-mode buckets and the persisted last-used
 * blobs: one function reads a stored blob into wizard state, the other
 * snapshots wizard state for storage.
 *
 * Both are pure, so every restore rule — bucket independence, the Antigravity
 * interlock, the effort clamp, which fields are per-session — is asserted
 * without a DOM. `NewSession.test.tsx` covers only what needs the component:
 * the seed on mount, the pin across an in-flight create, and the project
 * pre-fill.
 */
import type {
  ClaudeLaunchPrefs,
  LaunchPrefs,
  LaunchPrefsSnapshot,
} from '../../../stores/launchPrefsStore';
import {
  effectiveClaudeEffort,
  type ClaudeBucket,
  type ClaudeBucketId,
  type WizardMode,
  type WizardState,
} from './wizardState';

function restoreClaudeBucket(
  base: ClaudeBucket,
  saved: ClaudeLaunchPrefs | null,
  bucketId: ClaudeBucketId,
): ClaudeBucket {
  if (saved === null) return base;
  /*
   * Auto Scan only, matching both mobile clients: it is the only branch with a
   * Plan mode control (`AutoScanOptionsPanel`). Restoring it into the Claude
   * bucket would be the one field in the wizard that a hand-edited blob could
   * put on the wire with nothing on screen to show for it.
   */
  const restorePlanMode = bucketId === 'autoScan';
  // Every field listed rather than spread over `base`, for the mirror of the
  // reason `launchPrefsFromState`'s Codex arm gives: with a spread, a field
  // added to `ClaudeBucket` later would compile and silently never be restored
  // instead of forcing a decision here.
  const restored: ClaudeBucket = {
    model: saved.model,
    effort: saved.effort,
    fastMode: saved.fastMode,
    ultracode: saved.ultracode,
    debug: saved.debug,
    focusMode: saved.focusMode,
    appendSystemPrompt: saved.appendSystemPrompt,
    skipPermissions: saved.skipPermissions,
    noMemory: saved.noMemory,
    planMode: restorePlanMode ? saved.planMode : base.planMode,
    // Per-session: Auto Scan seeds it from the project's `auto_scan_prompt`.
    initialPrompt: base.initialPrompt,
    // Auto Scan always runs in the terminal and has no such toggle.
    terminalUi: bucketId === 'claude' ? saved.terminalUi : base.terminalUi,
  };
  // Display-only clamp, and the one place web improves on mobile: the effort
  // ladder narrows with the model, and both mobile clients restore the stored
  // effort raw, which renders their picker blank when it no longer fits. The
  // wire is unaffected either way — `buildCreateRequest` already passes every
  // effort through this same helper.
  return { ...restored, effort: effectiveClaudeEffort(restored) };
}

/**
 * Seed the wizard from the last successful launch of each kind.
 *
 * `base` stays the source of every default, so a `null` bucket is simply left
 * alone — that is what keeps Auto Scan's `skipPermissions: true` alive for a
 * profile that has never run a scan, while a stored `false` still wins.
 */
export function applyLaunchPrefs(base: WizardState, prefs: LaunchPrefs): WizardState {
  const antigravity = prefs.antigravity;
  return {
    ...base,
    claude: restoreClaudeBucket(base.claude, prefs.claude, 'claude'),
    autoScan: restoreClaudeBucket(base.autoScan, prefs.autoScan, 'autoScan'),
    codex:
      prefs.codex === null
        ? base.codex
        : {
            model: prefs.codex.model,
            sandbox: prefs.codex.sandbox,
            approval: prefs.codex.approval,
            fullAuto: prefs.codex.fullAuto,
            reasoningEffort: prefs.codex.reasoningEffort,
            terminalUi: prefs.codex.terminalUi,
          },
    antigravity:
      antigravity === null
        ? base.antigravity
        : {
            model: antigravity.model,
            skipPermissions: antigravity.skipPermissions,
            // The server 422s the pair, so the mutual exclusion the reducer
            // enforces has to be re-applied on the way back in — exactly as
            // both mobile clients do on restore.
            sandbox: antigravity.sandbox && !antigravity.skipPermissions,
            // Additional directories are per-session, never persisted.
            addDirsDraft: base.antigravity.addDirsDraft,
          },
  };
}

function claudePrefsFrom(bucket: ClaudeBucket): ClaudeLaunchPrefs {
  // The **raw** selection, not the effective one (Android's choice; iOS stores
  // the effective values). Turning Fast mode back off then returns the model
  // the user actually picked instead of the `opus` it implied.
  return {
    model: bucket.model,
    effort: bucket.effort,
    fastMode: bucket.fastMode,
    ultracode: bucket.ultracode,
    appendSystemPrompt: bucket.appendSystemPrompt,
    skipPermissions: bucket.skipPermissions,
    noMemory: bucket.noMemory,
    planMode: bucket.planMode,
    debug: bucket.debug,
    focusMode: bucket.focusMode,
    terminalUi: bucket.terminalUi,
  };
}

/**
 * Snapshot the bucket a launch of `mode` should be remembered under, or `null`
 * for the modes that carry no options (Terminal and custom agent types) — the
 * same set that sends no options block on the wire.
 *
 * Taken **before** the request goes out and held by the caller. The wizard's
 * option controls stay live while a create is in flight (only Back, Create and
 * Cancel are gated), so re-reading state afterwards would file options the
 * launch never used. Rebuilding from the request instead is not the way out:
 * `buildCreateRequest` drops a blank `append_system_prompt`, which would turn
 * `''` into `undefined` on the prefill path.
 */
export function launchPrefsFromState(
  state: WizardState,
  mode: WizardMode,
): LaunchPrefsSnapshot | null {
  switch (mode) {
    case 'claude':
      return { kind: 'claude', prefs: claudePrefsFrom(state.claude) };
    case 'auto_scan':
      return { kind: 'autoScan', prefs: claudePrefsFrom(state.autoScan) };
    case 'codex':
      // Listed rather than spread, like the other three: a spread satisfies the
      // target type without an excess-property check, so a field added to
      // `CodexBucket` later would silently start being persisted.
      return {
        kind: 'codex',
        prefs: {
          model: state.codex.model,
          sandbox: state.codex.sandbox,
          approval: state.codex.approval,
          fullAuto: state.codex.fullAuto,
          reasoningEffort: state.codex.reasoningEffort,
          terminalUi: state.codex.terminalUi,
        },
      };
    case 'antigravity':
      return {
        kind: 'antigravity',
        prefs: {
          model: state.antigravity.model,
          skipPermissions: state.antigravity.skipPermissions,
          sandbox: state.antigravity.sandbox,
        },
      };
    default:
      return null;
  }
}
