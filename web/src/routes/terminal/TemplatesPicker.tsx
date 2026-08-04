import { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import {
  groupTemplates,
  listTemplates,
  setTemplateFavorite,
  touchTemplate,
  type PromptTemplate,
} from '../../api/templateRepository';
import { queryKeys, wsBackedQueryOptions } from '../../lib/queryKeys';
import { useToastStore } from '../../stores/toastStore';

interface TemplatesPickerProps {
  onPick: (template: PromptTemplate) => void;
  onClose: () => void;
}

interface SectionProps {
  title: string;
  templates: readonly PromptTemplate[];
  onPick: (template: PromptTemplate) => void;
  onToggleFavorite: (template: PromptTemplate) => void;
  /** The row whose favourite PATCH is in flight, if any. */
  pendingFavoriteId: string | undefined;
}

function Section({
  title,
  templates,
  onPick,
  onToggleFavorite,
  pendingFavoriteId,
}: SectionProps) {
  if (templates.length === 0) return null;
  return (
    <section className="template-section">
      <h3>{title}</h3>
      <ul className="template-list">
        {templates.map((template) => (
          <li key={template.templateId} className="template-row">
            <button
              type="button"
              className="template-pick"
              onClick={() => {
                onPick(template);
              }}
            >
              <span className="template-name">{template.name}</span>
              <span className="template-body">{template.body}</span>
            </button>
            <button
              type="button"
              className="template-favorite"
              // The PATCH carries an absolute `is_favorite`, computed from the
              // value on screen — and that value only moves once the refetch
              // lands. A second click inside the round trip would send the same
              // boolean again and silently drop the user's toggle. Same
              // in-flight guard `Devices` and `GraceBanner` use.
              disabled={pendingFavoriteId === template.templateId}
              aria-pressed={template.isFavorite}
              aria-label={
                template.isFavorite
                  ? `Remove ${template.name} from favourites`
                  : `Add ${template.name} to favourites`
              }
              onClick={() => {
                onToggleFavorite(template);
              }}
            >
              {template.isFavorite ? '★' : '☆'}
            </button>
          </li>
        ))}
      </ul>
    </section>
  );
}

/**
 * Read-only prompt-template picker. Creating, editing and deleting templates
 * belong to WEB-09; this is the terminal's entry point into the list.
 *
 * Android refreshes and *then* opens the sheet, iOS opens and fetches inside it.
 * The web client takes Android's shape via `refetch()` on mount, because the
 * query is `staleTime: Infinity` (the user-events bridge invalidates it), so
 * without a deliberate fetch a cache warmed an hour ago would render as truth.
 */
export function TemplatesPicker({ onPick, onClose }: TemplatesPickerProps) {
  const queryClient = useQueryClient();
  const [filter, setFilter] = useState('');

  const templates = useQuery({
    queryKey: queryKeys.templates(),
    queryFn: listTemplates,
    refetchOnMount: 'always',
    ...wsBackedQueryOptions,
  });

  const favorite = useMutation({
    mutationFn: (template: PromptTemplate) =>
      setTemplateFavorite(template.templateId, !template.isFavorite),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: queryKeys.templates() });
    },
    onError: () => {
      useToastStore.getState().show('Failed to update template', 'error');
    },
  });

  const pendingFavoriteId = favorite.isPending
    ? favorite.variables.templateId
    : undefined;
  const grouped = groupTemplates(templates.data ?? [], filter);
  const empty =
    grouped.recents.length === 0 &&
    grouped.favorites.length === 0 &&
    grouped.others.length === 0;

  const pick = (template: PromptTemplate): void => {
    onPick(template);
    // Fire-and-forget: the insert already happened, and a failed stamp only
    // costs this template its place in the recents group.
    void touchTemplate(template.templateId)
      .then(() => queryClient.invalidateQueries({ queryKey: queryKeys.templates() }))
      .catch(() => undefined);
  };

  return (
    <div className="template-picker" role="dialog" aria-modal="true" aria-label="Templates">
      <header className="template-picker-head">
        <h2>Templates</h2>
        <button type="button" className="button" onClick={onClose}>
          Close
        </button>
      </header>

      <input
        type="search"
        className="field-input"
        value={filter}
        placeholder="Filter by name or body"
        aria-label="Filter templates"
        onChange={(event) => {
          setFilter(event.target.value);
        }}
      />

      {templates.isPending && <p>Loading templates…</p>}
      {templates.isError && <p className="auth-error">Failed to load templates.</p>}
      {!templates.isPending && empty && (
        <p className="empty-state">No templates match.</p>
      )}

      <Section
        title="Recent"
        templates={grouped.recents}
        onPick={pick}
        onToggleFavorite={favorite.mutate}
        pendingFavoriteId={pendingFavoriteId}
      />
      <Section
        title="Favourites"
        templates={grouped.favorites}
        onPick={pick}
        onToggleFavorite={favorite.mutate}
        pendingFavoriteId={pendingFavoriteId}
      />
      <Section
        title="All"
        templates={grouped.others}
        onPick={pick}
        onToggleFavorite={favorite.mutate}
        pendingFavoriteId={pendingFavoriteId}
      />
    </div>
  );
}
