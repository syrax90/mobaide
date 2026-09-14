import '@testing-library/jest-dom/vitest';

// The protocol suites run under `// @vitest-environment node`, where none of
// these DOM globals exist and none of the stand-ins are needed.
if (typeof HTMLDialogElement !== 'undefined') {
  // jsdom (30.x) ships HTMLDialogElement with the reflected `open` attribute
  // but none of its methods (jsdom/jsdom#3294), and Modal.tsx calls
  // showModal() on mount. Minimal stand-ins: drive `open` and fire `close`,
  // which is everything the unit tests observe.
  if (typeof HTMLDialogElement.prototype.showModal !== 'function') {
    HTMLDialogElement.prototype.showModal = function showModal(this: HTMLDialogElement) {
      this.open = true;
    };
  }
  if (typeof HTMLDialogElement.prototype.show !== 'function') {
    HTMLDialogElement.prototype.show = function show(this: HTMLDialogElement) {
      this.open = true;
    };
  }
  if (typeof HTMLDialogElement.prototype.close !== 'function') {
    HTMLDialogElement.prototype.close = function close(this: HTMLDialogElement) {
      this.open = false;
      this.dispatchEvent(new Event('close'));
    };
  }

  // jsdom (30.x) has no Popover API either: `showPopover` and friends are
  // missing, `:popover-open` never matches, and its UA sheet still hides every
  // `[popover]` — so `getByRole` would treat an open menu or panel as
  // inaccessible. The stand-in drives an inline `display` and fires the
  // `beforetoggle` / `toggle` pair synchronously with the `oldState` /
  // `newState` React's synthetic event copies (the real events do not bubble).
  if (typeof HTMLElement.prototype.showPopover !== 'function') {
    const toggle = (
      element: HTMLElement,
      type: 'beforetoggle' | 'toggle',
      oldState: string,
      newState: string,
    ): void => {
      element.dispatchEvent(
        Object.assign(new Event(type, { cancelable: type === 'beforetoggle' }), {
          oldState,
          newState,
        }),
      );
    };
    const isOpen = (element: HTMLElement): boolean => element.dataset['popoverOpen'] === '1';
    HTMLElement.prototype.showPopover = function showPopover(this: HTMLElement) {
      if (isOpen(this)) return;
      toggle(this, 'beforetoggle', 'closed', 'open');
      this.dataset['popoverOpen'] = '1';
      this.style.display = 'block';
      toggle(this, 'toggle', 'closed', 'open');
    };
    HTMLElement.prototype.hidePopover = function hidePopover(this: HTMLElement) {
      if (!isOpen(this)) return;
      toggle(this, 'beforetoggle', 'open', 'closed');
      delete this.dataset['popoverOpen'];
      this.style.removeProperty('display');
      toggle(this, 'toggle', 'open', 'closed');
    };
    HTMLElement.prototype.togglePopover = function togglePopover(
      this: HTMLElement,
      force?: boolean,
    ): boolean {
      const open = isOpen(this);
      if (force === true || (force === undefined && !open)) this.showPopover();
      else this.hidePopover();
      return isOpen(this);
    };
  }

  // jsdom implements neither matchMedia (used for the reduced-motion check)
  // nor scrollIntoView (AgentCredentialsPanel scrolls itself into view on
  // mount).
  if (typeof window.matchMedia !== 'function') {
    window.matchMedia = (query: string): MediaQueryList => ({
      matches: false,
      media: query,
      onchange: null,
      addEventListener: () => undefined,
      removeEventListener: () => undefined,
      addListener: () => undefined,
      removeListener: () => undefined,
      dispatchEvent: () => false,
    });
  }
  if (typeof Element.prototype.scrollIntoView !== 'function') {
    Element.prototype.scrollIntoView = () => undefined;
  }

  // jsdom has no pointer capture either, and the scrollbar thumb takes it on
  // pointerdown so a drag that leaves the 14 px track keeps being delivered.
  if (typeof Element.prototype.setPointerCapture !== 'function') {
    Element.prototype.setPointerCapture = () => undefined;
    Element.prototype.releasePointerCapture = () => undefined;
    Element.prototype.hasPointerCapture = () => false;
  }
}
