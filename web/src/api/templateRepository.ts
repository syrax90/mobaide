import { relayClient } from './clients';
import { toApiError } from './errors';

export interface PromptTemplate {
  templateId: string;
  name: string;
  body: string;
  isFavorite: boolean;
  lastUsedAt: string | null;
}

/** Recents shown before favourites, matching both mobile pickers. */
const MAX_RECENTS = 5;

export interface GroupedTemplates {
  recents: PromptTemplate[];
  favorites: PromptTemplate[];
  others: PromptTemplate[];
}

function byName(a: PromptTemplate, b: PromptTemplate): number {
  return a.name.toLowerCase().localeCompare(b.name.toLowerCase());
}

/**
 * Split templates the way `TemplatesPickerSheet` does on both mobile clients:
 * the five most recently used first, then favourites that are not already in
 * that list, then everything else — the last two groups sorted by name.
 */
export function groupTemplates(
  templates: readonly PromptTemplate[],
  filter = '',
): GroupedTemplates {
  const needle = filter.trim().toLowerCase();
  const matching =
    needle === ''
      ? [...templates]
      : templates.filter(
          (template) =>
            template.name.toLowerCase().includes(needle) ||
            template.body.toLowerCase().includes(needle),
        );

  const recents = matching
    .filter((template) => template.lastUsedAt !== null)
    .sort((a, b) => (b.lastUsedAt ?? '').localeCompare(a.lastUsedAt ?? ''))
    .slice(0, MAX_RECENTS);
  const inRecents = new Set(recents.map((template) => template.templateId));
  const favorites = matching
    .filter((template) => template.isFavorite && !inRecents.has(template.templateId))
    .sort(byName);
  const inFavorites = new Set(favorites.map((template) => template.templateId));
  const others = matching
    .filter(
      (template) =>
        !inRecents.has(template.templateId) && !inFavorites.has(template.templateId),
    )
    .sort(byName);

  return { recents, favorites, others };
}

/**
 * The management screen's grouping: favourites first, then everything else,
 * each sorted case-insensitively by name.
 *
 * Deliberately not `groupTemplates`. The picker answers "what did I use lately"
 * and puts recents first; the management list answers "what do I own" and must
 * show every template exactly once, so a recently-used favourite belongs in
 * Favorites, not in a third place above it.
 */
export function groupForManagement(templates: readonly PromptTemplate[]): {
  favorites: PromptTemplate[];
  others: PromptTemplate[];
} {
  return {
    favorites: templates.filter((template) => template.isFavorite).sort(byName),
    others: templates.filter((template) => !template.isFavorite).sort(byName),
  };
}

export async function listTemplates(): Promise<PromptTemplate[]> {
  const { data, error, response } = await relayClient.GET('/api/templates');
  if (error !== undefined || data === undefined) {
    throw toApiError(response, error);
  }
  // Mapped inline off the generated `TemplateSummary`, like `listDevices`. A
  // hand-written wire interface would type-check against a stale shape after a
  // schema change, which is exactly what the type-drift CI gate exists to catch.
  return data.map((template) => ({
    templateId: template.template_id,
    name: template.name,
    body: template.body,
    isFavorite: template.is_favorite,
    lastUsedAt: template.last_used_at,
  }));
}

/**
 * Stamp `last_used_at`, which is what promotes a template into the recents
 * group on every client. Fired when a template is inserted, not when the picker
 * opens.
 */
export async function touchTemplate(templateId: string): Promise<void> {
  const { error, response } = await relayClient.POST(
    '/api/templates/{template_id}/touch',
    { params: { path: { template_id: templateId } } },
  );
  if (error !== undefined) throw toApiError(response, error);
}

export async function setTemplateFavorite(
  templateId: string,
  isFavorite: boolean,
): Promise<void> {
  await updateTemplate(templateId, { is_favorite: isFavorite });
}

export async function createTemplate(name: string, body: string): Promise<void> {
  const { error, response } = await relayClient.POST('/api/templates', {
    body: { name, body },
  });
  if (error !== undefined) throw toApiError(response, error);
}

/**
 * A partial patch: an absent field keeps its value, and an all-absent body is a
 * legal no-op the server answers without bumping `version`.
 */
export async function updateTemplate(
  templateId: string,
  patch: { name?: string; body?: string; is_favorite?: boolean },
): Promise<void> {
  const { error, response } = await relayClient.PATCH('/api/templates/{template_id}', {
    params: { path: { template_id: templateId } },
    body: patch,
  });
  if (error !== undefined) throw toApiError(response, error);
}

/** Soft-delete, and idempotent — deleting an already-deleted template still 204s. */
export async function deleteTemplate(templateId: string): Promise<void> {
  const { error, response } = await relayClient.DELETE('/api/templates/{template_id}', {
    params: { path: { template_id: templateId } },
  });
  if (error !== undefined) throw toApiError(response, error);
}
