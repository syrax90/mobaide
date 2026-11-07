import {
  useEffect,
  useId,
  useRef,
  useState,
  type CSSProperties,
  type KeyboardEvent,
  type ReactNode,
  type RefObject,
  type ToggleEvent,
} from 'react';
import { isPrimaryPress } from '../lib/pointer';

export interface MenuItem {
  id: string;
  label: string;
  onSelect: () => void;
  disabled?: boolean;
  /** A second, muted line under the label. */
  description?: string;
  /** Set to make the item a `menuitemradio` (or `menuitemcheckbox` with `toggle`). */
  checked?: boolean;
  toggle?: boolean;
}

export interface MenuGroup {
  id: string;
  /** Rendered as the group's heading and used as its accessible name. */
  label: string;
  items: MenuItem[];
}

interface MenuButtonProps {
  /** The trigger's accessible name. */
  label: string;
  groups: MenuGroup[];
  /** Exposed so whatever an item opens can hand focus back when it closes. */
  triggerRef?: RefObject<HTMLButtonElement | null>;
  className?: string;
  /** Extra class on the menu itself. */
  menuClassName?: string;
  /** Open under the trigger's right edge (default) or above its left edge. */
  placement?: 'below' | 'above';
  /** The trigger's visible content. */
  children: ReactNode;
}

const ITEM_SELECTOR = '[role^="menuitem"]:not(:disabled)';

/**
 * The APG menu button, on the Popover API and nothing else.
 *
 * `popover="auto"` puts the menu on the top layer, which is what lets it sit
 * above xterm's canvases and outside the terminal bar's horizontal scroll clip
 * without a portal, and gives light dismiss for free. Browsers stop there: the
 * roles, focus-on-open, the arrow-key walk and Escape are this component's.
 *
 * Opened imperatively rather than through `popovertarget`, so the jsdom
 * stand-in in `test/setup.ts` sees every transition. `open` mirrors the
 * element's own state through `beforetoggle` — synchronous inside
 * `showPopover()`, unlike `toggle`, which is queued a task later and would
 * leave the first frame of an open menu empty — so a light dismiss, or a modal
 * dialog opening over it, cannot desynchronise the trigger's `aria-expanded`.
 */
export function MenuButton({
  label,
  groups,
  triggerRef,
  className,
  menuClassName,
  placement = 'below',
  children,
}: MenuButtonProps) {
  const menuId = useId();
  const fallbackRef = useRef<HTMLButtonElement>(null);
  const trigger = triggerRef ?? fallbackRef;
  const menuRef = useRef<HTMLDivElement>(null);
  const [open, setOpen] = useState(false);
  const [position, setPosition] = useState<CSSProperties>({});
  const pendingFocus = useRef<'first' | 'last' | null>(null);
  // A pointer press on the trigger while the menu is open light-dismisses it
  // at pointerup, before the click arrives — the trigger is not the popover's
  // invoker, so the browser treats it as "outside". A click alone would then
  // find the menu closed and reopen it. What the press saw decides instead.
  const openAtPointerDown = useRef(false);

  const items = (): HTMLButtonElement[] =>
    Array.from(menuRef.current?.querySelectorAll<HTMLButtonElement>(ITEM_SELECTOR) ?? []);

  const show = (focus: 'first' | 'last'): void => {
    const rect = trigger.current?.getBoundingClientRect();
    if (rect !== undefined) {
      // Anchored under the trigger's right edge. `getBoundingClientRect`
      // rather than CSS anchor positioning, which is not yet in every engine.
      setPosition(
        placement === 'above'
          ? { bottom: window.innerHeight - rect.top + 4, left: Math.max(0, rect.left) }
          : { top: rect.bottom + 4, right: Math.max(0, window.innerWidth - rect.right) },
      );
    }
    pendingFocus.current = focus;
    menuRef.current?.showPopover();
  };

  const hide = (): void => {
    menuRef.current?.hidePopover();
  };

  const closeToTrigger = (): void => {
    hide();
    trigger.current?.focus();
  };

  useEffect(() => {
    if (!open) return;
    const all = items();
    (pendingFocus.current === 'last' ? all[all.length - 1] : all[0])?.focus();
    pendingFocus.current = null;
  }, [open]);

  // The terminal bar scrolls horizontally without a light dismiss, which would
  // leave a fixed-position menu drifting away from its trigger.
  useEffect(() => {
    if (!open) return;
    const onScroll = (): void => {
      menuRef.current?.hidePopover();
    };
    document.addEventListener('scroll', onScroll, { capture: true, passive: true });
    return () => {
      document.removeEventListener('scroll', onScroll, { capture: true });
    };
  }, [open]);

  const onTriggerKeyDown = (event: KeyboardEvent<HTMLButtonElement>): void => {
    // A keyboard activation never had a press of its own; a latch left by a
    // press that never clicked (dragged off, cancelled) must not eat it.
    openAtPointerDown.current = false;
    if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
      event.preventDefault();
      show(event.key === 'ArrowDown' ? 'first' : 'last');
    }
  };

  const onMenuKeyDown = (event: KeyboardEvent<HTMLDivElement>): void => {
    const all = items();
    const index = all.findIndex((item) => item === document.activeElement);
    const focusAt = (next: number): void => {
      all[(next + all.length) % all.length]?.focus();
    };
    switch (event.key) {
      case 'ArrowDown':
        event.preventDefault();
        focusAt(index + 1);
        return;
      case 'ArrowUp':
        event.preventDefault();
        focusAt(index === -1 ? all.length - 1 : index - 1);
        return;
      case 'Home':
        event.preventDefault();
        focusAt(0);
        return;
      case 'End':
        event.preventDefault();
        focusAt(all.length - 1);
        return;
      case 'Escape':
        event.preventDefault();
        closeToTrigger();
        return;
      case 'Tab':
        // Tab leaves the menu; closing keeps the top layer from holding a
        // menu nobody is in.
        hide();
        return;
      default:
        return;
    }
  };

  return (
    <>
      <button
        ref={trigger}
        type="button"
        className={className}
        aria-haspopup="menu"
        aria-expanded={open}
        aria-controls={menuId}
        aria-label={label}
        title={label}
        onPointerDown={(event) => {
          if (isPrimaryPress(event)) openAtPointerDown.current = open;
        }}
        onPointerCancel={() => {
          openAtPointerDown.current = false;
        }}
        onClick={() => {
          const wasOpen = openAtPointerDown.current || open;
          openAtPointerDown.current = false;
          if (wasOpen) hide();
          else show('first');
        }}
        onKeyDown={onTriggerKeyDown}
      >
        {children}
      </button>
      <div
        ref={menuRef}
        id={menuId}
        popover="auto"
        role="menu"
        aria-label={label}
        className={menuClassName === undefined ? 'menu' : `menu ${menuClassName}`}
        style={position}
        onBeforeToggle={(event: ToggleEvent<HTMLDivElement>) => {
          setOpen(event.newState === 'open');
        }}
        onKeyDown={onMenuKeyDown}
      >
        {open &&
          groups.map((group, groupIndex) => (
            <MenuGroupView
              key={group.id}
              group={group}
              separated={groupIndex > 0}
              onPick={(item) => {
                // Close first: the native hide hands focus back to the trigger
                // while it still holds it, and whatever the item opens may
                // then take focus from there.
                closeToTrigger();
                item.onSelect();
              }}
            />
          ))}
      </div>
    </>
  );
}

interface MenuGroupViewProps {
  group: MenuGroup;
  separated: boolean;
  onPick: (item: MenuItem) => void;
}

function MenuGroupView({ group, separated, onPick }: MenuGroupViewProps) {
  const labelId = useId();
  return (
    <>
      {separated && <div role="separator" className="menu-separator" />}
      <div role="group" aria-labelledby={labelId}>
        <div id={labelId} className="menu-group-label">
          {group.label}
        </div>
        {group.items.map((item) => (
          <button
            key={item.id}
            type="button"
            role={
              item.checked === undefined
                ? 'menuitem'
                : item.toggle === true
                  ? 'menuitemcheckbox'
                  : 'menuitemradio'
            }
            aria-checked={item.checked}
            className={item.checked === undefined ? 'menu-item' : 'menu-item menu-item-checkable'}
            tabIndex={-1}
            disabled={item.disabled === true}
            onClick={() => {
              onPick(item);
            }}
          >
            {item.checked !== undefined && (
              <span className="menu-item-check" aria-hidden="true">
                {item.checked ? '✓' : ''}
              </span>
            )}
            {item.description === undefined ? (
              item.label
            ) : (
              <span className="menu-item-text">
                <span>{item.label}</span>
                <span className="menu-item-description">{item.description}</span>
              </span>
            )}
          </button>
        ))}
      </div>
    </>
  );
}
