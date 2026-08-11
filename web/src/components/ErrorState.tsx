interface ErrorStateProps {
  message: string;
  onRetry?: () => void;
}

/** A load failure with the affordance the bare error text lacked: retry. */
export function ErrorState({ message, onRetry }: ErrorStateProps) {
  return (
    <div className="page-error" role="alert">
      <p>{message}</p>
      {onRetry !== undefined && (
        <button type="button" className="button" onClick={onRetry}>
          Retry
        </button>
      )}
    </div>
  );
}
