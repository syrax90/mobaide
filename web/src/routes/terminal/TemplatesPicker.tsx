import {
  useEffect,
  useId,
  useMemo,
  useRef,
  useState,
  type KeyboardEvent,
} from 'react';
import { Link } from 'react-router';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { ErrorState } from '../../components/ErrorState';
import { Modal } from '../../components/Modal';
import {
  groupTemplates,
  listTemplates,
  setTemplateFavorite,
  touchTemplate,
  type PromptTemplate,
} from '../../api/templateRepository';
import { queryKeys, wsBackedQueryOptions } from '../../lib/queryKeys';

interface TemplatesPickerProps {
  /** False while the session refuses input: rows are disabled and say why. */
  canSend: boolean;
  /** Returns whether the body actually landed — a refused insert is not picked. */
  onPick: (template: PromptTemplate) => boolean;
  onClose: () => void;
}

interface SectionProps {
  title: string;
  templates: readonly PromptTemplate[];
  disabled: boolean;
  onPick: (template: PromptTemplate) => void;
  onToggleFavorite: (template: PromptTemplate) => void;
  /** Rows whose favourite PATCH is in flight. */
  pendingFavorites: ReadonlySet<string>;
}

function Section({
  title,
  templates,
  disabled,
  onPick,
  onToggleFavorite,
  pendingFavorites,
}: SectionProps) {
  if (templates.length === 0) return null;
  return (
    <section className="template-section">
      <h3>{title}</h3>
      <ul className="template-list">
        {templates.map((template) => (
          <li key={template.templateId} className="template-row">
            <button
              type="button"
              className="template-pick"
              // The arrow-key walk reads the rows back out of the DOM in render
              // order, which is the order on screen across all three sections.
              data-template-row=""
              disabled={disabled}
              onClick={() => {
                onPick(template);
              }}
            >
              <span className="template-name">{template.name}</span>
              <span className="template-body">{template.body}</span>
            </button>
            <button
              type="button"
              className="template-favorite"
              // The PATCH carries an absolute `is_favorite`, computed from the
              // value on screen — and that value only moves once the refetch
              // lands. A second click inside the round trip would send the same
              // boolean again and silently drop the user's toggle. Same
              // in-flight guard `Devices` and `GraceBanner` use.
              disabled={pendingFavorites.has(template.templateId)}
              aria-pressed={template.isFavorite}
              aria-label={
                template.isFavorite
                  ? `Remove ${template.name} from favourites`
                  : `Add ${template.name} to favourites`
              }
              onClick={() => {
                onToggleFavorite(template);
              }}
            >
              {template.isFavorite ? '★' : '☆'}
            </button>
          </li>
        ))}
      </ul>
    </section>
  );
}

/**
 * Read-only prompt-template picker. Creating, editing and deleting templates
 * live on `/templates`; this is the terminal's entry point into the list.
 *
 * A `Modal`, not a floating panel: as a plain `position: fixed` div it was
 * inert, because `addon-webgl` appends a full-grid link canvas at `z-index: 2`
 * and nothing between it and the root creates a stacking context, so it
 * hit-tested above a picker whose own `z-index` was `auto`. `showModal()` puts
 * this on the top layer, which no page z-index can reach.
 *
 * Messages render inline rather than as toasts for the same layering reason in
 * reverse: `.toast-viewport` sits below the top layer, so a toast raised while
 * this is open cannot be seen.
 *
 * Android refreshes and *then* opens the sheet, iOS opens and fetches inside it.
 * The web client takes Android's shape via `refetch()` on mount, because the
 * query is `staleTime: Infinity` (the user-events bridge invalidates it), so
 * without a deliberate fetch a cache warmed an hour ago would render as truth.
 */
export function TemplatesPicker({ canSend, onPick, onClose }: TemplatesPickerProps) {
  const queryClient = useQueryClient();
  const [filter, setFilter] = useState('');
  const [notice, setNotice] = useState<string | null>(null);
  const titleId = useId();
  const searchRef = useRef<HTMLInputElement>(null);
  const containerRef = useRef<HTMLDivElement>(null);

  const templates = useQuery({
    queryKey: queryKeys.templates(),
    queryFn: listTemplates,
    refetchOnMount: 'always',
    ...wsBackedQueryOptions,
  });

  // Tracked as a set rather than read off the mutation observer. A
  // `useMutation` observer only ever describes its *latest* call — `mutate()`
  // detaches the previous mutation — so toggling row A then row B would hand
  // A's star back while A's PATCH was still running, and a third click on A
  // would re-send the same stale absolute boolean. A half-guard is worse than
  // none: it advertises a serialization it does not provide.
  const [pendingFavorites, setPendingFavorites] = useState<ReadonlySet<string>>(
    new Set(),
  );

  const favorite = useMutation({
    mutationFn: (template: PromptTemplate) =>
      setTemplateFavorite(template.templateId, !template.isFavorite),
    onMutate: (template) => {
      setNotice(null);
      setPendingFavorites((pending) => new Set(pending).add(template.templateId));
    },
    onSettled: (_data, _error, template) => {
      setPendingFavorites((pending) => {
        const next = new Set(pending);
        next.delete(template.templateId);
        return next;
      });
    },
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: queryKeys.templates() });
    },
    onError: () => {
      setNotice('Failed to update the template.');
    },
  });

  // The picker is the only channel for these messages now that the toast path
  // is gone, so a stale one is the user's only signal. A refusal is about a
  // gate that has since reopened: retire it with the condition that raised it.
  useEffect(() => {
    if (canSend) setNotice(null);
  }, [canSend]);

  const needle = filter.trim();
  // Memoised because the parent re-renders far more often than this input
  // changes: `TerminalView` reads the whole view store, which `sampleFollow()`
  // writes once per parsed PTY chunk while the grid is scrolled up. Without
  // this, every one of those frames re-lowercases every name and body.
  // `templates.data` is reference-stable across renders (TanStack structural
  // sharing), so the memo recomputes only on a real list or filter change.
  const data = templates.data;
  const grouped = useMemo(() => groupTemplates(data ?? [], filter), [data, filter]);
  const empty =
    grouped.recents.length === 0 &&
    grouped.favorites.length === 0 &&
    grouped.others.length === 0;

  const pick = (template: PromptTemplate): void => {
    setNotice(null);
    // A refused insert must not promote the template into Recents — the stamp
    // would advertise an insert that never happened. The rows are already
    // disabled while the gate is shut; this covers it closing between the
    // render and the click. Deliberately not naming the send gate: `pasteText`
    // also refuses a body that is empty after control-stripping, and a guess
    // about which one fired would be wrong half the time.
    if (!onPick(template)) {
      setNotice('The template was not inserted.');
      return;
    }
    // Fire-and-forget: the insert already happened, and a failed stamp only
    // costs this template its place in the recents group.
    void touchTemplate(template.templateId)
      .then(() => queryClient.invalidateQueries({ queryKey: queryKeys.templates() }))
      .catch(() => undefined);
  };

  const rows = (): HTMLButtonElement[] =>
    Array.from(
      containerRef.current?.querySelectorAll<HTMLButtonElement>(
        '[data-template-row]:not(:disabled)',
      ) ?? [],
    );

  const moveFocus = (delta: number): void => {
    const items = rows();
    if (items.length === 0) return;
    // Keyed on the enclosing row, not on the pick button itself: the favourite
    // star is a sibling inside the same `<li>` and is reachable by Tab, so
    // matching the button identity would report "outside the list" for a focus
    // that is very much inside it and teleport to the far end.
    const active = document.activeElement;
    const row = active instanceof Element ? active.closest('.template-row') : null;
    const index = items.findIndex((item) => item.closest('.template-row') === row);
    // Focus is on the search field or the Close button: enter the list from
    // whichever end the key points at.
    if (index === -1) {
      (delta > 0 ? items[0] : items[items.length - 1])?.focus();
      return;
    }
    // Up off the first row returns to the search field rather than wrapping —
    // the field is where the list is narrowed from, so it is the natural top.
    if (index + delta < 0) {
      searchRef.current?.focus();
      return;
    }
    items[Math.min(index + delta, items.length - 1)]?.focus();
  };

  const handleKeyDown = (event: KeyboardEvent<HTMLDivElement>): void => {
    // Mid-composition every one of these keys belongs to the IME: Enter commits
    // a candidate and the arrows move through the candidate window. Same guard,
    // and the same two signals, as `keyboard/recordChord.ts`.
    if (event.nativeEvent.isComposing || event.nativeEvent.keyCode === 229) return;

    // One physical press, one action. A held Enter repeats at the OS rate, and
    // the press that opened this dialog arrives again on the search field and
    // again on whatever row focus just landed on — where the browser's own
    // button activation would paste into a live PTY. Cancelling here suppresses
    // that activation, because default actions run after the event dispatch.
    // Arrows are left alone: held-arrow navigation is the point.
    if (event.key === 'Enter' && event.repeat) {
      event.preventDefault();
      return;
    }

    // Deterministic dismissal. `<dialog>` turns Esc into a close request, but
    // Chrome and Safari let a non-empty `input[type=search]` consume that
    // keydown to clear itself first — and this dialog opens with the caret in
    // exactly that field, so the user would need two presses. `onClose` is
    // idempotent, so a browser that also raises the native request is harmless.
    if (event.key === 'Escape') {
      event.preventDefault();
      onClose();
      return;
    }

    if (event.key === 'ArrowDown') {
      event.preventDefault();
      moveFocus(1);
    } else if (event.key === 'ArrowUp') {
      event.preventDefault();
      moveFocus(-1);
    } else if (
      event.key === 'Enter' &&
      searchRef.current !== null &&
      event.target === searchRef.current
    ) {
      event.preventDefault();
      // Only ever pick something the user narrowed to. With an empty field the
      // "first row" is the most recently used template, and an Enter that was
      // meant for the button that opened this dialog — or a reflex to dismiss
      // it — would paste a body nobody chose into a live PTY. Bare Enter moves
      // into the list instead, so the row it would fire is visible first.
      if (needle === '') moveFocus(1);
      else rows()[0]?.click();
    }
  };

  return (
    <Modal
      labelledBy={titleId}
      className="template-picker"
      initialFocus={searchRef}
      onClose={onClose}
    >
      <div className="template-picker-inner" ref={containerRef} onKeyDown={handleKeyDown}>
        <header className="template-picker-head">
          <h2 id={titleId}>Templates</h2>
          <button type="button" className="button" onClick={onClose}>
            Close
          </button>
        </header>

        <input
          ref={searchRef}
          type="search"
          className="field-input"
          value={filter}
          placeholder="Search templates"
          aria-label="Search templates"
          onChange={(event) => {
            setFilter(event.target.value);
          }}
        />

        {!canSend && (
          <p className="template-picker-notice">
            The session is not accepting input, so templates cannot be inserted right
            now.
          </p>
        )}
        {notice !== null && (
          <p className="template-picker-notice" role="status" aria-live="polite">
            {notice}
          </p>
        )}

        <div className="template-picker-body">
          {templates.isPending && (
            <p className="page-note state-loading">Loading templates…</p>
          )}
          {templates.isError && (
            <ErrorState
              message="Failed to load templates."
              onRetry={() => {
                void templates.refetch();
                // The button unmounts the moment the refetch lands, and focus
                // would fall to the dialog — outside the element holding the
                // key handler, which kills the arrow walk until the user clicks
                // back in. The field is mounted in every state.
                searchRef.current?.focus();
              }}
            />
          )}
          {!templates.isPending && !templates.isError && empty && (
            // Two different situations, two different answers: a filter that
            // matched nothing is fixed by editing the filter, an empty account
            // is fixed on the management screen.
            <p className="empty-state">
              {needle === '' ? (
                <>
                  No templates yet. <Link to="/templates">Manage templates</Link>
                </>
              ) : (
                `No templates match “${needle}”.`
              )}
            </p>
          )}

          {/* "Recents"/"Favorites"/"All", not a local variant: `TemplatesScreen`
              documents these headings as a contract both mobile pickers hold to
              with exactly this disjoint split. */}
          <Section
            title="Recents"
            templates={grouped.recents}
            disabled={!canSend}
            onPick={pick}
            onToggleFavorite={favorite.mutate}
            pendingFavorites={pendingFavorites}
          />
          <Section
            title="Favorites"
            templates={grouped.favorites}
            disabled={!canSend}
            onPick={pick}
            onToggleFavorite={favorite.mutate}
            pendingFavorites={pendingFavorites}
          />
          <Section
            title="All"
            templates={grouped.others}
            disabled={!canSend}
            onPick={pick}
            onToggleFavorite={favorite.mutate}
            pendingFavorites={pendingFavorites}
          />
        </div>
      </div>
    </Modal>
  );
}
