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
}
