import type { ReactElement } from 'react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import {
  act,
  cleanup,
  createEvent,
  fireEvent,
  render,
  screen,
  type RenderResult,
} from '@testing-library/react';
import { MemoryRouter } from 'react-router';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { PromptTemplate } from '../../api/templateRepository';
import { useToastStore } from '../../stores/toastStore';

// Same seam as the sibling suite: the collaborator is the repository, not the
// wire, and `groupTemplates` stays real because it decides the on-screen order
// the arrow keys and Enter walk.
const repo = vi.hoisted(() => ({
  templates: [] as PromptTemplate[],
  /** Fails the next list fetch; cleared so a Retry can succeed. */
  listFails: false,
  favoriteFails: false,
}));

vi.mock('../../api/templateRepository', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../api/templateRepository')>();
  return {
    ...actual,
    listTemplates: () =>
      repo.listFails
        ? Promise.reject(new Error('relay refused'))
        : Promise.resolve(repo.templates),
    touchTemplate: () => Promise.resolve(),
    setTemplateFavorite: () =>
      repo.favoriteFails
        ? Promise.reject(new Error('PATCH refused'))
        : Promise.resolve(),
  };
});

const { TemplatesPicker } = await import('./TemplatesPicker');

const template = (name: string): PromptTemplate => ({
  templateId: `id-${name}`,
  name,
  body: `body of ${name}`,
  isFavorite: false,
  lastUsedAt: null,
});

/** The name and body spans sit flush, so the accessible name concatenates. */
const pickRow = (name: string): HTMLElement =>
  screen.getByRole('button', { name: `${name}body of ${name}` });

const star = (name: string): HTMLElement =>
  screen.getByRole('button', { name: `Add ${name} to favourites` });

const search = (): HTMLElement => screen.getByLabelText('Search templates');

/** Built rather than fired, so the cancel flag can be read back off the press. */
const press = (target: HTMLElement, init: KeyboardEventInit): Event => {
  const event = createEvent.keyDown(target, init);
  fireEvent(target, event);
  return event;
};

/** React Query notifies on a timer, so nothing it does lands in this task. */
const flush = async (): Promise<void> => {
  await act(async () => {
    await new Promise<void>((resolve) => setTimeout(resolve, 0));
  });
};

const onPick = vi.fn<(template: PromptTemplate) => boolean>(() => true);
const onClose = vi.fn<() => void>();

let client: QueryClient;

/** Kept as an element so a test can re-render the same picker with a new gate. */
const picker = (canSend: boolean): ReactElement => (
  <QueryClientProvider client={client}>
    <MemoryRouter>
      <TemplatesPicker canSend={canSend} onPick={onPick} onClose={onClose} />
    </MemoryRouter>
  </QueryClientProvider>
);

function renderPicker(canSend = true): RenderResult {
  return render(picker(canSend));
}

/** Rendered plus listed: the sections only exist once the query has landed. */
async function renderListed(...templates: PromptTemplate[]): Promise<RenderResult> {
  repo.templates = templates;
  const view = renderPicker();
  await screen.findByRole('heading', { name: 'All' });
  return view;
}

beforeEach(() => {
  client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  repo.templates = [];
  repo.listFails = false;
  repo.favoriteFails = false;
  onPick.mockClear();
  onPick.mockImplementation(() => true);
  onClose.mockClear();
});

afterEach(() => {
  cleanup();
  client.clear();
  useToastStore.setState({ toasts: [] });
});

describe('TemplatesPicker dialog shell', () => {
  it('opens with the search field under the cursor', async () => {
    // The picker's own reason to exist as a `<dialog>`: it is typed into
    // immediately. `showModal()` would otherwise land on the first focusable
    // descendant, which is Close.
    await renderListed(template('alpha'));

    expect(search()).toHaveFocus();
  });

  it('dismisses on Esc', async () => {
    // Esc reaches a native dialog as `cancel`; the jsdom stand-in implements no
    // key handling, so the event stands in for the press.
    await renderListed(template('alpha'));

    fireEvent(
      screen.getByRole('dialog'),
      new Event('cancel', { bubbles: false, cancelable: true }),
    );

    expect(onClose).toHaveBeenCalled();
  });

  it('walks the rows with the arrow keys and hands the field back at the top', async () => {
    // The walk reads the rows out of the DOM, so it crosses sections in the
    // order they are on screen; up off the first row returns to the field
    // rather than wrapping to the bottom.
    await renderListed(template('alpha'), template('beta'));

    fireEvent.keyDown(search(), { key: 'ArrowDown' });
    expect(pickRow('alpha')).toHaveFocus();

    fireEvent.keyDown(pickRow('alpha'), { key: 'ArrowDown' });
    expect(pickRow('beta')).toHaveFocus();

    fireEvent.keyDown(pickRow('beta'), { key: 'ArrowUp' });
    expect(pickRow('alpha')).toHaveFocus();

    fireEvent.keyDown(pickRow('alpha'), { key: 'ArrowUp' });
    expect(search()).toHaveFocus();
  });

  it('picks the first row still visible when Enter is pressed in the field', async () => {
    // "First" is the first *match*, not the first template: typing a name and
    // pressing Enter is the field's whole purpose.
    await renderListed(template('alpha'), template('beta'));

    fireEvent.change(search(), { target: { value: 'beta' } });
    fireEvent.keyDown(search(), { key: 'Enter' });
    await flush();

    expect(onPick).toHaveBeenCalledWith(expect.objectContaining({ templateId: 'id-beta' }));
  });

  it('moves into the list instead of picking when Enter arrives on an empty field', async () => {
    // Nothing was narrowed, so the "first row" is whichever template was used
    // last — and an Enter meant for the button that opened this dialog, or a
    // reflex to dismiss it, would paste that body into a live PTY. Focus lands
    // on the row it would have fired, so the user sees it before it goes.
    await renderListed(template('alpha'), template('beta'));

    fireEvent.keyDown(search(), { key: 'Enter' });
    await flush();

    expect(onPick).not.toHaveBeenCalled();
    expect(pickRow('alpha')).toHaveFocus();
  });

  it('counts a whitespace-only filter as empty', async () => {
    // The needle is trimmed before it is matched, so a field holding only
    // spaces still lists everything: pressing Enter there is the bare-Enter
    // case, not a narrowed one.
    await renderListed(template('alpha'), template('beta'));

    fireEvent.change(search(), { target: { value: '   ' } });
    fireEvent.keyDown(search(), { key: 'Enter' });
    await flush();

    expect(onPick).not.toHaveBeenCalled();
    expect(pickRow('alpha')).toHaveFocus();
  });

  it('leaves Enter to the IME while a composition is open', async () => {
    // Mid-composition Enter commits the candidate the IME is offering. Picking
    // on the same press would both steal the commit and insert a body chosen
    // from a filter the user has not finished typing.
    await renderListed(template('alpha'), template('beta'));
    fireEvent.change(search(), { target: { value: 'beta' } });

    fireEvent.keyDown(search(), { key: 'Enter', isComposing: true });
    fireEvent.keyDown(search(), { key: 'Enter', keyCode: 229 });
    await flush();

    expect(onPick).not.toHaveBeenCalled();

    // The same press once the composition has ended. The filter and the row it
    // matches are live throughout, so the guard is the only thing that held the
    // two presses above.
    fireEvent.keyDown(search(), { key: 'Enter' });
    await flush();

    expect(onPick).toHaveBeenCalledWith(expect.objectContaining({ templateId: 'id-beta' }));
  });

  it('leaves the arrow keys to the IME candidate window', async () => {
    // The arrows walk candidates while a composition is open; moving focus out
    // of the field would abandon the text being composed.
    await renderListed(template('alpha'), template('beta'));

    fireEvent.keyDown(search(), { key: 'ArrowDown', isComposing: true });
    fireEvent.keyDown(search(), { key: 'ArrowDown', keyCode: 229 });

    expect(search()).toHaveFocus();

    fireEvent.keyDown(search(), { key: 'ArrowDown' });

    expect(pickRow('alpha')).toHaveFocus();
  });

  it('steps one row at a time when the arrow comes from a row’s star', async () => {
    // The star is a sibling of the pick button inside the same row and is
    // reachable by Tab, so matching focus against the pick buttons alone read
    // as "outside the list" and teleported to whichever end the key pointed at.
    await renderListed(template('alpha'), template('beta'), template('gamma'));

    star('beta').focus();
    fireEvent.keyDown(star('beta'), { key: 'ArrowDown' });

    expect(pickRow('gamma')).toHaveFocus();

    star('beta').focus();
    fireEvent.keyDown(star('beta'), { key: 'ArrowUp' });

    expect(pickRow('alpha')).toHaveFocus();
  });

  it('swallows a repeated Enter arriving on the search field', async () => {
    // A held Enter repeats at the OS rate, so the press that activated the
    // Templates button is delivered again — to the field this dialog opens
    // focused on. Only the first press of a hold is an action.
    await renderListed(template('alpha'), template('beta'));

    const held = press(search(), { key: 'Enter', repeat: true });
    await flush();

    expect(held.defaultPrevented).toBe(true);
    expect(onPick).not.toHaveBeenCalled();
    expect(search()).toHaveFocus();

    // The same key without the repeat flag, which the guard has to let past:
    // on an empty field bare Enter steps into the list.
    press(search(), { key: 'Enter' });
    await flush();

    expect(pickRow('alpha')).toHaveFocus();
  });

  it('cancels a repeated Enter on a row before the browser activates it', async () => {
    // The other half of one physical hold: focus has landed in the list, and a
    // `<button>` activates itself on Enter, which would paste a body into a
    // live PTY off a press the user never made. Cancelling the keydown is what
    // suppresses that activation — jsdom runs no such default, so the cancel
    // flag rather than the absent `onPick` is what carries this test.
    await renderListed(template('alpha'), template('beta'));
    pickRow('alpha').focus();

    const held = press(pickRow('alpha'), { key: 'Enter', repeat: true });
    await flush();

    expect(held.defaultPrevented).toBe(true);
    expect(onPick).not.toHaveBeenCalled();

    // A first press on a row stays with the browser: that activation is how
    // Enter picks out of the list at all.
    expect(press(pickRow('alpha'), { key: 'Enter' }).defaultPrevented).toBe(false);
  });

  it('keeps navigating while an arrow key is held', async () => {
    // Held arrows are navigation, not a repeated action, so the repeat guard is
    // scoped to Enter and must not swallow these.
    await renderListed(template('alpha'), template('beta'));

    fireEvent.keyDown(search(), { key: 'ArrowDown', repeat: true });
    expect(pickRow('alpha')).toHaveFocus();

    fireEvent.keyDown(pickRow('alpha'), { key: 'ArrowDown', repeat: true });
    expect(pickRow('beta')).toHaveFocus();
  });

  it('closes on the first Esc even with a filter typed', async () => {
    // Chrome and Safari let a non-empty `input[type=search]` eat this keydown
    // to clear itself before the dialog ever sees a close request, and the
    // caret starts in exactly that field — so the press has to be handled here
    // or dismissing the dialog costs two of them.
    await renderListed(template('alpha'));
    fireEvent.change(search(), { target: { value: 'alpha' } });

    const escape = press(search(), { key: 'Escape' });

    expect(escape.defaultPrevented).toBe(true);
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it('leaves Esc to the IME candidate window', async () => {
    // Mid-composition Esc dismisses the candidates, not the dialog: closing on
    // it would throw away the text being composed. The composition guard runs
    // ahead of the close for that reason.
    await renderListed(template('alpha'));

    fireEvent.keyDown(search(), { key: 'Escape', isComposing: true });
    fireEvent.keyDown(search(), { key: 'Escape', keyCode: 229 });

    expect(onClose).not.toHaveBeenCalled();

    fireEvent.keyDown(search(), { key: 'Escape' });

    expect(onClose).toHaveBeenCalledTimes(1);
  });
});

describe('TemplatesPicker states', () => {
  it('names the trimmed needle when the filter matches nothing', async () => {
    await renderListed(template('alpha'));

    fireEvent.change(search(), { target: { value: '  zzz  ' } });

    expect(screen.getByText(/No templates match/)).toHaveTextContent(
      'No templates match “zzz”.',
    );
    expect(screen.queryByRole('heading', { name: 'All' })).toBeNull();
  });

  it('sends an empty account to the management screen instead of the filter', async () => {
    // The other half of the same split: nothing to filter is fixed on
    // /templates, not by editing the field.
    renderPicker();

    expect(await screen.findByText(/No templates yet/)).toBeInTheDocument();
    expect(screen.getByRole('link', { name: 'Manage templates' })).toHaveAttribute(
      'href',
      '/templates',
    );
  });

  it('offers a Retry that re-runs the failed list', async () => {
    repo.listFails = true;
    renderPicker();
    await screen.findByText(/Failed to load templates/);

    repo.listFails = false;
    repo.templates = [template('alpha')];
    fireEvent.click(screen.getByRole('button', { name: 'Retry' }));

    expect(await screen.findByRole('heading', { name: 'All' })).toBeInTheDocument();
    expect(pickRow('alpha')).toBeInTheDocument();
  });

  it('reports a failed favourite inside the dialog rather than as a toast', async () => {
    // A toast raised over an open dialog paints under the top layer, so the
    // user would be told nothing at all.
    repo.favoriteFails = true;
    await renderListed(template('alpha'));

    fireEvent.click(star('alpha'));

    expect(await screen.findByRole('status')).toHaveTextContent(
      'Failed to update the template.',
    );
    expect(useToastStore.getState().toasts).toEqual([]);
  });

  it('disables the rows and says why while the session refuses input', async () => {
    // The star stays live: favouriting is a REST call that owes nothing to the
    // PTY, and disabling it would misdescribe why the row is dead.
    repo.templates = [template('alpha')];
    renderPicker(false);
    await screen.findByRole('heading', { name: 'All' });

    expect(pickRow('alpha')).toBeDisabled();
    expect(screen.getByText(/not accepting input/)).toBeInTheDocument();
    expect(star('alpha')).toBeEnabled();
  });

  it('explains inline when the gate shuts between the render and the click', async () => {
    // The rows were enabled when they were drawn; the refusal arrives from the
    // pick itself, and the dialog is the only place the user can be told.
    onPick.mockImplementation(() => false);
    await renderListed(template('alpha'));

    fireEvent.click(pickRow('alpha'));

    expect(await screen.findByRole('status')).toHaveTextContent(
      'The template was not inserted.',
    );
  });

  it('retires the refusal once the gate that caused it reopens', async () => {
    // The dialog is the only channel for this message now that the toast path
    // is gone, so a stale one is all the user has to go on. It must outlive the
    // shut gate and no longer.
    onPick.mockImplementation(() => false);
    const view = await renderListed(template('alpha'));
    fireEvent.click(pickRow('alpha'));
    await screen.findByRole('status');

    view.rerender(picker(false));

    // Still true: the session is refusing input, which is why nothing landed.
    expect(screen.getByRole('status')).toBeInTheDocument();

    view.rerender(picker(true));

    expect(screen.queryByRole('status')).toBeNull();
  });
});
