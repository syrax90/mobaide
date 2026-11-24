/**
 * The shared failure rule for the Auto Scan start/stop/run-now mutations.
 *
 * Both the list section and the detail screen drive the same three endpoints and
 * owe the user the same answer, so the rule lives here rather than being written
 * out beside each `useMutation`.
 */
import { isApiError } from '../../api/errors';
import { useToastStore } from '../../stores/toastStore';

/**
 * Build an `onError` handler for one verb.
 *
 * A 404 means the PC no longer runs the scan. The user is told so, the list is
 * reconciled, and `onGone` lets the caller show the scan as stopped. An
 * unreachable agent answers 503 from the relay, not 404, so this cannot hide
 * a real connectivity failure.
 */
export function scanFailureHandler(
  verb: string,
  invalidate: () => Promise<void>,
  onGone?: () => void,
): (error: unknown) => void {
  return (error) => {
    const toast = useToastStore.getState();
    if (isApiError(error) && error.status === 404) {
      onGone?.();
      void invalidate();
      toast.show(`Cannot ${verb} the scan: it is no longer running on the PC.`, 'error');
      return;
    }
    toast.show(
      isApiError(error) ? `Failed to ${verb} scan: ${error.message}` : `Failed to ${verb} scan`,
      'error',
    );
  };
}
