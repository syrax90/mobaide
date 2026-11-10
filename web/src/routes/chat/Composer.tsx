import {
  useCallback,
  useEffect,
  useId,
  useImperativeHandle,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
  type ClipboardEvent,
  type KeyboardEvent,
  type Ref,
} from 'react';
import { useStore } from 'zustand';
import type { ChatSessionModel } from '../../chat/chatModel';
import {
  MAX_CHAT_IMAGES,
  MAX_TOTAL_BASE64,
  imageFilesFrom,
  prepareChatImage,
  totalBase64,
} from '../../chat/chatImages';
import type { ChatCommandInfo } from '../../chat/chatWire';
import { useUnloadGuard } from '../../keyboard/useUnloadGuard';
import { useToastStore } from '../../stores/toastStore';
import { useComposerVoice } from '../../voice/useComposerVoice';
import { spliceTranscript } from '../../voice/useDictation';
import { TemplateEditorDialog } from '../templates/TemplateEditorDialog';
import { useSaveTemplate } from '../templates/useSaveTemplate';
import { TemplatesPicker } from '../terminal/TemplatesPicker';
import { ComposerVoiceDialogs } from '../terminal/voice/ComposerVoiceDialogs';
import { VoiceButton } from '../terminal/voice/VoiceButton';
import { CrossIcon, ImageIcon, SendIcon, StopIcon, TemplateIcon } from './ChatIcons';

const MAX_SUGGESTIONS = 50;
const IMAGE_ACCEPT = 'image/png,image/jpeg,image/gif,image/webp';
const IMAGES_TOO_LARGE = 'These images are too large to send together.';

export interface ComposerHandle {
  addImages: (files: readonly File[]) => void;
  focus: () => void;
}

interface ComposerProps {
  model: ChatSessionModel;
  /** "Claude Code" — names the placeholder. */
  kindLabel: string;
  /** The session's kind (`claude`, `codex`…); undefined until its row arrives. */
  sessionKind: string | undefined;
  live: boolean;
  /** The session is over: nothing can be typed or sent. */
  closed: boolean;
  /** The agent stopped (`error`): typing stays, sending waits for Restart. */
  halted: boolean;
  running: boolean;
  cancelling: boolean;
  acceptsImages: boolean;
  commands: readonly ChatCommandInfo[];
  handleRef?: Ref<ComposerHandle>;
}

/** Commands matching what follows the `/`: by prefix first, then by substring. */
function matchCommands(text: string, commands: readonly ChatCommandInfo[]): ChatCommandInfo[] {
  if (!text.startsWith('/') || /\s/.test(text)) return [];
  const query = text.slice(1).toLowerCase();
  const prefix: ChatCommandInfo[] = [];
  const inner: ChatCommandInfo[] = [];
  for (const command of commands) {
    const name = command.name.toLowerCase();
    if (name.startsWith(query)) prefix.push(command);
    else if (query !== '' && name.includes(query)) inner.push(command);
  }
  return [...prefix, ...inner].slice(0, MAX_SUGGESTIONS);
}

type TemplatePanel = { kind: 'none' } | { kind: 'picker' } | { kind: 'editor'; initialBody: string };

export function Composer({
  model,
  kindLabel,
  sessionKind,
  live,
  closed,
  halted,
  running,
  cancelling,
  acceptsImages,
  commands,
  handleRef,
}: ComposerProps) {
  const [text, setTextState] = useState(() => model.draft);
  // In the model, not here: a preparation that ends after the user switched
  // sessions and came back must reach the composer now on screen.
  const images = useStore(model.store, (view) => view.draftImages);
  const preparing = useStore(model.store, (view) => view.preparingImages);
  const [active, setActive] = useState(0);
  const [dismissedFor, setDismissedFor] = useState<string | null>(null);
  const [hint, setHint] = useState<{ text: string; hint: string } | null>(null);
  const [panel, setPanel] = useState<TemplatePanel>({ kind: 'none' });
  const fieldRef = useRef<HTMLTextAreaElement>(null);
  const fileRef = useRef<HTMLInputElement>(null);
  const caretRef = useRef<number | null>(null);
  const listId = useId();
  const hintId = useId();

  const setText = useCallback(
    (next: string) => {
      setTextState(next);
      model.draft = next;
    },
    [model],
  );

  useLayoutEffect(() => {
    const field = fieldRef.current;
    if (field === null) return;
    field.style.height = 'auto';
    field.style.height = `${String(field.scrollHeight)}px`;
    const caret = caretRef.current;
    if (caret !== null && field.value === text) {
      caretRef.current = null;
      field.focus();
      field.setSelectionRange(caret, caret);
    }
  }, [text]);

  const suggestions = useMemo(
    () => (dismissedFor === text ? [] : matchCommands(text, commands)),
    [text, commands, dismissedFor],
  );
  const open = suggestions.length > 0;
  const activeIndex = Math.min(active, Math.max(0, suggestions.length - 1));

  const insertAtCaret = useCallback(
    (insert: string) => {
      const field = fieldRef.current;
      const start = field?.selectionStart ?? text.length;
      const end = field?.selectionEnd ?? text.length;
      const before = text.slice(0, start);
      const after = text.slice(end);
      const spliced = spliceTranscript({ before, after }, insert);
      caretRef.current = spliced.caret;
      setText(spliced.text);
    },
    [text, setText],
  );

  const addImages = useCallback(
    (files: readonly File[]) => {
      const picked = imageFilesFrom(files);
      if (picked.length === 0) return;
      if (!acceptsImages) {
        useToastStore.getState().show('This agent does not accept images.', 'error');
        return;
      }
      // From the model, not the render: it already counts what was just attached.
      const room = MAX_CHAT_IMAGES - model.draftImages.length - model.state().preparingImages;
      if (room <= 0) {
        useToastStore.getState().show(`Attach at most ${String(MAX_CHAT_IMAGES)} images.`, 'error');
        return;
      }
      if (picked.length > room) {
        useToastStore.getState().show(`Attach at most ${String(MAX_CHAT_IMAGES)} images.`, 'error');
      }
      const batch = picked.slice(0, room);
      model.notePreparingImages(batch.length);
      // One at a time: each decode holds a full-size bitmap, and a few phone
      // photos at once can take the tab past its memory limit.
      void (async () => {
        let tooLarge = false;
        for (const file of batch) {
          try {
            const image = await prepareChatImage(file);
            if (!model.attachDraftImage(image)) tooLarge = true;
          } catch (error: unknown) {
            useToastStore
              .getState()
              .show(error instanceof Error ? error.message : 'Could not add the image.', 'error');
          } finally {
            model.notePreparingImages(-1);
          }
        }
        if (tooLarge) useToastStore.getState().show(IMAGES_TOO_LARGE, 'error');
      })();
    },
    [acceptsImages, model],
  );

  useImperativeHandle(
    handleRef,
    () => ({
      addImages,
      focus: () => {
        fieldRef.current?.focus();
      },
    }),
    [addImages],
  );

  const hasContent = text.trim() !== '' || images.length > 0;
  const canSend = live && !closed && !halted && hasContent && preparing === 0;
  const showStop = running && !hasContent;

  const send = () => {
    if (!canSend) return;
    if (totalBase64(images) > MAX_TOTAL_BASE64) {
      useToastStore.getState().show(IMAGES_TOO_LARGE, 'error');
      return;
    }
    const body = text;
    const attached = images;
    setText('');
    model.draftImages = [];
    setHint(null);
    void model
      .prompt(
        body,
        attached.map(({ mime, data }) => ({ mime, data })),
      )
      .then((result) => {
        if (result.ok) {
          for (const image of attached) URL.revokeObjectURL(image.previewUrl);
          return;
        }
        // Through the model: the user may have left the session meanwhile.
        model.restoreDraft(body, attached);
        setTextState(model.draft);
        useToastStore
          .getState()
          .show(
            result.lost
              ? 'The connection dropped. Check that the message was sent.'
              : `Message not sent: ${result.error}`,
            'error',
          );
      });
  };

  const stop = () => {
    if (!live || cancelling) return;
    void model.cancel().then((result) => {
      if (!result.ok && !result.lost) {
        useToastStore.getState().show(`Could not stop: ${result.error}`, 'error');
      }
    });
  };

  const pick = (command: ChatCommandInfo) => {
    const next = `/${command.name} `;
    caretRef.current = next.length;
    setText(next);
    setActive(0);
    setHint(command.hint !== null && command.hint !== '' ? { text: next, hint: command.hint } : null);
  };

  const onKeyDown = (event: KeyboardEvent<HTMLTextAreaElement>) => {
    if (event.nativeEvent.isComposing) return;
    if (open) {
      if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
        event.preventDefault();
        const step = event.key === 'ArrowDown' ? 1 : -1;
        setActive((activeIndex + step + suggestions.length) % suggestions.length);
        return;
      }
      if ((event.key === 'Enter' && !event.shiftKey) || event.key === 'Tab') {
        const command = suggestions[activeIndex];
        if (command !== undefined) {
          event.preventDefault();
          pick(command);
          return;
        }
      }
      if (event.key === 'Escape') {
        event.preventDefault();
        setDismissedFor(text);
        return;
      }
    }
    if (event.key === 'Enter' && !event.shiftKey && !event.altKey) {
      event.preventDefault();
      send();
      return;
    }
    if (event.key === 'Escape' && running) {
      event.preventDefault();
      stop();
      return;
    }
    // Ctrl+C stops a turn, as in the terminal — but only with nothing selected
    // to copy, and never ⌘C.
    if (
      event.key === 'c' &&
      event.ctrlKey &&
      !event.metaKey &&
      running &&
      event.currentTarget.selectionStart === event.currentTarget.selectionEnd
    ) {
      event.preventDefault();
      stop();
    }
  };

  const onPaste = (event: ClipboardEvent<HTMLTextAreaElement>) => {
    const files = imageFilesFrom(event.clipboardData.files);
    if (files.length === 0) return;
    if (event.clipboardData.getData('text/plain') === '') event.preventDefault();
    addImages(files);
  };

  // Voice: the same mic and dictation flow as the terminal, but the transcript
  // lands in this composer instead of the PTY.
  const insertTranscript = useCallback(
    (transcript: string): boolean => {
      if (closed) return false;
      insertAtCaret(transcript);
      return true;
    },
    [closed, insertAtCaret],
  );
  const voice = useComposerVoice(model.agentId, model.sessionId, {
    insert: insertTranscript,
    sessionKind,
  });

  const dialogOpen = panel.kind !== 'none' || voice.dialogOpen;
  const dialogWasOpen = useRef(false);
  useEffect(() => {
    if (dialogWasOpen.current && !dialogOpen) fieldRef.current?.focus();
    dialogWasOpen.current = dialogOpen;
  }, [dialogOpen]);

  useUnloadGuard(hasContent);

  const save = useSaveTemplate(() => {
    setPanel({ kind: 'none' });
  });

  const activeOptionId = open ? `${listId}-${String(activeIndex)}` : undefined;
  const sendLabel = running ? 'Queue message' : 'Send message';

  return (
    <div className="chat-composer">
      {open && (
        <ul className="chat-suggestions" id={listId} role="listbox" aria-label="Commands">
          {suggestions.map((command, index) => (
            <li
              key={command.name}
              id={`${listId}-${String(index)}`}
              role="option"
              aria-selected={index === activeIndex}
              className={`chat-suggestion${index === activeIndex ? ' chat-suggestion-active' : ''}`}
              ref={(node) => {
                if (index === activeIndex) node?.scrollIntoView({ block: 'nearest' });
              }}
              onMouseDown={(event) => {
                event.preventDefault();
              }}
              onClick={() => {
                pick(command);
              }}
            >
              <span className="chat-suggestion-name">/{command.name}</span>
              {command.description !== '' && (
                <span className="chat-suggestion-description">{command.description}</span>
              )}
            </li>
          ))}
        </ul>
      )}

      {(images.length > 0 || preparing > 0) && (
        <ul className="chat-thumbs" aria-label="Attached images">
          {images.map((image, index) => (
            <li key={image.previewUrl} className="chat-thumb">
              <img src={image.previewUrl} alt={image.name === '' ? `Image ${String(index + 1)}` : image.name} />
              <button
                type="button"
                className="chat-thumb-remove"
                aria-label={`Remove ${image.name === '' ? `image ${String(index + 1)}` : image.name}`}
                onClick={() => {
                  URL.revokeObjectURL(image.previewUrl);
                  model.draftImages = model.draftImages.filter((entry) => entry !== image);
                  fieldRef.current?.focus();
                }}
              >
                <CrossIcon />
              </button>
            </li>
          ))}
          {preparing > 0 && (
            <li className="chat-thumb chat-thumb-loading" aria-label="Preparing image">
              <span className="chat-spinner" aria-hidden="true" />
            </li>
          )}
        </ul>
      )}

      <div className="chat-composer-row">
        {acceptsImages && (
          <>
            <button
              type="button"
              className="button chat-attach-button"
              aria-label="Attach images"
              title="Attach images"
              disabled={closed || images.length + preparing >= MAX_CHAT_IMAGES}
              onClick={() => fileRef.current?.click()}
            >
              <ImageIcon />
            </button>
            <input
              ref={fileRef}
              type="file"
              accept={IMAGE_ACCEPT}
              multiple
              hidden
              onChange={(event) => {
                addImages(imageFilesFrom(event.target.files));
                event.target.value = '';
              }}
            />
          </>
        )}
        <div className="chat-field-wrap">
          <textarea
            ref={fieldRef}
            className="chat-field"
            rows={1}
            value={text}
            placeholder={closed ? 'This session is closed.' : `Message ${kindLabel}…`}
            aria-label={`Message ${kindLabel}`}
            aria-describedby={hintId}
            aria-controls={open ? listId : undefined}
            aria-activedescendant={activeOptionId}
            disabled={closed}
            onChange={(event) => {
              setText(event.target.value);
              setActive(0);
              if (dismissedFor !== null && event.target.value !== dismissedFor) setDismissedFor(null);
            }}
            onKeyDown={onKeyDown}
            onPaste={onPaste}
          />
          {hint !== null && hint.text === text && (
            <div className="chat-field-ghost" aria-hidden="true">
              <span className="chat-field-ghost-text">{text}</span>
              <span className="chat-field-ghost-hint">{hint.hint}</span>
            </div>
          )}
        </div>
        {voice.micMode !== 'hidden' && (
          <VoiceButton
            mode={voice.micMode}
            status={voice.status}
            disabled={closed}
            onClick={voice.onMicClick}
          />
        )}
        <button
          type="button"
          className="button chat-templates-button"
          aria-label="Templates"
          title="Insert a prompt template"
          disabled={closed}
          onClick={() => {
            setPanel({ kind: 'picker' });
          }}
        >
          <TemplateIcon />
          <span className="chat-templates-label">Templates</span>
        </button>
        {showStop ? (
          <button
            type="button"
            className="button button-danger chat-send"
            aria-label={cancelling ? 'Stopping' : 'Stop'}
            title="Stop (Esc)"
            disabled={!live || cancelling}
            onClick={stop}
          >
            <StopIcon />
          </button>
        ) : (
          <button
            type="button"
            className="button button-primary chat-send"
            aria-label={sendLabel}
            title={running ? 'Queue message (Enter)' : 'Send (Enter)'}
            disabled={!canSend}
            onClick={send}
          >
            <SendIcon />
          </button>
        )}
      </div>
      <p className="chat-composer-hint" id={hintId}>
        Enter to send · Shift+Enter for a new line
      </p>
      <p className="visually-hidden" role="status" aria-live="polite">
        {open ? `${String(suggestions.length)} commands. Use the arrow keys to choose.` : ''}
      </p>

      {panel.kind === 'picker' && (
        <TemplatesPicker
          canSend={!closed}
          onClose={() => {
            setPanel({ kind: 'none' });
          }}
          onNewTemplate={() => {
            save.clearError();
            setPanel({ kind: 'editor', initialBody: text });
          }}
          onPick={(template) => {
            if (closed) return false;
            setPanel({ kind: 'none' });
            insertAtCaret(template.body);
            return true;
          }}
        />
      )}
      {panel.kind === 'editor' && (
        <TemplateEditorDialog
          editing={null}
          initialBody={panel.initialBody}
          busy={save.busy}
          error={save.error}
          onSave={save.save}
          onCancel={() => {
            setPanel({ kind: 'none' });
            save.clearError();
          }}
        />
      )}
      <ComposerVoiceDialogs voice={voice} canSend={!closed} canRecord={!closed} />
    </div>
  );
}
