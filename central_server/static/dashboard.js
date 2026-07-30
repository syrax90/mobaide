/* Admin dashboard behaviour — the script templates/users.html used to ship
 * inline, the modal dismissals its partials shipped as `onclick=`, and the
 * confirm-email gate _delete_user_modal.html shipped as `hx-on:input`.
 *
 * `/dashboard/*` is served under the strictest policy in the app —
 * `script-src 'self'`, `style-src 'self'`, no 'unsafe-inline' and no
 * 'unsafe-eval' — so none of those three forms executes any more. `hx-on:` in
 * particular is compiled by htmx with `new Function()`, which a nonce cannot
 * authorise; only 'unsafe-eval' would, and that is not worth one input handler.
 *
 * The portal has its own copy of the dismissal delegation in
 * static/site/portal.js. They are not shared: the two surfaces load disjoint
 * stylesheets and never appear on the same page, and a shared bundle would put
 * the portal's Google Sign-In machinery on the admin dashboard.
 *
 * Loaded by templates/base.html after htmx, so `window.htmx` is available.
 */
(function () {
  "use strict";

  var MODAL_ROOT_ID = "modal-root";

  function closeModal() {
    var root = document.getElementById(MODAL_ROOT_ID);
    if (root) root.innerHTML = "";
  }

  /* Toggle a user row's inline detail expansion. If the detail row exists,
     remove it (collapse); otherwise fetch and insert it via htmx.ajax with
     swap 'afterend' so htmx still processes the response normally. Written in
     JS rather than a row hx-get because 'afterend' has no toggle semantics —
     repeated clicks would pile duplicate detail rows into the DOM. */
  function toggleUserRow(userId) {
    var row = document.getElementById("user-row-" + userId);
    if (!row) return;
    var detail = document.getElementById("user-detail-" + userId);
    if (detail) {
      detail.remove();
      row.setAttribute("aria-expanded", "false");
      return;
    }
    row.setAttribute("aria-expanded", "true");
    window.htmx.ajax("GET", "/dashboard/users/" + userId + "/agents", {
      target: "#user-row-" + userId,
      swap: "afterend",
    });
  }

  document.addEventListener("click", function (event) {
    var target = event.target;
    if (!target || typeof target.closest !== "function") return;

    if (target.closest("[data-modal-close]")) {
      closeModal();
      return;
    }
    /* Backdrop dismissal: only when the overlay itself was clicked, never a
       click that bubbled up out of the card. */
    var overlay = target.closest(".modal-overlay");
    if (overlay && target === overlay) {
      closeModal();
      return;
    }
    /* The actions cell carries data-no-row-toggle so its buttons do not also
       expand the row — the delegated equivalent of the old
       onclick="event.stopPropagation()". */
    if (target.closest("[data-no-row-toggle]")) return;
    var row = target.closest("[data-user-id]");
    if (row) toggleUserRow(row.dataset.userId);
  });

  /* Gate a destructive submit button on the user typing the expected value.
     Server-side validation is the source of truth; this is the same
     type-to-confirm guard the field always had. */
  document.addEventListener("input", function (event) {
    var target = event.target;
    if (!target || typeof target.closest !== "function") return;
    var field = target.closest("[data-confirm-target]");
    if (!field) return;
    var button = document.getElementById(field.dataset.confirmTarget);
    if (button) button.disabled = field.value !== field.dataset.expected;
  });
})();
