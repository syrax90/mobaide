import { create } from 'zustand';

export type ToastKind = 'info' | 'success' | 'error';

export interface Toast {
  id: string;
  kind: ToastKind;
  message: string;
}

const AUTO_DISMISS_MS = 5000;

interface ToastState {
  toasts: Toast[];
  show: (message: string, kind?: ToastKind, durationMs?: number) => string;
  dismiss: (id: string) => void;
}

export const useToastStore = create<ToastState>()((set, get) => ({
  toasts: [],
  show: (message, kind = 'info', durationMs = AUTO_DISMISS_MS) => {
    const id = crypto.randomUUID();
    set((state) => ({ toasts: [...state.toasts, { id, kind, message }] }));
    if (durationMs > 0) {
      setTimeout(() => {
        get().dismiss(id);
      }, durationMs);
    }
    return id;
  },
  dismiss: (id) => {
    set((state) => ({ toasts: state.toasts.filter((toast) => toast.id !== id) }));
  },
}));
