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
 * one's Google Sign-In machinery on the admin dashboard. Change one, change the
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

  /* ---------- Google Identity Services ---------- */

  /* The GIS client is loaded `async defer`, so it is usually not ready when a
     page or a swapped-in fragment initialises.

     `isConnected` below is the leak guard — it stops the timer the moment the
     container leaves the document, which covers dismissal, backdrop click, an
     htmx re-swap and navigation alike. The attempt budget is only a slow-network
     backstop, and it is generous because the clock starts before the deferred
     GIS script has necessarily begun downloading: running out means the Google
     button never renders at all on /portal/login and /portal/signup.

     The interval ramps rather than staying at 50 ms for the whole minute. The
     overwhelmingly common case is GIS being a beat behind, which the first two
     seconds cover with no perceptible delay; past that we are waiting on a slow
     link or a blocked request, where a 250 ms check is just as timely and costs
     a quarter of the ticks. Same ~60s ceiling either way. */
  var GIS_FAST_POLL_MS = 50;
  var GIS_SLOW_POLL_MS = 250;
  var GIS_FAST_ATTEMPTS = 40;
  var GIS_MAX_ATTEMPTS = 272;

  function mountGisButton(el, options) {
    var clientId = el.dataset.gisClientId;
    if (!clientId) return;
    var attempts = 0;

    function attempt() {
      if (!el.isConnected) return;
      if (!window.google || !window.google.accounts) {
        if (attempts < GIS_MAX_ATTEMPTS) {
          var delay =
            attempts < GIS_FAST_ATTEMPTS ? GIS_FAST_POLL_MS : GIS_SLOW_POLL_MS;
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
     rather than at page load. `hasPassword`/`hasGoogle` are read off the
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
      var hasPassword = pwPanel !== null;
      var hasGoogle = gPanel !== null;
      var hasBoth = hasPassword && hasGoogle;
      var graceDays = form.dataset.graceDays || "";

      function selectedMethod() {
        if (!hasBoth) return hasPassword ? "password" : "google";
        var checked = form.querySelector('input[name="method"]:checked');
        return checked ? checked.value : "password";
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
      function refreshGoogleHint() {
        if (!gHint) return;
        if (selectedMode() === "immediate") {
          gHint.textContent =
            "Click below to verify with Google to delete your account " +
            "immediately. This cannot be undone.";
          gHint.classList.add("danger");
        } else {
          gHint.textContent =
            "Click below to verify with Google to schedule deletion in " +
            graceDays +
            " days. You can cancel any time before then.";
          gHint.classList.remove("danger");
        }
      }
      function refreshVisibility() {
        var method = selectedMethod();
        var usePw = method === "password" && hasPassword;
        var useG = method === "google" && hasGoogle;
        if (pwPanel) pwPanel.style.display = usePw ? "" : "none";
        if (pwBtn) pwBtn.style.display = usePw ? "" : "none";
        if (gPanel) gPanel.style.display = useG ? "" : "none";
      }
      function refreshSubmitState() {
        var match = emailMatches();
        if (pwBtn) pwBtn.disabled = !match;
        if (gWrapper) {
          gWrapper.setAttribute("aria-disabled", match ? "false" : "true");
        }
      }

      if (emailInput) emailInput.addEventListener("input", refreshSubmitState);
      form.querySelectorAll('input[name="mode"]').forEach(function (radio) {
        radio.addEventListener("change", function () {
          refreshPwLabel();
          refreshGoogleHint();
        });
      });
      form.querySelectorAll('input[name="method"]').forEach(function (radio) {
        radio.addEventListener("change", refreshVisibility);
      });
      refreshPwLabel();
      refreshGoogleHint();
      refreshVisibility();
      refreshSubmitState();

      if (gBtnContainer && claim(gBtnContainer, "gisReady")) {
        mountGisButton(gBtnContainer, {
          onCredential: function (response) {
            if (!emailMatches()) return;
            form.querySelector('input[name="credential"]').value =
              response.credential;
            if (hasBoth) {
              var googleRadio = form.querySelector(
                'input[name="method"][value="google"]'
              );
              if (googleRadio) googleRadio.checked = true;
            }
            /* htmx.trigger, not form.submit(): the form posts through
               hx-post and swaps the response back into #modal-root, so a
               native submit would navigate away from the page. */
            window.htmx.trigger(form, "submit");
          },
        });
      }
    });
  }

  /* ---------- wiring ---------- */

  function initAll(root) {
    initStandaloneGisButtons(root);
    initPasswordConfirm(root);
    initDeleteAccountForm(root);
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
