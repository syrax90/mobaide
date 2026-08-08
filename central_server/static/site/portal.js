/* Mobaide portal behaviour — every script the portal templates used to ship
 * inline, plus the modal dismissals they used to ship as `onclick=`.
 *
 * It all lives here because `/portal/*` is served under a CSP whose `script-src`
 * has no `'unsafe-inline'`: an inline block or an `onclick` attribute is not
 * "discouraged" on these pages, it silently does not run. Per-request values
 * that used to be interpolated into JS travel as `data-` attributes instead.
 *
 * Loaded by portal_base.html AFTER htmx, so `window.htmx` exists here. Every
 * per-element initialiser is registered through `htmx.onLoad`, which fires both
 * on the initial page and on each swapped-in fragment — that is what wires the
 * delete-account modal, which arrives as an htmx response into `#modal-root`.
 * Initialisers are idempotent (guarded by a `data-*-ready` marker) because
 * `htmx:load` can fire more than once over the same subtree.
 *
 * The two document-level delegated listeners at the bottom are registered once
 * at load, NOT inside htmx.onLoad, which would add a duplicate per swap.
 *
 * `closeModal()` and the [data-modal-close] / overlay-self delegation are
 * duplicated in static/dashboard.js on purpose: the two bundles never load on
 * the same page, there is no bundler here, and a shared file would put this
 * one's social sign-in machinery on the admin dashboard. Change one, change the
 * other.
 */
(function () {
  "use strict";

  var MODAL_ROOT_ID = "modal-root";

  /* htmx:load hands us the root of the new content, and that root can itself be
     the element we are looking for — `querySelectorAll` alone would skip it. */
  function findAll(root, selector) {
    var found = [];
    if (root.nodeType === 1 && root.matches(selector)) {
      found.push(root);
    }
    if (root.querySelectorAll) {
      Array.prototype.push.apply(found, root.querySelectorAll(selector));
    }
    return found;
  }

  function claim(el, marker) {
    if (el.dataset[marker]) return false;
    el.dataset[marker] = "1";
    return true;
  }

  function closeModal() {
    var root = document.getElementById(MODAL_ROOT_ID);
    if (root) root.innerHTML = "";
  }

  /* ---------- social sign-in SDK poll budget ---------- */

  /* Both provider SDKs are loaded `async defer`, so neither is usually ready
     when a page or a swapped-in fragment initialises. These constants are
     provider-neutral for the same reason the sign-in nonce is: one budget now
     serves Google and Apple alike.

     `isConnected` in each poll is the leak guard — it stops the timer the
     moment the element leaves the document, which covers dismissal, backdrop
     click, an htmx re-swap and navigation alike. The attempt budget is only a
     slow-network backstop, and it is generous because the clock starts before
     the deferred script has necessarily begun downloading: running out means
     the button never arms at all on /portal/login and /portal/signup.

     The interval ramps rather than staying at 50 ms for the whole minute. The
     overwhelmingly common case is the SDK being a beat behind, which the first
     two seconds cover with no perceptible delay; past that we are waiting on a
     slow link or a blocked request, where a 250 ms check is just as timely and
     costs a quarter of the ticks. Same ~60s ceiling either way. */
  var SDK_FAST_POLL_MS = 50;
  var SDK_SLOW_POLL_MS = 250;
  var SDK_FAST_ATTEMPTS = 40;
  var SDK_MAX_ATTEMPTS = 272;

  /* ---------- Google Identity Services ---------- */

  function mountGisButton(el, options) {
    var clientId = el.dataset.gisClientId;
    if (!clientId) return;
    var attempts = 0;

    function attempt() {
      if (!el.isConnected) return;
      if (!window.google || !window.google.accounts) {
        if (attempts < SDK_MAX_ATTEMPTS) {
          var delay =
            attempts < SDK_FAST_ATTEMPTS ? SDK_FAST_POLL_MS : SDK_SLOW_POLL_MS;
          attempts++;
          window.setTimeout(attempt, delay);
        }
        return;
      }
      window.google.accounts.id.initialize({
        client_id: clientId,
        nonce: el.dataset.gisNonce || "",
        ux_mode: "popup",
        callback: options.onCredential,
      });
      window.google.accounts.id.renderButton(el, {
        type: "standard",
        /* filled_black, not outline: the portal is a #121212 surface, on which
           outline renders a white slab. It is one of Google's own sanctioned
           themes, so the branding rules still hold. The small corner radius
           Google applies inside the cross-origin iframe is unreachable from our
           CSS — 'rectangular' is as square as it goes.

           `width` makes GIS emit a style= attribute on the button it renders.
           That is covered: the portal CSP concedes style-src 'unsafe-inline'
           whenever Google is configured, because the GIS client also appends an
           inline <style> to our <head> the moment it loads. */
        theme: "filled_black",
        size: "large",
        shape: "rectangular",
        logo_alignment: "left",
        text: el.dataset.gisText || "continue_with",
        width: 320,
      });
    }

    attempt();
  }

  /* The login and signup buttons: drop the credential into their own form and
     submit it natively. _portal_google_button.html renders both. */
  function initStandaloneGisButtons(root) {
    findAll(root, "[data-gis-button]").forEach(function (el) {
      if (!claim(el, "gisReady")) return;
      var form = document.getElementById(el.dataset.gisForm || "");
      if (!form) return;
      mountGisButton(el, {
        onCredential: function (response) {
          form.querySelector('input[name="credential"]').value =
            response.credential;
          form.submit();
        },
      });
    });
  }

  /* ---------- Sign in with Apple ---------- */

  /* Apple's SDK is loaded `async defer` like the GIS one, so the same
     ready-poll applies. It is a different shape of API though: rather than
     rendering a button, it exposes `AppleID.auth.signIn()`, which we drive
     from our own <button>. That button ships `disabled` and is enabled only
     once the SDK answers — a click before then would have nothing to call.

     `AppleID.auth.init` is global and idempotent per config, so it runs once
     per element and the per-attempt nonce rides on the `signIn()` argument
     instead (the SDK copies it onto a throwaway config object, leaving the
     global one alone).

     `redirectURI` is mandatory even in popup mode, where the browser never
     navigates to it: Apple only validates it against the Return URLs
     registered for the Services ID. Deriving it from the current origin is
     what lets one Services ID serve every host we deploy to. */
  var APPLE_REDIRECT_PATH = "/portal/login";

  function appleAuthApi() {
    return (
      window.AppleID && window.AppleID.auth && window.AppleID.auth.signIn
        ? window.AppleID.auth
        : null
    );
  }

  function mountAppleButton(el, options) {
    var clientId = el.dataset.appleClientId;
    if (!clientId) return;
    var attempts = 0;

    function attempt() {
      if (!el.isConnected) return;
      var auth = appleAuthApi();
      if (!auth) {
        if (attempts < SDK_MAX_ATTEMPTS) {
          var delay =
            attempts < SDK_FAST_ATTEMPTS ? SDK_FAST_POLL_MS : SDK_SLOW_POLL_MS;
          attempts++;
          window.setTimeout(attempt, delay);
        }
        return;
      }
      /* `init` throws on a config it rejects, and both its inputs are
         deployment-supplied. The initialisers share one htmx.onLoad callback,
         so an uncaught throw here would skip whatever runs after it in the
         same pass. Degrade to a button that never arms instead. */
      try {
        auth.init({
          clientId: clientId,
          scope: "email",
          redirectURI: window.location.origin + APPLE_REDIRECT_PATH,
          usePopup: true,
        });
      } catch (err) {
        return;
      }
      el.addEventListener("click", function () {
        /* Nothing is awaited before signIn(): the popup has to open inside
           the click's user-activation window or the browser blocks it. */
        if (el.disabled) return;
        el.disabled = true;
        auth
          .signIn({ nonce: el.dataset.appleNonce || "" })
          .then(function (data) {
            var token =
              data && data.authorization && data.authorization.id_token;
            if (token) options.onCredential(token);
          })
          .catch(function () {
            /* Cancelling, closing the popup, or letting the browser block it
               all reject here. None is an error worth interrupting the page
               for — the button re-arms for another try. */
          })
          .then(function () {
            /* Reached on both paths because the .catch above swallows every
               rejection. onReady re-applies the owner's own gate rather than
               blindly enabling — the delete modal's also depends on the typed
               email. */
            options.onReady();
          });
      });
      options.onReady();
    }

    attempt();
  }

  /* Login/signup: same hidden-form contract as the Google button. */
  function initStandaloneAppleButtons(root) {
    findAll(root, "[data-apple-button]").forEach(function (el) {
      if (!claim(el, "appleReady")) return;
      var form = document.getElementById(el.dataset.appleForm || "");
      if (!form) return;
      mountAppleButton(el, {
        onReady: function () {
          el.disabled = false;
        },
        onCredential: function (idToken) {
          form.querySelector('input[name="credential"]').value = idToken;
          form.submit();
        },
      });
    });
  }

  /* ---------- password reveal ---------- */

  /* Which of the two SVG icons is visible follows from aria-pressed, via the
     rules in portal.css — so this only has to keep the accessible state
     correct, and the icons cannot end up disagreeing with it. */
  function toggleReveal(btn) {
    var input = document.getElementById(btn.getAttribute("data-target") || "");
    if (!input) return;
    var revealed = input.type === "text";
    input.type = revealed ? "password" : "text";
    btn.setAttribute("aria-pressed", revealed ? "false" : "true");
    btn.setAttribute("aria-label", revealed ? "Show password" : "Hide password");
  }

  /* ---------- password + confirm match indicator ---------- */

  /* Server-side validation in portal/router.py is the source of truth; this is
     UX only. The submit button ships enabled and is disabled here, so a failure
     to load this file leaves the form usable rather than permanently locked. */
  function initPasswordConfirm(root) {
    findAll(root, "[data-password-confirm]").forEach(function (indicator) {
      if (!claim(indicator, "passwordConfirmReady")) return;
      var pw = document.getElementById(indicator.dataset.passwordInput || "");
      var confirm = document.getElementById(indicator.dataset.confirmInput || "");
      var submitBtn = document.getElementById(
        indicator.dataset.submitButton || ""
      );
      if (!pw || !confirm || !submitBtn) return;

      submitBtn.disabled = true;

      function refresh() {
        var a = pw.value;
        var b = confirm.value;
        if (b.length === 0) {
          indicator.textContent = "";
          indicator.classList.remove("ok", "bad");
          submitBtn.disabled = true;
          return;
        }
        if (a === b) {
          indicator.textContent = "Passwords match";
          indicator.classList.add("ok");
          indicator.classList.remove("bad");
          submitBtn.disabled = a.length < 8;
        } else {
          indicator.textContent = "Passwords don't match";
          indicator.classList.add("bad");
          indicator.classList.remove("ok");
          submitBtn.disabled = true;
        }
      }

      pw.addEventListener("input", refresh);
      confirm.addEventListener("input", refresh);
    });
  }

  /* ---------- delete-account modal ---------- */

  /* Arrives as an htmx fragment into #modal-root, so this runs from htmx.onLoad
     rather than at page load. Which re-auth methods exist is read off the
     rendered panels instead of being duplicated as Jinja booleans — the panels
     are the same condition, and they cannot disagree with themselves. */
  function initDeleteAccountForm(root) {
    findAll(root, "[data-delete-account-form]").forEach(function (form) {
      if (!claim(form, "deleteAccountReady")) return;

      var emailInput = form.querySelector("#confirm-email");
      var pwBtn = form.querySelector("#confirm-delete-btn");
      var pwPanel = form.querySelector("#reauth-password-panel");
      var gPanel = form.querySelector("#reauth-google-panel");
      var gWrapper = form.querySelector("#confirm-delete-google-wrapper");
      var gBtnContainer = form.querySelector("#confirm-delete-google-btn");
      var gHint = form.querySelector("#confirm-delete-google-hint");
      var aPanel = form.querySelector("#reauth-apple-panel");
      var aBtn = form.querySelector("#confirm-delete-apple-btn");
      var aHint = form.querySelector("#confirm-delete-apple-hint");
      var graceDays = form.dataset.graceDays || "";
      /* Apple's button is ours, so it carries a real `disabled` — which two
         independent conditions gate: the SDK answering, and the typed email
         matching. Neither may clear the other's block. */
      var appleSdkReady = false;

      var methods = [];
      if (pwPanel) methods.push("password");
      if (gPanel) methods.push("google");
      if (aPanel) methods.push("apple");

      function selectedMethod() {
        if (methods.length < 2) return methods[0] || "password";
        var checked = form.querySelector('input[name="method"]:checked');
        return checked ? checked.value : methods[0];
      }
      function selectedMode() {
        var checked = form.querySelector('input[name="mode"]:checked');
        return checked ? checked.value : "scheduled";
      }
      function emailMatches() {
        return (
          emailInput !== null && emailInput.value === emailInput.dataset.expected
        );
      }
      function refreshPwLabel() {
        if (!pwBtn) return;
        var label = pwBtn.getAttribute("data-label-" + selectedMode());
        if (label) pwBtn.textContent = label;
      }
      function refreshHint(hint, provider) {
        if (!hint) return;
        if (selectedMode() === "immediate") {
          hint.textContent =
            "Click below to verify with " +
            provider +
            " to delete your account immediately. This cannot be undone.";
          hint.classList.add("danger");
        } else {
          hint.textContent =
            "Click below to verify with " +
            provider +
            " to schedule deletion in " +
            graceDays +
            " days. You can cancel any time before then.";
          hint.classList.remove("danger");
        }
      }
      function refreshHints() {
        refreshHint(gHint, "Google");
        refreshHint(aHint, "Apple");
      }
      function refreshVisibility() {
        var method = selectedMethod();
        var usePw = method === "password" && pwPanel !== null;
        if (pwPanel) pwPanel.style.display = usePw ? "" : "none";
        if (pwBtn) pwBtn.style.display = usePw ? "" : "none";
        if (gPanel) gPanel.style.display = method === "google" ? "" : "none";
        if (aPanel) aPanel.style.display = method === "apple" ? "" : "none";
      }
      function refreshSubmitState() {
        var match = emailMatches();
        if (pwBtn) pwBtn.disabled = !match;
        if (gWrapper) {
          gWrapper.setAttribute("aria-disabled", match ? "false" : "true");
        }
        if (aBtn) aBtn.disabled = !match || !appleSdkReady;
      }

      /* Adopt a social credential: the token only counts once the typed email
         matches, and the chooser has to agree with the button that produced
         it or the server would re-auth against the wrong provider. */
      function submitWithCredential(method, idToken) {
        if (!emailMatches()) return;
        form.querySelector('input[name="credential"]').value = idToken;
        if (methods.length > 1) {
          var radio = form.querySelector(
            'input[name="method"][value="' + method + '"]'
          );
          if (radio) radio.checked = true;
        }
        /* htmx.trigger, not form.submit(): the form posts through
           hx-post and swaps the response back into #modal-root, so a
           native submit would navigate away from the page. */
        window.htmx.trigger(form, "submit");
      }

      if (emailInput) emailInput.addEventListener("input", refreshSubmitState);
      form.querySelectorAll('input[name="mode"]').forEach(function (radio) {
        radio.addEventListener("change", function () {
          refreshPwLabel();
          refreshHints();
        });
      });
      form.querySelectorAll('input[name="method"]').forEach(function (radio) {
        radio.addEventListener("change", refreshVisibility);
      });
      refreshPwLabel();
      refreshHints();
      refreshVisibility();
      refreshSubmitState();

      if (gBtnContainer && claim(gBtnContainer, "gisReady")) {
        mountGisButton(gBtnContainer, {
          onCredential: function (response) {
            submitWithCredential("google", response.credential);
          },
        });
      }

      if (aBtn && claim(aBtn, "appleReady")) {
        mountAppleButton(aBtn, {
          onReady: function () {
            appleSdkReady = true;
            refreshSubmitState();
          },
          onCredential: function (idToken) {
            submitWithCredential("apple", idToken);
          },
        });
      }
    });
  }

  /* ---------- wiring ---------- */

  /* Order is defensive, not arbitrary: these run in one htmx.onLoad callback,
     so a throw in any of them skips the rest, and the two SDK initialisers are
     the only ones that touch deployment-supplied third-party config — they go
     last so nothing else rides on them. That ordering also protects the
     controls with no graceful degradation: the password-match indicator
     disables a button that ships enabled (see initPasswordConfirm), whereas
     both Apple buttons ship `disabled` and are dead if their initialiser never
     runs. */
  function initAll(root) {
    initPasswordConfirm(root);
    initDeleteAccountForm(root);
    initStandaloneGisButtons(root);
    initStandaloneAppleButtons(root);
  }

  document.addEventListener("click", function (event) {
    var target = event.target;
    if (!target || typeof target.closest !== "function") return;

    var revealBtn = target.closest(".reveal-btn");
    if (revealBtn) {
      toggleReveal(revealBtn);
      return;
    }
    if (target.closest("[data-modal-close]")) {
      closeModal();
      return;
    }
    /* Backdrop dismissal: only when the overlay itself was clicked, never a
       click that bubbled up out of the card. */
    var overlay = target.closest(".modal-overlay");
    if (overlay && target === overlay) closeModal();
  });

  if (window.htmx && typeof window.htmx.onLoad === "function") {
    window.htmx.onLoad(initAll);
  } else if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", function () {
      initAll(document.body);
    });
  } else {
    initAll(document.body);
  }
})();
