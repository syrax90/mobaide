import { CheckboxField } from '../../../../components/form/CheckboxField';
import { SelectField } from '../../../../components/form/SelectField';
import { TextAreaField } from '../../../../components/form/TextAreaField';
import {
  addDirsError,
  ANTIGRAVITY_MODELS,
  type AntigravityBucket,
  type WizardAction,
} from '../wizardState';
import { modelOptions } from './ClaudeOptionsPanel';

interface AntigravityOptionsPanelProps {
  bucket: AntigravityBucket;
  models: readonly string[] | null;
  dispatch: (action: WizardAction) => void;
}

export function AntigravityOptionsPanel({
  bucket,
  models,
  dispatch,
}: AntigravityOptionsPanelProps) {
  return (
    <div className="option-group">
      <SelectField
        label="Model"
        value={bucket.model}
        options={modelOptions(models, ANTIGRAVITY_MODELS, bucket.model, 'Auto')}
        onChange={(model) => {
          dispatch({ type: 'setAntigravityModel', model });
        }}
      />
      {/*
        Mutually exclusive: --dangerously-skip-permissions auto-approves sandbox
        escapes, so the pair is a 422 server-side. The reducer enforces it too —
        disabling alone would not survive a restored preference.
      */}
      <CheckboxField
        label="Skip permissions (dangerous)"
        checked={bucket.skipPermissions}
        disabled={bucket.sandbox}
        onChange={(value) => {
          dispatch({ type: 'setAntigravitySkipPermissions', value });
        }}
      />
      <CheckboxField
        label="Sandbox"
        checked={bucket.sandbox}
        disabled={bucket.skipPermissions}
        onChange={(value) => {
          dispatch({ type: 'setAntigravitySandbox', value });
        }}
      />
      <TextAreaField
        label="Additional directories"
        value={bucket.addDirsDraft}
        placeholder="/one/path, /another/path"
        hint="One per line, or comma-separated."
        error={addDirsError(bucket.addDirsDraft) ?? undefined}
        onChange={(draft) => {
          dispatch({ type: 'setAntigravityAddDirs', draft });
        }}
      />
    </div>
  );
}
