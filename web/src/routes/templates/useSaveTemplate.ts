import { useState } from 'react';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import { isApiError } from '../../api/errors';
import { createTemplate } from '../../api/templateRepository';
import { queryKeys } from '../../lib/queryKeys';
import { useToastStore } from '../../stores/toastStore';

/**
 * Saves a new template from a session screen's editor. `onSaved` closes the
 * editor; the error is meant for inline display in it.
 */
export function useSaveTemplate(onSaved: () => void) {
  const queryClient = useQueryClient();
  const [error, setError] = useState<string | null>(null);
  const mutation = useMutation({
    mutationFn: ({ name, body }: { name: string; body: string }) => createTemplate(name, body),
    onSuccess: async (_data, variables) => {
      // Closed first, then announced: `.toast-viewport` paints below the top
      // layer, so a toast raised with the editor still open cannot be seen.
      // Both land in one commit, so there is no frame showing neither.
      //
      // Deliberately without a `mounted` guard: this reports durable account
      // state that stays true wherever the user went.
      onSaved();
      setError(null);
      useToastStore.getState().show(`Saved template "${variables.name}"`, 'success');
      await queryClient.invalidateQueries({ queryKey: queryKeys.templates() });
    },
    // Inline, never a toast: this renders while the editor is still up, which
    // is exactly where a toast would be invisible.
    onError: (failure: unknown) => {
      setError(
        isApiError(failure) ? `Failed to save template: ${failure.message}` : 'Failed to save template',
      );
    },
  });

  return {
    save: (name: string, body: string) => {
      mutation.mutate({ name, body });
    },
    busy: mutation.isPending,
    error,
    clearError: () => {
      setError(null);
    },
  };
}
