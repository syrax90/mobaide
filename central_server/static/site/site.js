/* Mobaide marketing site — reveal-on-scroll, the hero typewriter, and the
 * install-command copy buttons.
 *
 * Loaded synchronously from <head> (not deferred) so the `js` class lands
 * before first paint: site.css gates every animation start-state on that class,
 * so with scripting off, or if this file fails to load, the page renders fully
 * visible instead of blank.
 *
 * Everything here is progressive enhancement over markup that already reads
 * correctly on its own, and every hidden state has a guaranteed release path —
 * see releaseTranscript() and the watchdog in setUpTypewriter().
 */
(function () {
  "use strict";

  document.documentElement.classList.add("js");

  var reduceMotion =
    window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;

  // The transcript's hidden state is watchdogged as a STALL detector, not a
  // fixed deadline: a plain deadline has to outlast the animation itself, and
  // guessing that duration wrong either truncates the effect or leaves the hero
  // blank for seconds. So: allow this long for typing to begin at all...
  var TRANSCRIPT_START_MS = 2500;
  // ...then re-arm this far past each frame that makes progress. Any freeze —
  // stalled font load, throttled background tab, dead rAF, never-firing
  // observer — releases the content shortly after the last progress, while a
  // healthy animation of any length is never cut short.
  var TRANSCRIPT_STALL_MS = 1500;
  // Bound on waiting for the webfont before typing; see setUpTypewriter().
  var FONT_WAIT_MS = 1200;
  // How long the copy button holds its checkmark. Roughly Android's
  // Toast.LENGTH_SHORT, which is the confirmation this mirrors.
  var COPIED_MS = 2000;

  /* ---- install-command copy buttons ---- */

  /* Puts the command on the clipboard, resolving true/false rather than
   * rejecting: Firefox and Safari refuse a gesture-less write, and an unhandled
   * rejection inside a click handler surfaces somewhere unhelpful. Mirrors the
   * web client's writeClipboardText().
   */
  function writeClipboard(text) {
    try {
      return navigator.clipboard.writeText(text).then(
        function () {
          return true;
        },
        function () {
          return false;
        }
      );
    } catch (err) {
      return Promise.resolve(false);
    }
  }

  /* Fallback for a refused write: hand the visitor a ready-made selection so
   * Ctrl+C still works. Deliberately not document.execCommand("copy") — it is
   * deprecated, and the selection is the part that actually helps.
   */
  function selectContents(node) {
    if (!window.getSelection || !document.createRange) {
      return false;
    }
    var selection = window.getSelection();
    if (!selection) {
      return false;
    }
    var range = document.createRange();
    range.selectNodeContents(node);
    selection.removeAllRanges();
    selection.addRange(range);
    return true;
  }

  function wireCopyButton(button) {
    var block = button.closest(".copyblock");
    if (!block) {
      return;
    }
    var code = block.querySelector("code");
    var status = block.querySelector("[role='status']");
    if (!code) {
      return;
    }

    var timer = null;

    // The button's own aria-label is the stable accessible name, so the
    // outcome goes to a separate live region instead — changing the name of
    // the control the user just pressed is not an announcement.
    var announce = function (message) {
      if (status) {
        status.textContent = message;
      }
    };

    var reset = function () {
      button.removeAttribute("data-copied");
      button.removeAttribute("data-copy-failed");
      announce("");
    };

    button.addEventListener("click", function () {
      // Clear first, so a second click re-arms the full hold rather than
      // inheriting the remainder of the previous one.
      window.clearTimeout(timer);
      reset();

      writeClipboard(code.textContent).then(function (ok) {
        if (ok) {
          button.setAttribute("data-copied", "");
          announce("Command copied");
        } else {
          button.setAttribute("data-copy-failed", "");
          announce(
            selectContents(code)
              ? "Could not copy — the command is selected, press Ctrl+C"
              : "Could not copy — select the command and copy it manually"
          );
        }
        timer = window.setTimeout(reset, COPIED_MS);
      });
    });
  }

  function setUpCopyButtons() {
    // navigator.clipboard is undefined on an insecure origin, so scripting
    // running is not enough to promise a working button. Returning here leaves
    // .copy-btn at its display:none default rather than showing a dead
    // control; the command below it stays selectable text either way.
    if (
      !window.Promise ||
      !navigator.clipboard ||
      !navigator.clipboard.writeText ||
      !Element.prototype.closest
    ) {
      return;
    }
    var buttons = document.querySelectorAll(".copy-btn[data-copy]");
    for (var i = 0; i < buttons.length; i++) {
      wireCopyButton(buttons[i]);
    }
    // Only now, with every button wired, does the stylesheet reveal them.
    document.documentElement.classList.add("can-copy");
  }

  function setUpReveals() {
    var targets = document.querySelectorAll("[data-reveal]");
    if (!("IntersectionObserver" in window)) {
      // No observer: show everything rather than leave sections faded out.
      for (var i = 0; i < targets.length; i++) {
        targets[i].classList.add("is-in");
      }
      return;
    }
    var observer = new IntersectionObserver(
      function (entries) {
        entries.forEach(function (entry) {
          if (entry.isIntersecting) {
            entry.target.classList.add("is-in");
            observer.unobserve(entry.target);
          }
        });
      },
      { rootMargin: "0px 0px -12% 0px", threshold: 0.05 }
    );
    for (var j = 0; j < targets.length; j++) {
      observer.observe(targets[j]);
    }
  }

  /* Hands the transcript back to plain CSS: drops every per-line animation
   * class and marks the root ready, which is what the stylesheet keys the
   * hidden state off. Idempotent, so any number of exit paths may call it.
   */
  function releaseTranscript(root) {
    var lines = root.querySelectorAll(".t-line");
    for (var i = 0; i < lines.length; i++) {
      lines[i].classList.remove("is-typing", "is-shown");
      lines[i].style.removeProperty("--shown");
    }
    root.classList.add("is-ready");
  }

  /* Types the transcript line by line by widening a clip-path in whole
   * character cells. Clipping rather than rewriting text is what lets the
   * per-token colour spans inside each line survive the animation.
   */
  function typeTranscript(root, onProgress, onDone) {
    var lines = Array.prototype.slice.call(root.querySelectorAll(".t-line"));
    if (!lines.length) {
      onDone();
      return;
    }

    // ~2.5s for the whole transcript. The panel is already sized for all its
    // lines (so nothing shifts), but it starts visually empty, and a slower
    // crawl leaves the hero looking broken rather than alive.
    var CHAR_MS = 4;
    var LINE_PAUSE_MS = 45;

    var plan = lines.map(function (line) {
      return { line: line, chars: line.textContent.length };
    });

    var index = 0;
    var shown = 0;
    var last = null;
    var carry = 0;

    function step(now) {
      if (root.classList.contains("is-ready")) {
        return; // watchdog got here first
      }
      if (last === null) {
        last = now;
      }
      carry += now - last;
      last = now;

      while (index < plan.length) {
        var item = plan[index];

        if (!item.line.classList.contains("is-typing")) {
          item.line.classList.add("is-typing", "is-shown");
          item.line.style.setProperty("--shown", "0");
          shown = 0;
        }

        var budget = Math.floor(carry / CHAR_MS);
        if (budget < 1) {
          window.requestAnimationFrame(step);
          return;
        }

        var next = Math.min(item.chars, shown + budget);
        carry -= (next - shown) * CHAR_MS;
        shown = next;
        item.line.style.setProperty("--shown", String(shown));
        onProgress();

        if (shown < item.chars) {
          window.requestAnimationFrame(step);
          return;
        }

        // Dropping is-typing removes the clip-path, so a finished line never
        // depends on its --shown value; is-shown keeps it visible.
        item.line.classList.remove("is-typing");
        item.line.style.removeProperty("--shown");
        index += 1;
        shown = 0;
        carry -= LINE_PAUSE_MS;
        if (carry < 0) {
          carry = 0;
        }
      }

      onDone();
    }

    window.requestAnimationFrame(step);
  }

  function setUpTypewriter() {
    var root = document.querySelector("[data-typewriter]");
    if (!root) {
      return;
    }

    var watchdog = null;
    var armWatchdog = function (delay) {
      window.clearTimeout(watchdog);
      watchdog = window.setTimeout(function () {
        releaseTranscript(root);
      }, delay);
    };
    armWatchdog(TRANSCRIPT_START_MS);

    var onProgress = function () {
      armWatchdog(TRANSCRIPT_STALL_MS);
    };

    var finish = function () {
      window.clearTimeout(watchdog);
      releaseTranscript(root);
    };

    var start = function () {
      // `--shown` steps in `ch`, which resolves against whatever font is
      // active, so typing before the webfont swaps would clip against the
      // fallback's advance width and cut characters mid-glyph. The race keeps a
      // font load that never settles from stranding the transcript — headless
      // Chrome, for one, can leave document.fonts.ready pending indefinitely.
      var waited = false;
      var begin = function () {
        if (waited) {
          return;
        }
        waited = true;
        typeTranscript(root, onProgress, finish);
      };
      window.setTimeout(begin, FONT_WAIT_MS);
      if (document.fonts && document.fonts.ready) {
        document.fonts.ready.then(begin, begin);
      } else {
        begin();
      }
    };

    if (!("IntersectionObserver" in window)) {
      start();
      return;
    }
    var observer = new IntersectionObserver(
      function (entries) {
        if (entries[0].isIntersecting) {
          observer.disconnect();
          start();
        }
      },
      { threshold: 0.25 }
    );
    observer.observe(root);
  }

  function init() {
    // Ahead of the reduced-motion return below: copying is not motion, and a
    // visitor who asked for less of it still needs the button to work.
    setUpCopyButtons();

    if (reduceMotion) {
      // The `.js` class has already armed the hidden states, so hand the
      // transcript straight back rather than leaving it hidden.
      var root = document.querySelector("[data-typewriter]");
      if (root) {
        releaseTranscript(root);
      }
      return;
    }
    setUpReveals();
    setUpTypewriter();
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
