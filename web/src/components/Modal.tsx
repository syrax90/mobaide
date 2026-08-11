import { useEffect, useRef, type ReactNode } from 'react';

interface ModalProps {
  /** id of the element that names the dialog. */
  labelledBy: string;
  role?: 'dialog' | 'alertdialog';
  /** Pass false while a mutation is in flight to block Esc-dismissal. */
  dismissible?: boolean;
  onClose: () => void;
  className?: string;
  children: ReactNode;
}

/**
 * A native `<dialog>` opened with `showModal()`: the top layer provides the
 * focus trap, background inertness, Esc handling and focus restoration the old
 * inline `.confirm-panel` divs lacked — on a long list they rendered below the
 * fold with no backdrop. Deliberately no backdrop-click dismiss: Esc plus an
 * explicit Cancel button is the portal pattern, and a stray click must not
 * discard form input.
 */
export function Modal({
  labelledBy,
  role = 'dialog',
  dismissible = true,
  onClose,
  className,
  children,
}: ModalProps) {
  const dialogRef = useRef<HTMLDialogElement>(null);

  useEffect(() => {
    const dialog = dialogRef.current;
    if (dialog !== null && !dialog.open) dialog.showModal();
  }, []);

  return (
    <dialog
      ref={dialogRef}
      className={className === undefined ? 'dialog' : `dialog ${className}`}
      role={role}
      aria-labelledby={labelledBy}
      onCancel={(event) => {
        event.preventDefault();
        if (dismissible) onClose();
      }}
    >
      {children}
    </dialog>
  );
}
