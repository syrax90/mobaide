/**
 * The Auto Scan templates saved on the PC, and saving and deleting them.
 *
 * Both writes answer with every template, so the list is written from the
 * answer rather than read again. Failures stay inline in the templates card:
 * a template is a convenience, and nothing about it may keep a scan from
 * starting.
 */
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import {
  deleteAutoScanTemplate,
  listAutoScanTemplates,
  saveAutoScanTemplate,
  type AutoScanTemplate,
  type AutoScanTemplateSettings,
} from '../../../api/autoscanRepository';
import { isApiError } from '../../../api/errors';
import { queryKeys } from '../../../lib/queryKeys';
import { errorDetailString } from './createErrors';

export interface AutoScanTemplatesView {
  status: 'loading' | 'ready' | 'error';
  templates: readonly AutoScanTemplate[];
  loadError: string | null;
  retryLoad: () => void;
  /** Resolves with the saved list; rejects with a message to show. */
  save: (name: string, settings: AutoScanTemplateSettings) => Promise<AutoScanTemplate[]>;
  saving: boolean;
  /**
   * Resolves with the list left — also when another client deleted the
   * template first (404); rejects with a message to show.
   */
  remove: (templateId: string) => Promise<AutoScanTemplate[]>;
  deleting: boolean;
}

/** The agent's own sentence when it sent one (the 409 at the cap does), else the HTTP error. */
export function templateErrorMessage(error: unknown): string {
  if (isApiError(error)) {
    const detail = errorDetailString(error.body);
    if (detail !== null) return detail;
    if (error.status === 422) {
      return 'The PC did not accept these settings. Check the filter and the agent options.';
    }
    return error.message;
  }
  return error instanceof Error ? error.message : String(error);
}

export function useAutoScanTemplates(agentId: string, enabled: boolean): AutoScanTemplatesView {
  const queryClient = useQueryClient();
  const key = queryKeys.autoscanTemplates(agentId);
  const list = useQuery({
    queryKey: key,
    queryFn: () => listAutoScanTemplates(agentId),
    enabled,
    // Rendered inline; a toast would repeat it.
    meta: { silent: true },
  });

  // The page stays mounted when the route switches to another agent, and a
  // pending mutation then runs this render's callbacks: the agent travels in
  // the variables, so the answer lands in the list of the PC that sent it.
  const write = (forAgent: string, templates: AutoScanTemplate[]) => {
    queryClient.setQueryData<AutoScanTemplate[]>(queryKeys.autoscanTemplates(forAgent), templates);
  };
  const save = useMutation({
    mutationFn: (input: { agentId: string; name: string; settings: AutoScanTemplateSettings }) =>
      saveAutoScanTemplate(input.agentId, input.name, input.settings),
    onSuccess: (templates, input) => {
      write(input.agentId, templates);
    },
  });
  const remove = useMutation({
    mutationFn: (input: { agentId: string; templateId: string }) =>
      deleteAutoScanTemplate(input.agentId, input.templateId),
    onSuccess: (templates, input) => {
      write(input.agentId, templates);
    },
  });

  const rethrow = (error: unknown): never => {
    throw new Error(templateErrorMessage(error));
  };

  return {
    status: list.data !== undefined ? 'ready' : list.isError ? 'error' : 'loading',
    templates: list.data ?? [],
    loadError: list.isError ? templateErrorMessage(list.error) : null,
    retryLoad: () => {
      void list.refetch();
    },
    save: (name, settings) => save.mutateAsync({ agentId, name, settings }).catch(rethrow),
    saving: save.isPending,
    remove: (templateId) =>
      remove.mutateAsync({ agentId, templateId }).catch((error: unknown) => {
        if (!(isApiError(error) && error.status === 404)) return rethrow(error);
        // Already gone: drop it here as well, and say nothing about it.
        const left = (queryClient.getQueryData<AutoScanTemplate[]>(key) ?? []).filter(
          (template) => template.id !== templateId,
        );
        write(agentId, left);
        return left;
      }),
    deleting: remove.isPending,
  };
}
