/* Mobaide marketing site — reveal-on-scroll, the hero typewriter, the
 * install-command copy buttons, and the Android/iOS screenshot switch.
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
   * Ctrl+C (Command+C on macOS) still works. Deliberately not
   * document.execCommand("copy") — it is deprecated, and the selection is the
   * part that actually helps.
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
    // Bumped per activation so a write that settles after a newer click is
    // recognised as stale. Without it two in-flight writes both repaint the
    // button and both schedule a reset, and only the newest reset is
    // cancellable — the older one then fires early and cuts the newer hold
    // short, or an older failure repaints over a newer success.
    var generation = 0;

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
      var mine = ++generation;
      // Clear first, so a second click re-arms the full hold rather than
      // inheriting the remainder of the previous one.
      window.clearTimeout(timer);
      reset();

      writeClipboard(code.textContent).then(function (ok) {
        if (mine !== generation) {
          return; // a newer click owns the button now
        }
        if (ok) {
          button.setAttribute("data-copied", "");
          announce("Command copied");
        } else {
          button.setAttribute("data-copy-failed", "");
          // Naming both chords beats guessing the platform: macOS is one of
          // the two targets this very block installs onto, and Ctrl+C alone
          // would send half those visitors a shortcut that does nothing.
          announce(
            selectContents(code)
              ? "Could not copy — the command is selected, press Ctrl+C " +
                  "(Command+C on macOS)"
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

  /* ---- Android/iOS screenshot switch ---- */

  /* Turns the two stacked screenshot panels into a tab pair. The markup ships
   * with both panels visible and the tablist at display:none, so every early
   * return below leaves a page that still shows every screenshot under its own
   * heading — the `is-live` class at the end is what swaps in the switched
   * presentation, and it is only reached once both tabs are wired.
   */
  function setUpShotSwitch() {
    var container = document.querySelector("[data-shots-switch]");
    if (!container) {
      return;
    }
    var tabs = container.querySelectorAll("[role=tab]");
    if (tabs.length !== 2) {
      return;
    }
    var panels = [];
    for (var i = 0; i < tabs.length; i++) {
      var panel = document.getElementById(tabs[i].getAttribute("aria-controls"));
      if (!panel) {
        return;
      }
      panels.push(panel);
    }

    function select(index, moveFocus) {
      for (var j = 0; j < tabs.length; j++) {
        var on = j === index;
        tabs[j].setAttribute("aria-selected", on ? "true" : "false");
        // Roving tabindex: one stop for the whole group, arrows do the rest.
        tabs[j].tabIndex = on ? 0 : -1;
        panels[j].hidden = !on;
      }
      if (moveFocus) {
        tabs[index].focus();
      }
    }

    for (var k = 0; k < tabs.length; k++) {
      (function (index) {
        tabs[index].addEventListener("click", function () {
          select(index, false);
        });
        tabs[index].addEventListener("keydown", function (event) {
          // Left/Right only: the tablist is horizontal, so Up/Down belong to
          // the page. Claiming them would swallow a scroll keypress — and
          // switch platforms instead — whenever a tab holds focus.
          var next = null;
          if (event.key === "ArrowRight") {
            next = (index + 1) % tabs.length;
          } else if (event.key === "ArrowLeft") {
            next = (index + tabs.length - 1) % tabs.length;
          } else if (event.key === "Home") {
            next = 0;
          } else if (event.key === "End") {
            next = tabs.length - 1;
          }
          if (next === null) {
            return;
          }
          event.preventDefault();
          // Automatic activation: the panels are already in the document and
          // the hidden one's images are loading="lazy", so arrowing across
          // costs nothing a click would not.
          select(next, true);
        });
      })(k);
    }

    // Most visitors arrive on the phone they would install on. An iPad carries
    // no iPad token: since iPadOS 13 Safari asks for desktop sites by default
    // and claims to be a Macintosh, so the only thing separating it from a real
    // Mac is touch — Apple ships no touchscreen Mac, and a trackpad reports no
    // touch points.
    var ua = navigator.userAgent || "";
    var onIos =
      /iPad|iPhone|iPod/.test(ua) ||
      (/Macintosh/.test(ua) && (navigator.maxTouchPoints || 0) > 1);
    select(onIos ? 1 : 0, false);
    container.classList.add("is-live");
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


  /* ---- Google Ads consent banner ---- */

  // Public Ads tag id — the same value every visitor's DOM would show, so it
  // ships in source rather than through template plumbing.
  var ADS_TAG_ID = "AW-18403068615";
  // Conversion label of the "Page view" conversion action, minted by Google
  // Ads next to the tag id. `config` alone pings the tag but counts no
  // *labelled* action, which Google Ads reports against that action as "never
  // received a tag ping" for as long as the event below is missing.
  var ADS_PAGE_VIEW_LABEL = "-6EGCOnoq-ccEMeVosdE";
  var ADS_CONSENT_KEY = "mobaide-ads-consent";

  /* localStorage can throw on access (private windows, blocked site data), so
   * both accessors degrade to "no stored choice": the tag stays unloaded and
   * the banner simply asks again on the next visit.
   */
  function readAdsConsent() {
    try {
      return window.localStorage.getItem(ADS_CONSENT_KEY);
    } catch (err) {
      return null;
    }
  }

  function storeAdsConsent(value) {
    try {
      window.localStorage.setItem(ADS_CONSENT_KEY, value);
    } catch (err) {
      // The choice still governs this visit; only the memory of it is lost.
    }
  }

  /* First-party click attribution for the web app. Accepting the banner also
   * records the Google Ads click id the visitor arrived with (gclid, or the
   * iOS-era wbraid/gbraid) in a cookie on the registrable domain, so that
   * app.mobaide.com can attach it to the sign-up request and the server can
   * report the account's first agent pairing back to the ad click. Without a
   * click id the cookie is a bare consent marker (the server then falls back
   * to a hashed email). The cookie is issued and signed by the server, not
   * written from script: a domain cookie set by script proves nothing, since
   * any host on the registrable domain could plant one. This page only reads
   * it back (to avoid renewing it) and expires it on Decline.
   */
  var ADS_COOKIE_NAME = "mobaide_ads";
  var ADS_CLICK_ID_RE = /^[A-Za-z0-9_-]{1,200}$/;
  var ADS_CLICK_PARAMS = ["gclid", "wbraid", "gbraid"];
  // Stamped once per page load: the moment the click id arrived, not the
  // moment the visitor got around to pressing Accept in a tab left open.
  var LANDING_ARRIVED_AT = Math.floor(Date.now() / 1000);

  function readLandingClickId() {
    var params = new URLSearchParams(window.location.search);
    for (var i = 0; i < ADS_CLICK_PARAMS.length; i++) {
      var value = params.get(ADS_CLICK_PARAMS[i]);
      if (value && ADS_CLICK_ID_RE.test(value)) {
        return { kind: ADS_CLICK_PARAMS[i], id: value };
      }
    }
    return null;
  }

  function readAdsCookie() {
    var cookies;
    try {
      cookies = document.cookie.split(";");
    } catch (err) {
      return null;
    }
    for (var i = 0; i < cookies.length; i++) {
      var entry = cookies[i];
      var eq = entry.indexOf("=");
      if (eq === -1) {
        continue;
      }
      if (entry.slice(0, eq).replace(/^\s+/, "") === ADS_COOKIE_NAME) {
        return entry.slice(eq + 1);
      }
    }
    return null;
  }

  function requestAdsCookie() {
    var click = readLandingClickId();
    // Only a new click id rewrites an existing cookie. A plain return visit
    // must not renew the 90-day lifetime the policy discloses — neither for
    // a stored click id nor for a bare consent marker.
    if (!click && readAdsCookie() !== null) {
      return;
    }
    if (typeof window.fetch !== "function") {
      return;
    }
    try {
      window
        .fetch("/api/ads/consent", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            click_id: click ? click.id : null,
            click_id_kind: click ? click.kind : null,
            arrived_at: LANDING_ARRIVED_AT,
          }),
          credentials: "same-origin",
          keepalive: true,
        })
        .then(function () {
          // A Decline that landed while this was in flight must win: the
          // response's Set-Cookie would otherwise resurrect the cookie.
          if (readAdsConsent() !== "granted") {
            clearAdsCookie();
          }
        })
        .catch(function () {
          // Offline or blocked: the visit still counts on this page, only
          // the sign-up attribution is lost.
        });
    } catch (err) {
      // Same as above for a synchronous fetch failure.
    }
  }

  function clearAdsCookie() {
    document.cookie = ADS_COOKIE_NAME + "=; Max-Age=0; path=/";
    document.cookie =
      ADS_COOKIE_NAME + "=; Max-Age=0; path=/; domain=." + window.location.hostname;
  }

  var googleTagInjected = false;

  /* The official snippet's `function gtag(){dataLayer.push(arguments)}`,
   * shared so the withdrawal path can queue commands too. gtag.js consumes
   * Arguments objects specifically, so this must push `arguments` itself —
   * never an array literal.
   */
  function gtagPush() {
    window.dataLayer = window.dataLayer || [];
    window.dataLayer.push(arguments);
  }

  /* Injects gtag.js exactly once (Google's one-tag-per-page rule) and fires
   * the page-view conversion behind it. Runs only
   * after an explicit Accept — nothing here may execute pre-consent, which is
   * what keeps the EU/ePrivacy side of this honest. The Consent Mode v2
   * default-denied → update-granted pair is queued ahead of the script so the
   * tag boots already knowing consent state. Only the two measurement signals
   * are granted: the banner promises measurement, not remarketing, so
   * ad_personalization stays denied (analytics_storage too — this is an Ads
   * conversion tag, not an analytics rollout). Denying personalization is
   * also what keeps the tag off the per-country `www.google.<tld>`
   * remarketing endpoints, which the landing page's static CSP cannot
   * enumerate — granting it makes the browser log a CSP violation per visit
   * for most EU geographies.
   */
  function loadGoogleTag() {
    if (googleTagInjected) {
      return;
    }
    googleTagInjected = true;

    gtagPush("consent", "default", {
      ad_storage: "denied",
      ad_user_data: "denied",
      ad_personalization: "denied",
      analytics_storage: "denied",
    });
    gtagPush("consent", "update", {
      ad_storage: "granted",
      ad_user_data: "granted",
    });
    gtagPush("js", new Date());
    gtagPush("config", ADS_TAG_ID);
    // Queued after `config`, which gtag requires to have run before it can
    // resolve `send_to`. Value and currency restate the conversion action's
    // own setting in Google Ads; change them in both places or not at all.
    gtagPush("event", "conversion", {
      send_to: ADS_TAG_ID + "/" + ADS_PAGE_VIEW_LABEL,
      value: 1.0,
      currency: "CZK",
    });

    var script = document.createElement("script");
    script.async = true;
    script.src = "https://www.googletagmanager.com/gtag/js?id=" + ADS_TAG_ID;
    document.head.appendChild(script);
  }

  /* GDPR Art. 7(3): withdrawing consent must be as easy as giving it, so
   * Decline is also the withdrawal path. A tag already running this page is
   * told to stop via the consent API, and Google's `_gcl_*` cookies are
   * expired outright — gtag writes them from script on this origin, so script
   * can remove them. Both domain spellings are expired because gtag registers
   * them on the registrable domain, not the host. Our own attribution cookie
   * goes with them.
   */
  function withdrawGoogleTag() {
    if (googleTagInjected) {
      gtagPush("consent", "update", {
        ad_storage: "denied",
        ad_user_data: "denied",
      });
    }
    // Tell the server first, while the cookie can still be read: an account
    // created with it must lose the stored attribution too, not only the
    // browser its copy. Best effort — the local deletion below never waits.
    var existing = readAdsCookie();
    if (existing !== null && typeof window.fetch === "function") {
      try {
        window
          .fetch("/api/ads/consent/withdraw", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ value: existing }),
            credentials: "same-origin",
            keepalive: true,
          })
          .catch(function () {});
      } catch (err) {
        // Same as above: withdrawal is still recorded on this page.
      }
    }
    var cookies = document.cookie.split(";");
    for (var i = 0; i < cookies.length; i++) {
      var name = cookies[i].split("=")[0].replace(/^\s+/, "");
      if (name.indexOf("_gcl") === 0) {
        document.cookie = name + "=; Max-Age=0; path=/";
        document.cookie =
          name + "=; Max-Age=0; path=/; domain=." + window.location.hostname;
      }
    }
    clearAdsCookie();
  }

  /* The banner ships with the `hidden` attribute and is revealed only when no
   * choice is stored — so a no-JS visitor, who can never load the tag, is
   * never asked, and a returning visitor is not asked twice — or when the
   * visitor reopens it from the footer's "Cookie settings" control, which is
   * what makes a stored choice reversible on the site itself.
   */
  function setUpConsentBanner() {
    // The static assets ship identically to every environment, but the Ads
    // property is production-only: an Accept on localhost or a staging deploy
    // would feed that environment's page and click signals into live tag
    // diagnostics and campaign data. Gating the whole flow here — not just
    // the injection — also spares those visitors a banner whose consent
    // would gate nothing. For local verification, launch Chrome with
    // --host-resolver-rules="MAP mobaide.com 127.0.0.1" and browse
    // http://mobaide.com:<port>/ — location.hostname never carries the port.
    if (window.location.hostname !== "mobaide.com") {
      return;
    }
    var banner = document.querySelector("[data-consent-banner]");
    if (!banner) {
      return;
    }

    var accept = banner.querySelector("[data-consent-accept]");
    var decline = banner.querySelector("[data-consent-decline]");
    if (!accept || !decline) {
      return;
    }

    // Bound before the stored-choice branch: a visitor who already chose can
    // reopen the banner from the footer and choose differently.
    accept.addEventListener("click", function () {
      storeAdsConsent("granted");
      banner.hidden = true;
      loadGoogleTag();
      requestAdsCookie();
    });
    decline.addEventListener("click", function () {
      storeAdsConsent("denied");
      banner.hidden = true;
      withdrawGoogleTag();
    });

    var settings = document.querySelector("[data-consent-settings]");
    if (settings) {
      settings.addEventListener("click", function () {
        banner.hidden = false;
      });
      // Only now, with the control wired, does the stylesheet reveal it —
      // same contract as the copy buttons' `can-copy`.
      document.documentElement.classList.add("has-consent-ui");
    }

    var choice = readAdsConsent();
    if (choice === "granted") {
      loadGoogleTag();
      requestAdsCookie();
      return;
    }
    if (choice === "denied") {
      return;
    }
    banner.hidden = false;
  }

  function init() {
    // Ahead of the reduced-motion return below: copying is not motion, and a
    // visitor who asked for less of it still needs the button to work.
    setUpCopyButtons();
    // Likewise not motion — and consent must not depend on animation settings.
    setUpConsentBanner();
    // Also not motion: a reduced-motion visitor still picks their platform.
    setUpShotSwitch();

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
