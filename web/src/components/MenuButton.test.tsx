import { type RefObject } from 'react';
import { act, cleanup, createEvent, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { MenuButton, type MenuItem } from './MenuButton';

// The Popover API is the jsdom stand-in in `test/setup.ts`: `showPopover()`
// sets an inline display and fires `beforetoggle` synchronously, which is the
// path the component's `open` state hangs off. Roles, focus and the key walk
// are all the component's own, and are what these cases hold.

/** Built rather than fired, so the cancel flag can be read back off the press. */
const press = (target: HTMLElement, init: KeyboardEventInit): Event => {
  const event = createEvent.keyDown(target, init);
  fireEvent(target, event);
  return event;
};

/** jsdom defaults `isPrimary` to false, which is not a press the trigger acts on. */
const primaryPress = (target: HTMLElement): void => {
  fireEvent.pointerDown(target, { button: 0, isPrimary: true });
};

const onChanges = vi.fn<() => void>();
const onCommit = vi.fn<() => void>();

const item = (id: string, label: string, over: Partial<MenuItem> = {}): MenuItem => ({
  id,
  label,
  onSelect: () => undefined,
  ...over,
});

const gitItems = (): MenuItem[] => [
  item('changes', 'Changes…', { onSelect: onChanges }),
  item('commit', 'Commit…', { onSelect: onCommit }),
];

function renderMenu(items: MenuItem[] = gitItems(), triggerRef?: RefObject<HTMLButtonElement | null>) {
  return render(
    <MenuButton
      label="More options"
      groups={[{ id: 'git', label: 'Git', items }]}
      triggerRef={triggerRef}
    >
      ⋯
    </MenuButton>,
  );
}

const trigger = (): HTMLElement => screen.getByRole('button', { name: 'More options' });
const menuItem = (name: string): HTMLElement => screen.getByRole('menuitem', { name });

afterEach(() => {
  cleanup();
  onChanges.mockClear();
  onCommit.mockClear();
});

describe('MenuButton opening', () => {
  it('opens on click with the first item focused, and closes on the next click', () => {
    renderMenu();

    fireEvent.click(trigger());

    expect(trigger()).toHaveAttribute('aria-expanded', 'true');
    expect(screen.getByRole('menu', { name: 'More options' })).toBeInTheDocument();
    expect(screen.getByRole('group', { name: 'Git' })).toBeInTheDocument();
    expect(menuItem('Changes…')).toHaveFocus();

    fireEvent.click(trigger());

    expect(trigger()).toHaveAttribute('aria-expanded', 'false');
    expect(screen.queryByRole('menuitem')).toBeNull();
  });

  it('opens on ArrowDown at the first item and on ArrowUp at the last, consuming both', () => {
    renderMenu();

    const down = press(trigger(), { key: 'ArrowDown' });

    expect(down.defaultPrevented).toBe(true);
    expect(menuItem('Changes…')).toHaveFocus();

    press(menuItem('Changes…'), { key: 'Escape' });
    const up = press(trigger(), { key: 'ArrowUp' });

    expect(up.defaultPrevented).toBe(true);
    expect(menuItem('Commit…')).toHaveFocus();
  });

  it('mirrors a hide the browser performs itself, such as a light dismiss', () => {
    // `open` follows `beforetoggle`, not the click that asked for it: a click
    // outside, or a modal dialog opening over the menu, closes the popover
    // without going through the component.
    const { container } = renderMenu();
    fireEvent.click(trigger());
    const menu = container.querySelector<HTMLElement>('[role="menu"]');

    // Outside React's event system, as the browser's own dismiss would be.
    act(() => {
      menu?.hidePopover();
    });

    expect(trigger()).toHaveAttribute('aria-expanded', 'false');
    expect(screen.queryByRole('menuitem')).toBeNull();
  });

  it('stays closed when the press that light-dismissed it is followed by its click', () => {
    // A real browser dismisses an `auto` popover at pointerup, before the
    // click — the trigger is not the popover's invoker, so the press counts as
    // "outside". The click then finds the menu closed and would reopen it.
    const { container } = renderMenu();
    fireEvent.click(trigger());
    const menu = container.querySelector<HTMLElement>('[role="menu"]');

    primaryPress(trigger());
    act(() => {
      menu?.hidePopover();
    });
    fireEvent.click(trigger());

    expect(trigger()).toHaveAttribute('aria-expanded', 'false');
    expect(screen.queryByRole('menuitem')).toBeNull();
  });

  it('spends what the press saw on its click, so a key opens the menu afterwards', () => {
    // Enter and Space reach the trigger as a click with no pointerdown before
    // it. A latch left set by the dismissing press would make that click
    // "close" a menu that is not open.
    const { container } = renderMenu();
    fireEvent.click(trigger());
    const menu = container.querySelector<HTMLElement>('[role="menu"]');
    primaryPress(trigger());
    act(() => {
      menu?.hidePopover();
    });
    fireEvent.click(trigger());

    fireEvent.click(trigger());

    expect(trigger()).toHaveAttribute('aria-expanded', 'true');
    expect(menuItem('Changes…')).toHaveFocus();
  });

  it('ignores a press that is not the primary button, so the click after it opens', () => {
    // A right-click dismisses the popover as well, but the click that follows
    // it is a fresh activation: only the primary button's press arms the latch.
    const { container } = renderMenu();
    fireEvent.click(trigger());
    const menu = container.querySelector<HTMLElement>('[role="menu"]');
    fireEvent.pointerDown(trigger(), { button: 2, isPrimary: true });
    act(() => {
      menu?.hidePopover();
    });

    fireEvent.click(trigger());

    expect(trigger()).toHaveAttribute('aria-expanded', 'true');
    expect(menuItem('Changes…')).toHaveFocus();
  });

  it('lets a key open the menu after a press that never clicked', () => {
    // A press dragged off the trigger dismisses the menu but produces no
    // click, leaving the latch set; the next keyboard activation must not be
    // spent on it.
    const { container } = renderMenu();
    fireEvent.click(trigger());
    const menu = container.querySelector<HTMLElement>('[role="menu"]');
    primaryPress(trigger());
    act(() => {
      menu?.hidePopover();
    });

    press(trigger(), { key: 'Enter' });
    fireEvent.click(trigger());

    expect(trigger()).toHaveAttribute('aria-expanded', 'true');
    expect(menuItem('Changes…')).toHaveFocus();
  });

  it('drops the latch with a cancelled press', () => {
    // A touch the browser turns into a scroll is cancelled rather than
    // clicked; a click with no press of its own (assistive tech,
    // `element.click()`) must then find no stale latch.
    const { container } = renderMenu();
    fireEvent.click(trigger());
    const menu = container.querySelector<HTMLElement>('[role="menu"]');
    primaryPress(trigger());
    act(() => {
      menu?.hidePopover();
    });
    fireEvent.pointerCancel(trigger());

    fireEvent.click(trigger());

    expect(trigger()).toHaveAttribute('aria-expanded', 'true');
    expect(menuItem('Changes…')).toHaveFocus();
  });

  it('closes when the bar scrolls underneath it', () => {
    // The terminal bar scrolls horizontally without a light dismiss; a fixed
    // menu would drift away from its trigger.
    renderMenu();
    fireEvent.click(trigger());

    fireEvent.scroll(document.body);

    expect(trigger()).toHaveAttribute('aria-expanded', 'false');
  });

  it('stays open while the menu scrolls its own items', () => {
    // A menu taller than its max height (the chat settings menu) scrolls to
    // reach its lower choices; that must not dismiss it.
    renderMenu();
    fireEvent.click(trigger());

    fireEvent.scroll(screen.getByRole('menu'));
    fireEvent.scroll(menuItem('Changes…'));

    expect(trigger()).toHaveAttribute('aria-expanded', 'true');
  });
});

describe('MenuButton key walk', () => {
  it('wraps with the arrows and jumps with Home and End, consuming each key', () => {
    renderMenu();
    fireEvent.click(trigger());

    expect(press(menuItem('Changes…'), { key: 'ArrowDown' }).defaultPrevented).toBe(true);
    expect(menuItem('Commit…')).toHaveFocus();

    expect(press(menuItem('Commit…'), { key: 'ArrowDown' }).defaultPrevented).toBe(true);
    expect(menuItem('Changes…')).toHaveFocus();

    expect(press(menuItem('Changes…'), { key: 'ArrowUp' }).defaultPrevented).toBe(true);
    expect(menuItem('Commit…')).toHaveFocus();

    expect(press(menuItem('Commit…'), { key: 'Home' }).defaultPrevented).toBe(true);
    expect(menuItem('Changes…')).toHaveFocus();

    expect(press(menuItem('Changes…'), { key: 'End' }).defaultPrevented).toBe(true);
    expect(menuItem('Commit…')).toHaveFocus();
  });

  it('walks past a disabled item', () => {
    renderMenu([
      item('changes', 'Changes…'),
      item('commit', 'Commit…', { disabled: true }),
      item('branches', 'Branches…'),
    ]);
    fireEvent.click(trigger());

    press(menuItem('Changes…'), { key: 'ArrowDown' });

    expect(menuItem('Branches…')).toHaveFocus();
    expect(menuItem('Commit…')).toBeDisabled();

    press(menuItem('Branches…'), { key: 'ArrowDown' });

    expect(menuItem('Changes…')).toHaveFocus();
  });

  it('closes on Escape and hands focus back to the trigger', () => {
    renderMenu();
    fireEvent.click(trigger());

    const escape = press(menuItem('Changes…'), { key: 'Escape' });

    expect(escape.defaultPrevented).toBe(true);
    expect(trigger()).toHaveAttribute('aria-expanded', 'false');
    expect(screen.queryByRole('menuitem')).toBeNull();
    expect(trigger()).toHaveFocus();
  });

  it('closes on Tab without swallowing it, so focus moves on', () => {
    renderMenu();
    fireEvent.click(trigger());

    const tab = press(menuItem('Changes…'), { key: 'Tab' });

    expect(tab.defaultPrevented).toBe(false);
    expect(trigger()).toHaveAttribute('aria-expanded', 'false');
  });
});

describe('MenuButton selection', () => {
  it('closes and refocuses the trigger before the item runs, then runs it once', () => {
    // Whatever the item opens may take focus from the trigger; the native
    // hide would otherwise hand focus back to the trigger a moment later and
    // pull it out of the thing just opened.
    let focusedAtSelect: Element | null = null;
    renderMenu([
      item('changes', 'Changes…', {
        onSelect: () => {
          focusedAtSelect = document.activeElement;
          onChanges();
        },
      }),
    ]);
    fireEvent.click(trigger());

    fireEvent.click(menuItem('Changes…'));

    expect(onChanges).toHaveBeenCalledTimes(1);
    expect(focusedAtSelect).toBe(trigger());
    expect(trigger()).toHaveAttribute('aria-expanded', 'false');
    expect(screen.queryByRole('menuitem')).toBeNull();
    expect(trigger()).toHaveFocus();
  });
});
