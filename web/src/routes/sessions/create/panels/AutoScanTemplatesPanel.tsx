import { useId, useRef, useState } from 'react';
import { MenuButton, type MenuGroup } from '../../../../components/MenuButton';
import { Modal } from '../../../../components/Modal';
import { TextField } from '../../../../components/form/TextField';
import type { AutoScanTemplate } from '../../../../api/autoscanRepository';
import {
  autoScanTemplateSaveBlocker,
  autoScanTemplateSummary,
  cleanTemplateName,
  templateNameError,
  templateNameKey,
} from '../autoScanSetup';
import { autoScanTemplateSettings } from '../buildCreateRequest';
import type { AutoScanTemplatesView } from '../useAutoScanTemplates';
import type { WizardAction, WizardState } from '../wizardState';

interface AutoScanTemplatesPanelProps {
  templates: AutoScanTemplatesView;
  state: WizardState;
  dispatch: (action: WizardAction) => void;
}

/**
 * Named sets of Auto Scan choices saved on the PC: pick one to fill the whole
 * wizard at once, or save what the wizard holds now. A failure here stays in
 * this card and never keeps a scan from starting.
 */
export function AutoScanTemplatesPanel({ templates, state, dispatch }: AutoScanTemplatesPanelProps) {
  const headingId = useId();
  const triggerRef = useRef<HTMLButtonElement>(null);
  // The template the wizard last took or saved, by name: it labels the
  // picker and prefills the save dialog.
  const [current, setCurrent] = useState<string | null>(null);
  const [notice, setNotice] = useState('');
  const [saving, setSaving] = useState(false);
  const [deleting, setDeleting] = useState<AutoScanTemplate | null>(null);
  const [deleteError, setDeleteError] = useState<{ template: AutoScanTemplate; message: string } | null>(null);

  const apply = (template: AutoScanTemplate) => {
    dispatch({ type: 'applyAutoScanSettings', settings: template.settings });
    setCurrent(template.name);
    setDeleteError(null);
    setNotice(`Applied “${template.name}”.`);
  };

  const remove = (template: AutoScanTemplate) => {
    setDeleteError(null);
    templates.remove(template.id).then(
      () => {
        if (current !== null && templateNameKey(current) === templateNameKey(template.name)) {
          setCurrent(null);
        }
        setNotice(`Deleted “${template.name}”.`);
      },
      (error: unknown) => {
        // An error and an older success notice must never show side by side.
        setNotice('');
        setDeleteError({
          template,
          message: error instanceof Error ? error.message : String(error),
        });
      },
    );
  };

  const list = templates.templates;
  const groups: MenuGroup[] = [
    {
      id: 'apply',
      label: 'Apply a template',
      items: list.map((template) => {
        const summary = autoScanTemplateSummary(template.settings);
        return {
          id: `apply-${template.id}`,
          label: template.name,
          description: summary !== '' ? summary : undefined,
          onSelect: () => {
            apply(template);
          },
        };
      }),
    },
    {
      id: 'delete',
      label: 'Delete a template',
      items: list.map((template) => ({
        id: `delete-${template.id}`,
        label: `Delete “${template.name}”…`,
        onSelect: () => {
          setDeleting(template);
        },
      })),
    },
  ];
  const pickerText = current ?? 'Choose a template';

  return (
    <section className="option-group autoscan-templates" aria-labelledby={headingId}>
      <h3 id={headingId}>Template</h3>
      <div className="autoscan-templates-row">
        {templates.status === 'loading' ? (
          <button type="button" className="button autoscan-template-picker" disabled>
            <span className="state-loading">Loading templates</span>
          </button>
        ) : list.length === 0 ? (
          <button type="button" className="button autoscan-template-picker" disabled>
            Choose a template
          </button>
        ) : (
          <MenuButton
            label={`Template: ${pickerText}`}
            groups={groups}
            triggerRef={triggerRef}
            className="button autoscan-template-picker"
            menuClassName="autoscan-template-menu"
          >
            <span className="autoscan-template-picker-text">{pickerText}</span>
            <span aria-hidden="true">▾</span>
          </MenuButton>
        )}
        {/* Only with the list in hand: it is what warns that a name replaces a template. */}
        <button
          type="button"
          className="button"
          disabled={templates.status !== 'ready'}
          onClick={() => {
            setSaving(true);
          }}
        >
          Save as template…
        </button>
      </div>

      {templates.status === 'ready' && list.length === 0 && (
        <p className="field-hint">No templates yet. Set up a scan, then save it as a template.</p>
      )}
      {templates.status === 'error' && (
        <div className="autoscan-inline-error">
          <p className="field-error">Couldn’t load templates: {templates.loadError}</p>
          <button type="button" className="button" onClick={templates.retryLoad}>
            Retry
          </button>
        </div>
      )}
      {deleteError !== null && (
        <div className="autoscan-inline-error">
          <p className="field-error">
            Couldn’t delete “{deleteError.template.name}”: {deleteError.message}
          </p>
          <button
            type="button"
            className="button"
            disabled={templates.deleting}
            onClick={() => {
              remove(deleteError.template);
            }}
          >
            Retry
          </button>
        </div>
      )}
      {/* Always mounted, so a screen reader hears each new notice. */}
      <p className="field-hint autoscan-template-notice" role="status">
        {notice}
      </p>

      {saving && (
        <SaveTemplateDialog
          initialName={current ?? ''}
          templates={list}
          problem={autoScanTemplateSaveBlocker({
            setup: state.autoScanSetup,
            prompt: state.autoScan.initialPrompt,
            antigravityAddDirs: state.autoScanAgents.antigravity.addDirsDraft,
          })}
          busy={templates.saving}
          onSave={async (name) => {
            await templates.save(name, autoScanTemplateSettings(state));
            setCurrent(name);
            setDeleteError(null);
            setNotice(`Saved “${name}”.`);
            setSaving(false);
          }}
          onFailed={() => {
            setNotice('');
          }}
          onClose={() => {
            setSaving(false);
          }}
        />
      )}
      {deleting !== null && (
        <DeleteTemplateDialog
          template={deleting}
          onConfirm={() => {
            const template = deleting;
            setDeleting(null);
            remove(template);
          }}
          onCancel={() => {
            setDeleting(null);
          }}
        />
      )}
    </section>
  );
}

/**
 * Two error places: a bad name shows under the Name field; anything about the
 * settings — what `problem` names before sending, or what the PC answers —
 * shows in the dialog body, since the Name field cannot fix it.
 */
function SaveTemplateDialog({
  initialName,
  templates,
  problem,
  busy,
  onSave,
  onFailed,
  onClose,
}: {
  initialName: string;
  templates: readonly AutoScanTemplate[];
  /** Why the current settings cannot be saved (`autoScanTemplateSaveBlocker`), or `null`. */
  problem: string | null;
  busy: boolean;
  onSave: (name: string) => Promise<void>;
  onFailed: () => void;
  onClose: () => void;
}) {
  const titleId = useId();
  const [name, setName] = useState(initialName);
  const [error, setError] = useState<string | undefined>(undefined);
  const [saveError, setSaveError] = useState<string | null>(null);
  const formError = problem ?? saveError;
  const clean = cleanTemplateName(name);
  const replaces =
    clean === ''
      ? undefined
      : templates.find((template) => templateNameKey(template.name) === templateNameKey(clean));

  return (
    <Modal labelledBy={titleId} dismissible={!busy} onClose={onClose}>
      <form
        className="autoscan-template-form"
        onSubmit={(event) => {
          event.preventDefault();
          if (busy || problem !== null) return;
          const nameError = templateNameError(name);
          if (nameError !== null) {
            setError(nameError);
            return;
          }
          setError(undefined);
          setSaveError(null);
          onSave(clean).catch((cause: unknown) => {
            setSaveError(cause instanceof Error ? cause.message : String(cause));
            onFailed();
          });
        }}
      >
        <h2 id={titleId}>Save as template</h2>
        <p className="field-hint">
          Keeps where to find issues, the filter, the AI agent with its options, and the
          instructions. It also keeps the session view and the time between scans. Templates
          are saved on the PC, for every workspace.
        </p>
        {formError !== null && (
          <p className="field-error" role="alert">
            {formError}
          </p>
        )}
        <TextField
          label="Name"
          autoComplete="off"
          autoFocus
          value={name}
          error={error}
          hint={replaces !== undefined ? `Replaces the template “${replaces.name}”.` : undefined}
          onChange={(value) => {
            setName(value);
            setError(undefined);
          }}
        />
        <div className="button-row">
          <button
            type="submit"
            className="button button-primary"
            disabled={busy || clean === '' || problem !== null}
          >
            {busy ? 'Saving…' : replaces !== undefined ? 'Replace' : 'Save'}
          </button>
          <button type="button" className="button" disabled={busy} onClick={onClose}>
            Cancel
          </button>
        </div>
      </form>
    </Modal>
  );
}

function DeleteTemplateDialog({
  template,
  onConfirm,
  onCancel,
}: {
  template: AutoScanTemplate;
  onConfirm: () => void;
  onCancel: () => void;
}) {
  const titleId = useId();
  const cancelRef = useRef<HTMLButtonElement>(null);
  return (
    <Modal role="alertdialog" labelledBy={titleId} initialFocus={cancelRef} onClose={onCancel}>
      <h2 id={titleId}>Delete template “{template.name}”?</h2>
      <p>The template is removed from the PC. Scans that are running keep their settings.</p>
      <div className="button-row">
        <button type="button" className="button button-danger" onClick={onConfirm}>
          Delete
        </button>
        <button type="button" ref={cancelRef} className="button" onClick={onCancel}>
          Cancel
        </button>
      </div>
    </Modal>
  );
}
