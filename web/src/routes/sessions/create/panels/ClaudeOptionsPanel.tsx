import { CheckboxField } from '../../../../components/form/CheckboxField';
import { SelectField, type SelectOption } from '../../../../components/form/SelectField';
import {
  claudeEffortsFor,
  CLAUDE_MODELS,
  effectiveClaudeModel,
  type ClaudeBucket,
  type ClaudeBucketId,
  type ClaudeEffort,
  type WizardAction,
} from '../wizardState';

interface ClaudeOptionsPanelProps {
  bucket: ClaudeBucket;
  bucketId: ClaudeBucketId;
  models: readonly string[] | null;
  dispatch: (action: WizardAction) => void;
  /** Auto Scan explains what turning it off costs; the Claude branch does not. */
  skipPermissionsHint?: string;
  /** False when the session opens in the chat view, whose Focus view is in its ⋯ menu. */
  showTerminalOptions?: boolean;
}

/**
 * `selected` is always included, even when the agent's advertised list omits it.
 *
 * The probe resolves after the first render, so a model can be chosen from the
 * hardcoded fallback and then vanish when a narrower advertised list (an org
 * allowlist, say) arrives. A controlled `<select>` whose value matches no option
 * renders blank while the reducer keeps the value and the request still sends
 * it — displayed and submitted would disagree. Keeping the option is the right
 * repair rather than reconciling the bucket, which would silently discard a
 * deliberate choice.
 */
export function modelOptions(
  models: readonly string[] | null,
  fallback: readonly string[],
  selected: string | null,
  defaultLabel = 'Default',
): SelectOption[] {
  const available = models ?? fallback;
  const known = selected === null || available.includes(selected);
  return [
    { value: null, label: defaultLabel },
    ...(known ? available : [...available, selected]).map((model) => ({
      value: model,
      label: model,
    })),
  ];
}

/**
 * The main Claude controls, shared by the Claude Code and Auto Scan branches and
 * parameterised by which bucket it writes to — the two hold completely separate
 * values. The less common flags live in `ClaudeAdvancedPanel`, which each branch
 * renders after its own main options.
 */
export function ClaudeOptionsPanel({
  bucket,
  bucketId,
  models,
  dispatch,
  skipPermissionsHint,
  showTerminalOptions = true,
}: ClaudeOptionsPanelProps) {
  const efforts = claudeEffortsFor(effectiveClaudeModel(bucket));

  return (
    <div className="option-group">
      <SelectField
        label="Model"
        value={effectiveClaudeModel(bucket)}
        options={modelOptions(models, CLAUDE_MODELS, effectiveClaudeModel(bucket))}
        // Fast mode is Opus-only, so it owns the model while it is on.
        disabled={bucket.fastMode}
        onChange={(model) => {
          dispatch({ type: 'setClaudeModel', bucket: bucketId, model });
        }}
      />
      <SelectField
        label="Effort"
        value={bucket.effort}
        options={efforts.map((effort) => ({ value: effort, label: effort }))}
        // Ultracode makes the agent omit --effort entirely.
        disabled={bucket.ultracode}
        onChange={(effort) => {
          if (effort !== null) {
            dispatch({ type: 'setClaudeEffort', bucket: bucketId, effort: effort as ClaudeEffort });
          }
        }}
      />
      {/* Plan mode has a control only in Auto Scan; the Claude Code launch never sends it. */}
      {bucketId === 'autoScan' && (
        <CheckboxField
          label="Plan mode"
          checked={bucket.planMode}
          onChange={(value) => {
            dispatch({ type: 'setClaudeFlag', bucket: bucketId, flag: 'planMode', value });
          }}
        />
      )}
      {showTerminalOptions && (
        <CheckboxField
          label="Focus view"
          checked={bucket.focusMode}
          hint="Claude Code shows only your last prompt, a one-line summary of tool calls, and the final response."
          onChange={(value) => {
            dispatch({ type: 'setClaudeFlag', bucket: bucketId, flag: 'focusMode', value });
          }}
        />
      )}
      <CheckboxField
        label="Skip permissions"
        checked={bucket.skipPermissions}
        hint={skipPermissionsHint}
        onChange={(value) => {
          dispatch({ type: 'setClaudeFlag', bucket: bucketId, flag: 'skipPermissions', value });
        }}
      />
    </div>
  );
}
