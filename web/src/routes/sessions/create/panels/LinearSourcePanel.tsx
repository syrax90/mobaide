import { useId, useState } from 'react';
import { PasswordField } from '../../../../components/form/PasswordField';
import type { AutoScanFilter } from '../../../../protocol/dto/workspaces';
import { AutoScanFilterSection } from '../../../workspaces/AutoScanFilterSection';
import type { AutoScanSourcesView } from '../useAutoScanSources';
import type { WizardAction } from '../wizardState';

interface LinearSourcePanelProps {
  hasLinearKey: boolean;
  filter: AutoScanFilter;
  sources: AutoScanSourcesView;
  dispatch: (action: WizardAction) => void;
}

/**
 * The Linear half of the Auto Scan source step. A workspace without a Linear
 * key gets a field to save one first (it is stored with the workspace on the
 * PC, as the workspace form does); then the workspace form's filter editor,
 * starting empty when the workspace never chose a filter.
 */
export function LinearSourcePanel({ hasLinearKey, filter, sources, dispatch }: LinearSourcePanelProps) {
  if (!hasLinearKey) return <LinearKeyForm sources={sources} />;
  return (
    <AutoScanFilterSection
      title="Linear filter"
      filter={filter}
      labels={sources.linear?.labels ?? []}
      states={sources.linear?.states ?? []}
      loading={sources.linearLoading}
      error={sources.linear?.failed === true ? 'Couldn’t load Linear labels/states.' : null}
      showErrors={false}
      showWorktreeNote={false}
      onChange={(next) => {
        dispatch({ type: 'setAutoScanLinearFilter', filter: next });
      }}
      onRefresh={sources.refreshLinear}
    />
  );
}

function LinearKeyForm({ sources }: { sources: AutoScanSourcesView }) {
  const headingId = useId();
  // Local, never in the wizard's reducer: this form unmounts once the save
  // lands (`has_linear_key` turns true), which drops the typed key with it,
  // and a failed save keeps it for a retry.
  const [key, setKey] = useState('');

  return (
    <section className="option-group" aria-labelledby={headingId}>
      <h3 id={headingId}>Linear API key</h3>
      <p className="field-hint">
        This workspace has no Linear API key. Save one to read its labels and workflow
        states. The key is stored with the workspace on the PC.
      </p>
      <form
        className="autoscan-key-form"
        onSubmit={(event) => {
          event.preventDefault();
          if (key.trim() === '' || sources.linearKeySaving) return;
          sources.saveLinearKey(key);
        }}
      >
        <PasswordField
          label="Linear API key"
          autoComplete="off"
          secretNoun="key"
          value={key}
          error={sources.linearKeyError ?? undefined}
          onChange={setKey}
        />
        <button
          type="submit"
          className="button"
          disabled={key.trim() === '' || sources.linearKeySaving}
        >
          {sources.linearKeySaving ? 'Saving…' : 'Save key'}
        </button>
      </form>
    </section>
  );
}
