import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { useState } from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { useDragReorder, type ReorderSource } from './useDragReorder';

let moves: [number, number, ReorderSource][];

beforeEach(() => {
  moves = [];
});

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

const record = (from: number, to: number, source: ReorderSource) => {
  moves.push([from, to, source]);
};

function List({
  items,
  onMove = record,
  disabled,
}: {
  items: readonly string[];
  onMove?: (from: number, to: number, source: ReorderSource) => void;
  disabled?: boolean;
}) {
  const sortable = useDragReorder({ keys: items, onMove, disabled });
  return (
    <ul {...sortable.listProps}>
      {items.map((item, index) => (
        <li key={item} data-testid={`row-${item}`} {...sortable.itemProps(index)}>
          <button type="button" aria-label={`Reorder ${item}`} {...sortable.handleProps(index)} />
          {item}
        </li>
      ))}
    </ul>
  );
}

/** The caller's half of the contract: re-render the rows in their new order. */
function OwnedList({ initial }: { initial: readonly string[] }) {
  const [items, setItems] = useState(initial);
  return (
    <List
      items={items}
      onMove={(from, to, source) => {
        record(from, to, source);
        setItems((current) => {
          const next = [...current];
          const [moved] = next.splice(from, 1);
          if (moved !== undefined) next.splice(to, 0, moved);
          return next;
        });
      }}
    />
  );
}

const handle = (item: string): HTMLElement =>
  screen.getByRole('button', { name: `Reorder ${item}` });

const row = (item: string): HTMLElement => screen.getByTestId(`row-${item}`);

const rowOrder = (): string[] =>
  screen.getAllByRole('button', { name: /^Reorder / }).map((button) =>
    (button.getAttribute('aria-label') ?? '').slice('Reorder '.length),
  );

/**
 * jsdom builds no `PointerEvent`, and `fireEvent.pointerDown` then falls back
 * to a bare `Event`, which drops `clientY` — the one field the drag math reads
 * (the `TerminalScrollbar.test.tsx` helper).
 */
function pointer(type: string, clientY: number, pointerId = 1): Event {
  const event = new MouseEvent(type, { bubbles: true, cancelable: true, clientY, button: 0 });
  Object.defineProperty(event, 'pointerId', { value: pointerId });
  return event;
}

/** jsdom computes no layout: rows 40 px tall on a 50 px pitch, from the top. */
function stubRows(items: readonly string[]): void {
  items.forEach((item, index) => {
    const top = index * 50;
    vi.spyOn(row(item), 'getBoundingClientRect').mockReturnValue({
      top,
      bottom: top + 40,
      height: 40,
      left: 0,
      right: 300,
      width: 300,
      x: 0,
      y: top,
      toJSON: () => ({}),
    });
  });
}

describe('useDragReorder keyboard', () => {
  it('moves the focused row one place per arrow key', () => {
    render(<List items={['a', 'b', 'c']} />);

    fireEvent.keyDown(handle('b'), { key: 'ArrowDown' });
    fireEvent.keyDown(handle('b'), { key: 'ArrowUp' });

    expect(moves).toEqual([
      [1, 2, 'keyboard'],
      [1, 0, 'keyboard'],
    ]);
  });

  it('does nothing past either end of the list', () => {
    render(<List items={['a', 'b', 'c']} />);

    fireEvent.keyDown(handle('a'), { key: 'ArrowUp' });
    fireEvent.keyDown(handle('c'), { key: 'ArrowDown' });
    fireEvent.keyDown(handle('b'), { key: 'Enter' });

    expect(moves).toEqual([]);
  });

  it('keeps focus on the moved row’s handle after the list re-renders', () => {
    render(<OwnedList initial={['a', 'b', 'c']} />);
    handle('a').focus();

    fireEvent.keyDown(handle('a'), { key: 'ArrowDown' });
    expect(rowOrder()).toEqual(['b', 'a', 'c']);
    expect(handle('a')).toHaveFocus();

    fireEvent.keyDown(handle('a'), { key: 'ArrowDown' });
    expect(rowOrder()).toEqual(['b', 'c', 'a']);
    expect(handle('a')).toHaveFocus();
  });

  it('ignores arrow keys while disabled', () => {
    render(<List items={['a', 'b']} disabled />);

    fireEvent.keyDown(handle('a'), { key: 'ArrowDown' });

    expect(moves).toEqual([]);
    expect(handle('a')).toBeDisabled();
  });
});

describe('useDragReorder pointer', () => {
  it('translates the rows during a drag and reports one move on drop', async () => {
    render(<List items={['a', 'b', 'c']} />);
    stubRows(['a', 'b', 'c']);

    fireEvent(handle('a'), pointer('pointerdown', 20));
    fireEvent(handle('a'), pointer('pointermove', 80));
    fireEvent(handle('a'), pointer('pointermove', 130));

    // Past both siblings' centres: the dragged row follows the pointer and the
    // other two close its gap by one pitch each, without leaving their place
    // in the DOM.
    await waitFor(() => {
      expect(row('a').style.transform).toBe('translateY(110px)');
    });
    expect(row('b').style.transform).toBe('translateY(-50px)');
    expect(row('c').style.transform).toBe('translateY(-50px)');
    expect(rowOrder()).toEqual(['a', 'b', 'c']);
    expect(moves).toEqual([]);

    fireEvent(handle('a'), pointer('pointerup', 130));

    expect(moves).toEqual([[0, 2, 'pointer']]);
    expect(row('a').style.transform).toBe('');
  });

  it('reports nothing for a drop back on the starting place', () => {
    render(<List items={['a', 'b', 'c']} />);
    stubRows(['a', 'b', 'c']);

    fireEvent(handle('b'), pointer('pointerdown', 70));
    fireEvent(handle('b'), pointer('pointermove', 100));
    fireEvent(handle('b'), pointer('pointerup', 75));

    expect(moves).toEqual([]);
  });

  it('ignores another pointer while a drag runs', () => {
    render(<List items={['a', 'b', 'c']} />);
    stubRows(['a', 'b', 'c']);

    fireEvent(handle('a'), pointer('pointerdown', 20, 1));
    fireEvent(handle('a'), pointer('pointerup', 130, 2));
    expect(moves).toEqual([]);

    fireEvent(handle('a'), pointer('pointerup', 80, 1));
    expect(moves).toEqual([[0, 1, 'pointer']]);
  });

  it('cancels the drag on Escape', () => {
    render(<List items={['a', 'b', 'c']} />);
    stubRows(['a', 'b', 'c']);

    fireEvent(handle('a'), pointer('pointerdown', 20));
    fireEvent(handle('a'), pointer('pointermove', 130));
    fireEvent.keyDown(window, { key: 'Escape' });
    fireEvent(handle('a'), pointer('pointerup', 130));

    expect(moves).toEqual([]);
    expect(row('a').style.transform).toBe('');
  });

  it('cancels the drag when the rows change under it', () => {
    // A push from another device rewrote the list: the indices the drag holds
    // no longer name the same rows.
    const { rerender } = render(<List items={['a', 'b', 'c']} />);
    stubRows(['a', 'b', 'c']);
    fireEvent(handle('a'), pointer('pointerdown', 20));

    act(() => {
      rerender(<List items={['c', 'a', 'b']} />);
    });
    fireEvent(handle('a'), pointer('pointerup', 130));

    expect(moves).toEqual([]);
  });

  it('offers no drag for a single row', () => {
    render(<List items={['a']} />);
    stubRows(['a']);

    expect(handle('a')).toBeDisabled();
    fireEvent(handle('a'), pointer('pointerdown', 20));
    fireEvent(handle('a'), pointer('pointerup', 130));

    expect(moves).toEqual([]);
    expect(document.body).not.toHaveClass('sortable-dragging');
  });
});
