import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import { Modal } from './Modal';

// The native `cancel` event (Esc) does not bubble, but React still runs every
// ancestor's `onCancel` for it, so a dialog opened from inside another one
// reaches both handlers from a single Esc.

let closed: string[];

function renderNested(): void {
  render(
    <Modal
      labelledBy="outer-title"
      onClose={() => {
        closed.push('outer');
      }}
    >
      <h2 id="outer-title">Outer</h2>
      <div>
        <Modal
          labelledBy="inner-title"
          onClose={() => {
            closed.push('inner');
          }}
        >
          <h2 id="inner-title">Inner</h2>
        </Modal>
      </div>
    </Modal>,
  );
}

const esc = (dialog: HTMLElement): void => {
  fireEvent(dialog, new Event('cancel', { cancelable: true }));
};

beforeEach(() => {
  closed = [];
});

afterEach(() => {
  cleanup();
});

describe('Modal nested in a Modal', () => {
  it('closes only the inner dialog on Esc there', () => {
    renderNested();

    esc(screen.getByRole('dialog', { name: 'Inner' }));

    expect(closed).toEqual(['inner']);
  });

  it('closes the outer dialog on Esc there', () => {
    renderNested();

    esc(screen.getByRole('dialog', { name: 'Outer' }));

    expect(closed).toEqual(['outer']);
  });
});
