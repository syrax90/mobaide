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
 * A 404 means the scan is already gone, which satisfies the user's intent —
 * reconcile the list rather than report a failure they cannot act on. An
 * unreachable agent answers 503 from the relay, not 404, so this cannot swallow
 * a real connectivity failure.
 */
export function scanFailureHandler(
  verb: string,
  invalidate: () => Promise<void>,
): (error: unknown) => void {
  return (error) => {
    if (isApiError(error) && error.status === 404) {
      void invalidate();
      return;
    }
    useToastStore
      .getState()
      .show(
        isApiError(error) ? `Failed to ${verb} scan: ${error.message}` : `Failed to ${verb} scan`,
        'error',
      );
  };
}
