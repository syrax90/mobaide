import { useRef, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Link } from 'react-router';
import {
  createTemplate,
  deleteTemplate,
  groupForManagement,
  listTemplates,
  setTemplateFavorite,
  updateTemplate,
  type PromptTemplate,
} from '../../api/templateRepository';
import { isApiError } from '../../api/errors';
import { ErrorState } from '../../components/ErrorState';
import { Modal } from '../../components/Modal';
import { queryKeys, wsBackedQueryOptions } from '../../lib/queryKeys';
import { useToastStore } from '../../stores/toastStore';
import { TemplateEditorDialog } from './TemplateEditorDialog';

type Panel =
  | { kind: 'none' }
  | { kind: 'editor'; template: PromptTemplate | null }
  | { kind: 'confirmDelete'; template: PromptTemplate };

function errorMessage(error: unknown, fallback: string): string {
  return isApiError(error) ? `${fallback}: ${error.message}` : fallback;
}

/**
 * Prompt template management.
 *
 * Templates are central-server entities, not agent ones, so this screen needs no
 * agent picker and works while every agent is offline. The terminal's picker
 * reads the same query key, and `template.*` on the user channel invalidates it,
 * so an edit here shows up there — and on a phone — without any extra wiring.
 */
export function TemplatesScreen() {
  const queryClient = useQueryClient();
  const [panel, setPanel] = useState<Panel>({ kind: 'none' });
  const deleteCancelRef = useRef<HTMLButtonElement>(null);
  const [saveError, setSaveError] = useState<string | null>(null);

  const templates = useQuery({
    queryKey: queryKeys.templates(),
    queryFn: listTemplates,
    ...wsBackedQueryOptions,
  });

  const invalidate = async () => {
    await queryClient.invalidateQueries({ queryKey: queryKeys.templates() });
  };

  const save = useMutation({
    mutationFn: ({
      template,
      name,
      body,
    }: {
      template: PromptTemplate | null;
      name: string;
      body: string;
    }) =>
      template === null
        ? createTemplate(name, body)
        : updateTemplate(template.templateId, { name, body }),
    onSuccess: async () => {
      // Dismiss only once the server has it, so a failure keeps the draft.
      setPanel({ kind: 'none' });
      setSaveError(null);
      await invalidate();
    },
    onError: (error: unknown) => {
      setSaveError(errorMessage(error, 'Failed to save template'));
    },
  });

  const favorite = useMutation({
    mutationFn: (template: PromptTemplate) =>
      setTemplateFavorite(template.templateId, !template.isFavorite),
    onSuccess: invalidate,
    onError: (error: unknown) => {
      useToastStore
        .getState()
        .show(errorMessage(error, 'Failed to update template'), 'error');
    },
  });

  const remove = useMutation({
    mutationFn: (template: PromptTemplate) => deleteTemplate(template.templateId),
    onSuccess: async () => {
      setPanel({ kind: 'none' });
      await invalidate();
    },
    onError: (error: unknown) => {
      setPanel({ kind: 'none' });
      useToastStore
        .getState()
        .show(errorMessage(error, 'Failed to delete template'), 'error');
    },
  });

  const groups = groupForManagement(templates.data ?? []);

  const renderSection = (title: string, rows: PromptTemplate[]) =>
    rows.length === 0 ? null : (
      <section className="template-section" key={title}>
        <h2>{title}</h2>
        <ul className="template-list">
          {rows.map((template) => (
            <li key={template.templateId} className="template-row">
              <div className="template-row-text">
                <span className="template-name">{template.name}</span>
                <span className="template-body">{template.body}</span>
              </div>
              <button
                type="button"
                className="template-favorite"
                aria-label={
                  template.isFavorite
                    ? `Unfavorite ${template.name}`
                    : `Favorite ${template.name}`
                }
                aria-pressed={template.isFavorite}
                disabled={favorite.isPending}
                onClick={() => {
                  favorite.mutate(template);
                }}
              >
                {template.isFavorite ? '★' : '☆'}
              </button>
              <button
                type="button"
                className="button"
                onClick={() => {
                  setSaveError(null);
                  setPanel({ kind: 'editor', template });
                }}
              >
                Edit
              </button>
              <button
                type="button"
                className="button button-danger"
                onClick={() => {
                  setPanel({ kind: 'confirmDelete', template });
                }}
              >
                Delete
              </button>
            </li>
          ))}
        </ul>
      </section>
    );

  return (
    <section className="page">
      <header className="page-header">
        <h1>Prompt templates</h1>
        <Link to="/">Back to sessions</Link>
      </header>

      <p className="page-note">
        Reusable prompts, shared across every device on your account. Insert one from
        the terminal&rsquo;s composer.
      </p>

      <div className="sessions-toolbar">
        <button
          type="button"
          className="button button-primary"
          onClick={() => {
            setSaveError(null);
            setPanel({ kind: 'editor', template: null });
          }}
        >
          New template
        </button>
      </div>

      {templates.isPending ? (
        <p className="page-note state-loading">Loading templates…</p>
      ) : templates.isError ? (
        <ErrorState
          message="Failed to load templates."
          onRetry={() => void templates.refetch()}
        />
      ) : templates.data.length === 0 ? (
        <p className="empty-state">No templates yet. Create one to get started.</p>
      ) : (
        <>
          {/* The sections are disjoint, so "All" is really "everything else" —
              a favourite appears only under Favorites. Both mobile clients use
              exactly these two headings with exactly this split. */}
          {renderSection('Favorites', groups.favorites)}
          {renderSection('All', groups.others)}
        </>
      )}

      {panel.kind === 'editor' && (
        <TemplateEditorDialog
          // Remount per target so the draft state is seeded from the right row.
          key={panel.template?.templateId ?? 'new'}
          editing={panel.template}
          busy={save.isPending}
          error={saveError}
          onSave={(name, body) => {
            save.mutate({ template: panel.template, name, body });
          }}
          onCancel={() => {
            setPanel({ kind: 'none' });
            setSaveError(null);
          }}
        />
      )}

      {panel.kind === 'confirmDelete' && (
        <Modal
          role="alertdialog"
          labelledBy="delete-template-title"
          dismissible={!remove.isPending}
          initialFocus={deleteCancelRef}
          onClose={() => {
            setPanel({ kind: 'none' });
          }}
        >
          <h2 id="delete-template-title">Delete template?</h2>
          <p>Delete &ldquo;{panel.template.name}&rdquo;? This cannot be undone.</p>
          <div className="button-row">
            <button
              type="button"
              className="button button-danger"
              disabled={remove.isPending}
              onClick={() => {
                remove.mutate(panel.template);
              }}
            >
              Delete
            </button>
            <button
              type="button"
              ref={deleteCancelRef}
              className="button"
              disabled={remove.isPending}
              onClick={() => {
                setPanel({ kind: 'none' });
              }}
            >
              Cancel
            </button>
          </div>
        </Modal>
      )}
    </section>
  );
}
