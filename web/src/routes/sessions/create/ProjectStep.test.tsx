import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import type { ProjectSummary } from '../../../api/projectRepository';
import { queryKeys } from '../../../lib/queryKeys';
import { projectInfoSchema } from '../../../protocol/dto/projects';
import { ProjectStep } from './ProjectStep';
import type { ProjectSelection, WizardAction } from './wizardState';

const dispatched: WizardAction[] = [];
const forgotten: string[] = [];
const selected: string[] = [];
const picked: string[] = [];
let continued = 0;
let createOpened = 0;

const project = (name: string): ProjectSummary =>
  projectInfoSchema.parse({ name, path: `/srv/${name}` });

interface Overrides {
  projects?: readonly ProjectSummary[];
  selection?: ProjectSelection;
  recentPaths?: readonly string[];
  customPathDraft?: string;
  customPathError?: string | null;
}

/**
 * Defaults to no stored projects: the recents block hangs off the custom-path
 * group, and an empty project list keeps the rest of the step out of the way.
 */
function renderStep(over: Overrides = {}) {
  // The custom-path field's folder suggestions are off for this agent, so the
  // step renders without ever reaching the network.
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  client.setQueryData(queryKeys.agentFsDirs('agent-1'), { dirs: false, windowsPaths: false });
  render(
    <QueryClientProvider client={client}>
      <ProjectStep
        agentId="agent-1"
        projects={over.projects ?? []}
        selection={over.selection ?? { kind: 'none' }}
        customPathDraft={over.customPathDraft ?? ''}
        customPathError={over.customPathError ?? null}
        recentPaths={over.recentPaths ?? []}
        onForgetRecent={(path) => forgotten.push(path)}
        onSelectProject={(project) => selected.push(project.name)}
        onSelectRecentPath={(path) => picked.push(path)}
        onCreateProject={() => {
          createOpened += 1;
        }}
        dispatch={(action) => dispatched.push(action)}
        onContinue={() => {
          continued += 1;
        }}
      />
    </QueryClientProvider>,
  );
}

const listedPaths = (): string[] =>
  [...document.querySelectorAll('.recent-path-pick')].map((node) => node.textContent ?? '');

const continueButton = (): HTMLElement => screen.getByRole('button', { name: 'Continue' });

beforeEach(() => {
  dispatched.length = 0;
  forgotten.length = 0;
  selected.length = 0;
  picked.length = 0;
  continued = 0;
  createOpened = 0;
});

afterEach(cleanup);

describe('ProjectStep project rows', () => {
  it('commits on the row itself, with no confirm click', () => {
    // The whole point of the step: picking is one act, as it already is on the
    // mode step. The container is what navigates.
    renderStep({ projects: [project('alpha'), project('beta')] });

    fireEvent.click(screen.getByRole('button', { name: /beta/ }));

    expect(selected).toEqual(['beta']);
    expect(continued).toBe(0);
  });

  it('highlights the selection the step was arrived at with', () => {
    // Only ever seen on a step that was navigated *to* — the last-launch
    // pre-fill, the wizard's own Back, a deep link.
    renderStep({
      projects: [project('alpha'), project('beta')],
      selection: { kind: 'stored', name: 'beta' },
    });

    expect(screen.getByRole('button', { name: /beta/ })).toHaveAttribute('aria-pressed', 'true');
    expect(screen.getByRole('button', { name: /alpha/ })).toHaveAttribute('aria-pressed', 'false');
  });

  it('drops the highlight while a one-off path is being typed', () => {
    // A typed path is what Continue would send, so the stale row must not read
    // as the pending choice.
    renderStep({
      projects: [project('beta')],
      selection: { kind: 'stored', name: 'beta' },
      customPathDraft: '/srv/typed',
    });

    expect(screen.getByRole('button', { name: /beta/ })).toHaveAttribute('aria-pressed', 'false');
  });
});

describe('ProjectStep create row', () => {
  const EMPTY_STATE = 'This agent has no projects yet. Create one, or enter a path below.';

  const listedRows = (): string[] =>
    [...document.querySelectorAll('.project-list .project-name')].map(
      (node) => node.textContent ?? '',
    );

  it('is offered on an agent with no projects at all', () => {
    // The agent that most needs it: both mobile pickers show the row here too.
    renderStep({ projects: [] });

    expect(screen.getByText(EMPTY_STATE)).toBeInTheDocument();
    expect(listedRows()).toEqual(['Create new project…']);
  });

  it('comes last, after the stored projects, and is not a selectable row', () => {
    renderStep({
      projects: [project('alpha'), project('beta')],
      selection: { kind: 'stored', name: 'beta' },
    });

    expect(screen.queryByText(EMPTY_STATE)).not.toBeInTheDocument();
    expect(listedRows()).toEqual(['alpha', 'beta', 'Create new project…']);
    expect(screen.getByRole('button', { name: /Create new project…/ })).not.toHaveAttribute(
      'aria-pressed',
    );
  });

  it('hands the click to the container without picking or advancing', () => {
    renderStep({ projects: [project('alpha')] });

    fireEvent.click(screen.getByRole('button', { name: /Create new project…/ }));

    expect(createOpened).toBe(1);
    expect(selected).toEqual([]);
    expect(dispatched).toEqual([]);
    expect(continued).toBe(0);
  });
});

describe('ProjectStep continue gate', () => {
  it('stays disabled on a step nobody has typed into', () => {
    // `customPathError` is null on an untouched field, so the emptiness test is
    // the whole of what keeps a fresh step from reading as valid.
    renderStep();

    expect(continueButton()).toBeDisabled();
  });

  it('stays disabled for a highlighted project row', () => {
    // The behaviour change itself: a stored project no longer waits on the
    // button, so the button must not offer to re-send one.
    renderStep({
      projects: [project('beta')],
      selection: { kind: 'stored', name: 'beta' },
    });

    expect(continueButton()).toBeDisabled();
  });

  it('stays disabled while the typed path is not absolute', () => {
    renderStep({ customPathDraft: 'srv/two', customPathError: 'Path must be absolute' });

    expect(continueButton()).toBeDisabled();
  });

  it('enables only for a valid one-off directory, and commits it once', () => {
    renderStep({ customPathDraft: '  /srv/two  ' });

    const button = continueButton();
    expect(button).toBeEnabled();

    fireEvent.click(button);

    expect(continued).toBe(1);
    expect(selected).toEqual([]);
  });
});

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

  it('hands a picked recent to the container rather than dispatching a keystroke', () => {
    // Picking one is a complete act, like a project row: the container fills
    // the field, re-validates and advances. The step no longer arbitrates.
    renderStep({ recentPaths: ['/srv/one', '/srv/two'] });

    fireEvent.click(screen.getByRole('button', { name: '/srv/two' }));

    expect(picked).toEqual(['/srv/two']);
    expect(dispatched).toEqual([]);
    expect(continued).toBe(0);
  });

  it('forgets the row whose × was pressed, and only that row', () => {
    // The sibling button navigates now, so "× must not advance" is a real
    // regression risk rather than a formality.
    renderStep({ recentPaths: ['/srv/one', '/srv/two'] });

    fireEvent.click(screen.getByRole('button', { name: 'Remove /srv/two' }));

    expect(forgotten).toEqual(['/srv/two']);
    expect(picked).toEqual([]);
    expect(dispatched).toEqual([]);
  });

  it('keeps every recent up while the user types, rather than filtering', () => {
    // Both mobile clients show all five regardless of the draft; filtering here
    // would hide the row a user is part-way through retyping by hand.
    renderStep({ recentPaths: ['/srv/one', '/srv/two'], customPathDraft: '/srv/o' });

    expect(listedPaths()).toEqual(['/srv/one', '/srv/two']);
  });
});
