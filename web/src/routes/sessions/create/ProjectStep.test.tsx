import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import { ProjectStep } from './ProjectStep';
import type { WizardAction } from './wizardState';

const dispatched: WizardAction[] = [];
const forgotten: string[] = [];

/**
 * No stored projects: the recents block hangs off the custom-path group, and an
 * empty project list keeps the rest of the step out of the way.
 */
function renderStep(over: { recentPaths?: readonly string[]; customPathDraft?: string } = {}) {
  render(
    <ProjectStep
      projects={[]}
      selection={{ kind: 'none' }}
      customPathDraft={over.customPathDraft ?? ''}
      customPathError={null}
      recentPaths={over.recentPaths ?? []}
      onForgetRecent={(path) => forgotten.push(path)}
      dispatch={(action) => dispatched.push(action)}
      onContinue={() => undefined}
    />,
  );
}

const listedPaths = (): string[] =>
  [...document.querySelectorAll('.recent-path-pick')].map((node) => node.textContent ?? '');

beforeEach(() => {
  dispatched.length = 0;
  forgotten.length = 0;
});

afterEach(cleanup);

describe('ProjectStep recent paths', () => {
  it('shows nothing at all when this agent has no recents', () => {
    // No label, no placeholder: an agent that has never launched a one-off
    // directory should not pay for the block.
    renderStep({ recentPaths: [] });

    expect(screen.queryByRole('heading', { name: 'Recent paths' })).not.toBeInTheDocument();
    expect(listedPaths()).toEqual([]);
  });

  it('lists the stored directories most-recent-first, each carrying its full path', () => {
    // The rows ellipsise in CSS, so the tooltip is what discloses a long path
    // in full.
    renderStep({ recentPaths: ['/srv/one', '/srv/two'] });

    expect(screen.getByRole('heading', { name: 'Recent paths' })).toBeInTheDocument();
    expect(listedPaths()).toEqual(['/srv/one', '/srv/two']);
    expect(screen.getByRole('button', { name: '/srv/one' })).toHaveAttribute('title', '/srv/one');
  });

  it('fills the field through the same action a keystroke dispatches', () => {
    // Reusing `setCustomPathDraft` is what keeps validation, the resulting
    // selection and the Continue gate identical to typed text.
    renderStep({ recentPaths: ['/srv/one', '/srv/two'] });

    fireEvent.click(screen.getByRole('button', { name: '/srv/two' }));

    expect(dispatched).toEqual([{ type: 'setCustomPathDraft', draft: '/srv/two' }]);
    expect(forgotten).toEqual([]);
  });

  it('forgets the row whose × was pressed, and only that row', () => {
    renderStep({ recentPaths: ['/srv/one', '/srv/two'] });

    fireEvent.click(screen.getByRole('button', { name: 'Remove /srv/two' }));

    expect(forgotten).toEqual(['/srv/two']);
    expect(dispatched).toEqual([]);
  });

  it('keeps every recent up while the user types, rather than filtering', () => {
    // Both mobile clients show all five regardless of the draft; filtering here
    // would hide the row a user is part-way through retyping by hand.
    renderStep({ recentPaths: ['/srv/one', '/srv/two'], customPathDraft: '/srv/o' });

    expect(listedPaths()).toEqual(['/srv/one', '/srv/two']);
  });
});
