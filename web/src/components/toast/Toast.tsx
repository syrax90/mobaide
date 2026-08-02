import type { Toast as ToastModel } from '../../stores/toastStore';

interface ToastProps {
  toast: ToastModel;
  onDismiss: (id: string) => void;
}

export function Toast({ toast, onDismiss }: ToastProps) {
  return (
    <div className={`toast toast-${toast.kind}`}>
      <span className="toast-message">{toast.message}</span>
      <button
        type="button"
        className="toast-dismiss"
        aria-label="Dismiss notification"
        onClick={() => {
          onDismiss(toast.id);
        }}
      >
        ×
      </button>
    </div>
  );
}
