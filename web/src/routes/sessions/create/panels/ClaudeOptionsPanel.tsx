import { CheckboxField } from '../../../../components/form/CheckboxField';
import { SelectField, type SelectOption } from '../../../../components/form/SelectField';
import { TextAreaField } from '../../../../components/form/TextAreaField';
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
}

export function modelOptions(
  models: readonly string[] | null,
  fallback: readonly string[],
  defaultLabel = 'Default',
): SelectOption[] {
  return [
    { value: null, label: defaultLabel },
    ...(models ?? fallback).map((model) => ({ value: model, label: model })),
  ];
}

/**
 * Shared by the Claude Code and Auto Scan branches, parameterised by which
 * bucket it writes to — the two hold completely separate values.
 */
export function ClaudeOptionsPanel({
  bucket,
  bucketId,
  models,
  dispatch,
  skipPermissionsHint,
}: ClaudeOptionsPanelProps) {
  const efforts = claudeEffortsFor(effectiveClaudeModel(bucket));

  return (
    <div className="option-group">
      <SelectField
        label="Model"
        value={effectiveClaudeModel(bucket)}
        options={modelOptions(models, CLAUDE_MODELS)}
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
      <CheckboxField
        label="Skip permissions"
        checked={bucket.skipPermissions}
        hint={skipPermissionsHint}
        onChange={(value) => {
          dispatch({ type: 'setClaudeFlag', bucket: bucketId, flag: 'skipPermissions', value });
        }}
      />
      <CheckboxField
        label="Fast mode (Opus only)"
        checked={bucket.fastMode}
        onChange={(value) => {
          dispatch({ type: 'setClaudeFlag', bucket: bucketId, flag: 'fastMode', value });
        }}
      />
      <CheckboxField
        label="Ultracode"
        checked={bucket.ultracode}
        onChange={(value) => {
          dispatch({ type: 'setClaudeFlag', bucket: bucketId, flag: 'ultracode', value });
        }}
      />
      <CheckboxField
        label="No memory"
        checked={bucket.noMemory}
        onChange={(value) => {
          dispatch({ type: 'setClaudeFlag', bucket: bucketId, flag: 'noMemory', value });
        }}
      />
      <TextAreaField
        label="Append system prompt (optional)"
        value={bucket.appendSystemPrompt}
        onChange={(value) => {
          dispatch({
            type: 'setClaudeText',
            bucket: bucketId,
            field: 'appendSystemPrompt',
            value,
          });
        }}
      />
    </div>
  );
}
