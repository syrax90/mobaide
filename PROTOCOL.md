# Wire-protocol versions

The Mobaide system uses a single **integer** ``protocol_version`` as the
authoritative compatibility signal between the Android app, ``mobaide_agent``,
and ``central_server``. The three human-facing semvers (``android-vX.Y.Z``,
``agent-vX.Y.Z``, ``server-vX.Y.Z``) are independent and ship on their own
cadence; they do **not** gate compatibility.

## proto-v7 (current)

* Introduced: 2026-07-27 (CLA-153).
* Python support: ``PROTOCOL_VERSION = 7``, ``SUPPORTED_PROTOCOL_VERSIONS =
  (6, 7)``, ``MIN_CLIENT_PROTOCOL_VERSION = 6`` (both ``mobaide_agent`` and
  ``central_server``). Android advanced to ``7`` with its client half
  (CLA-154) and iOS with its half (CLA-155) — ``APP_PROTOCOL_VERSION = 7``
  on both; old mobile builds remain accepted meanwhile.
* **Deployment order matters**: a proto-v7 agent handshaking against a
  proto-v6-only relay is closed with ``4002``, so the central server must
  deploy before any agent rollout. Old (v6) agents keep connecting — the
  relay supports both versions — but cannot launch custom agent types
  (see the ``409`` gate below).
* Change vs proto-v6: account-scoped **custom AI agent types** — a name +
  launch command registry on the central server, launched as plain
  terminal sessions with an **open session kind**. The version is bumped
  (rather than riding the additive no-bump precedent) because the kind
  vocabulary stops being a closed enum: clients must **preserve unknown
  ``kind`` strings verbatim** instead of coercing them to ``claude``, and
  a detectable version boundary is needed so the relay can refuse custom
  launches against old agents that would silently ignore the new fields
  and open a bare shell.
* **Open session kind**: ``kind`` (in ``session_to_wire_dict`` and every
  session list/event envelope) may now be the raw string
  ``custom_<12hex>`` (pattern ``^custom_[0-9a-f]{12}$``; the 12-hex id is
  the registry row id). Built-in kinds are unchanged. The session wire
  dict also gains nullable ``kind_label`` — a snapshot of the type's
  display name taken at session creation, so lists render correctly after
  the type is renamed or deleted (null for built-in kinds).
* **Session create** (``POST /api/sessions`` proxied over ``CH_CONTROL``,
  and the equivalent ``create_session`` control message): the **existing
  ``cli`` discriminator** carries the custom kind — ``cli:
  "custom_<12hex>"``. Clients never send the command; the client-supplied
  ``command``/``env`` hints stay discarded exactly as on the Terminal
  path.
  * The relay **strips** ``resolved_command`` / ``resolved_name`` from
    every client body on this route, then — for a valid custom ``cli`` —
    looks the id up in the caller's registry and **injects** the stored
    command + display name into the forwarded body. These two fields are
    therefore server-only: on the agent side a non-null value is always
    relay-authored (mobile clients have no path that reaches the agent's
    control channel with arbitrary JSON).
  * Relay-side failures answer without forwarding: unknown/deleted id →
    ``404``; malformed ``custom_*`` id → ``400``; **live tunnel's**
    negotiated ``protocol_version`` ``< 7`` (or unset) → ``409`` with body
    ``{"agent_update_required": true, "detail": ...}``. Agent **offline** →
    the standard proxy ``503`` (the version gate applies only to a live
    tunnel — the relay never answers ``agent_update_required`` from a
    persisted handshake row, which overlapping tunnels can leave
    describing a different connection).
  * Agent-side: a custom ``cli`` without ``resolved_command`` (agent-direct
    REST, or a pre-v7 relay) fails cleanly — ``400`` on the REST/proxy
    paths, ``session_create_failed`` on the control path — never a silent
    bare-shell downgrade. The agent-direct REST API always rejects custom
    kinds (``400``): it has no registry access.
  * Spawn contract: POSIX ``[login_shell, "-l", "-c", command]`` with the
    stored command as **one argv element** (no interpolation, no quoting
    layer); Windows ``["cmd.exe", "/c", command]``. The login shell
    resolves ``$SHELL`` → ``pw_shell`` → ``/bin/sh``, falling back to
    ``/bin/sh`` when the basename isn't a known POSIX shell (bash / zsh /
    sh / dash / ksh). Plain Terminal sessions now use the same resolver
    (previously hardcoded ``/bin/bash``). Custom sessions are
    plain-terminal semantics end to end: the ``worktree`` /
    ``branch_name`` toggle works unchanged, and there is no managed-CLI
    machinery (no options block, no attention hooks, no autoscan).
* **REST registry** (dashboard/user JWT, non-admin, on the central
  server): ``GET /api/custom-agent-types`` → list;
  ``POST /api/custom-agent-types`` ``{"name", "command"}`` → ``201``;
  ``PATCH /api/custom-agent-types/{id}`` ``{"name"? , "command"?}`` (at
  least one field; provided-but-blank → ``422``; there is **no**
  empty-string-clears form — both attributes are mandatory);
  ``DELETE /api/custom-agent-types/{id}`` → ``204`` (soft-delete).
  Validation: ``name`` 1–60 chars trimmed, unique per account
  case-insensitively (ASCII case folding only — SQLite ``NOCASE``;
  non-ASCII characters compare byte-wise, so ``Ågent`` and ``ågent`` are
  distinct names); ``command`` 1–1000 chars trimmed; an embedded NUL
  byte in either field → ``422``. The per-account
  cap is **20** live types, enforced atomically → ``409``; a name
  conflict → ``409`` (distinct ``detail`` strings); ownership violations
  and repeat deletes → ``403`` (same non-idempotent-DELETE caveat as
  shortcuts). The row shape is ``{"id", "name", "command", "created_at",
  "updated_at"}`` — ``command`` is returned to its owner (the editor
  needs it); only session-create requests never carry it.
* **New user-event kinds** (server-originated, same envelope as
  ``template.*`` / ``shortcut.*``): ``custom_type.created`` /
  ``custom_type.updated`` (full row) and ``custom_type.deleted``
  ``{"id"}``. Deleting a type also soft-deletes the owner's saved
  shortcuts whose ``kind`` equals ``custom_<id>`` **in the same
  transaction, with only the one ``custom_type.deleted`` event** — no
  companion ``shortcut.deleted`` events; Android and iOS purge their
  local shortcut caches for that kind on receipt (the web client removed
  its shortcuts UI and ignores the cascade). Connect-time reconcile is the
  usual REST refresh (``GET /api/custom-agent-types``). Per-type shortcut
  scoping needs no server change: ``custom_<12hex>`` already satisfies
  the shortcuts ``KIND_PATTERN`` and the server stores kinds opaquely.
* **First-pairing account event** (additive, **no version bump** — the
  CLA-106 precedent; neither ``envelope.py`` copy changes). New user-event
  kind ``account.first_agent_paired`` ``{"agent_id"}``, broadcast once per
  account from ``_complete_pairing`` when the ``users.first_paired_at``
  compare-and-set wins, and a new REST route ``GET /api/auth/me`` →
  ``{"user_id", "email", "role", "created_at", "scheduled_deletion_at"?,
  "first_paired_at"?}`` (dashboard/user JWT, any role). Together they let
  the mobile apps log their Firebase ``agent_paired`` conversion exactly
  once per account — live off the event, or on the next user-events
  connect off the ``/me`` stamp when the app was closed at pairing time
  (see docs/app-conversion-measurement.md). Older clients ignore the kind
  (``else -> Unit`` / ``default: .none`` / ``{kind: 'ignored'}``); the web
  client mirrors both shapes and invalidates its agent list on the event.
* Protocol-fixture updates under ``tests/protocol_fixtures/`` (dropping
  the ``unknown_coerces_to_claude`` cases, adding ``custom_*`` round-trip
  cases and the ``CustomAgentTypeDto`` fixtures) rode with the iOS client
  half (CLA-155), which was the fixtures' only consumer at the time; its
  registry gate would have broken the iOS build had they changed before
  the ``.custom`` kind existed there. **Since CLA-191 (WEB-04) the web
  client is a second consumer**: ``web/src/protocol/`` reads the same JSON
  off disk under Vitest, and ``protocol-integrity`` runs that suite on
  every PR. A fixture edit must therefore satisfy both arms — and note
  that Swift's ``Codable`` drops unknown keys on re-encode while the web
  client's ``z.looseObject`` preserves them, so an unknown-extra-key case
  added to a shared fixture would fail iOS. Web coverage is deliberately
  partial and declared in ``web/src/protocol/__fixtures__/coverage.ts``,
  which fails if a new fixture file is neither asserted nor assigned an
  owner.
* Additive delta within proto-v7 (2026-09-21, in-session conflict resolver —
  read a conflicted file, write its resolution, take one side, rebuild the
  conflict): **four additive REST routes, one additive status field, one
  additive capability key, no version bump.** Session-scoped next to the
  rebase routes, both transports byte-identical. Every route names exactly
  one repo-relative ``path`` and acts on it only while the index holds it
  unmerged under that exact name — a literal pathspec still matches by
  directory prefix, so the stages are compared byte for byte; that check is
  what bounds the write route to a file git itself reports as conflicted.
  ``ours`` is index stage 2 and ``theirs`` stage 3: during a rebase, the
  branch being rebased onto (plus the commits already replayed) and the
  commit being replayed.
  * ``GET /api/sessions/{session_id}/git/conflict?path=`` answers
    ``GitConflictResponse {"path", "code", "state", "kind", "content",
    "token", "markers", "has_base", "ours": {"label", "present"}, "theirs":
    {"label", "present"}, "byte_limit"}``. ``code`` is the porcelain pair
    (``UU`` ``AA`` ``DU`` ``UD`` ``DD`` ``AU`` ``UA``); ``present`` is false
    for a side that deleted the file or never had it. ``kind`` is an open
    vocabulary — clients treat an unknown value as "not editable here":
    ``text``; ``too_large`` (over ``byte_limit``, 256 KiB — the file travels
    whole inside one tunnel frame, ``json.dumps`` escapes non-ASCII up to
    6x, and a frame over the 4 MiB cap closes the tunnel for every
    session); ``binary`` (a NUL byte); ``encoding`` (not UTF-8);
    ``missing``; ``symlink``; ``submodule``; ``unsupported`` (a directory in
    the file's place). Only ``text`` carries ``content`` — LF line endings,
    BOM stripped — and ``token``, the SHA-256 of the file's bytes as read.
    ``markers`` counts lines starting with ``<<<<<<<``; ``has_base`` tells
    that the file holds ``|||||||`` sections (diff3 style). ``label`` names
    a side for the user and is nullable: while ``rebasing``, ``ours`` is the
    ``onto`` branch (else its short oid) and ``theirs`` is ``<short_oid>
    <subject>`` of the stopped commit; while ``merging``, the current branch
    and ``MERGE_HEAD``. Clients never print "ours"/"theirs" and never read
    the label text inside the markers, which ``restore`` rewrites.
    ``409 not_conflicted`` (message = the path) when the path is not
    unmerged; ``400 bad_path`` also for any ``.git`` component.
  * ``POST …/git/conflict/resolve`` with ``GitConflictResolveRequest
    {"path", "token", "content", "mark_resolved": true}`` replaces the file
    and stages it. Under the per-toplevel lock: ``not_conflicted``, then
    ``409 unsupported`` for a submodule or symlink entry, then
    ``409 file_changed`` unless ``token`` matches the file on disk — Claude
    or the user may be editing it in the terminal — or the file already
    holds exactly the bytes this request would write (a retry after a lost
    answer or a failed ``git add``, which skips the write and still runs
    ``git add``), then an atomic replace
    (exclusive temporary beside the file, its permission bits kept, no
    symlink followed at any level; ``409 file_busy`` when the platform
    refuses the rename) and ``git add``. The file's own line-ending and BOM
    convention is re-applied from the bytes on disk (a file mixing endings
    gets its dominant one), since ``core.safecrlf`` would refuse an LF
    rewrite of a CRLF file. With ``mark_resolved`` a ``content`` still
    holding a ``<<<<<<<`` / ``>>>>>>>`` line is ``409 conflict_markers``
    before anything is written; ``mark_resolved: false`` is a draft — it
    writes the file, markers included, and leaves the path unmerged.
    ``content`` is validated by the service, not the schema (a ``422`` echoes
    its input): ``400 too_large`` over ``byte_limit`` — measured on the
    request's LF text and again on the bytes that would land on disk, since
    CRLF and a BOM grow it — and ``400 bad_content`` for a NUL or an
    unencodable string.
  * ``POST …/git/conflict/take`` with ``GitConflictTakeRequest {"path",
    "side": "ours" | "theirs"}`` makes the path look like one side and
    marks it resolved — the only resolution for a ``kind`` that is not
    ``text``. A side that is ``present`` runs ``git checkout --<side>`` and
    ``git add``; an absent side resolves by removal, which is how a
    modify/delete conflict is settled as "delete": the agent unlinks the
    file itself without following a symlink and then drops the index entry
    with ``git rm --cached`` — plain ``git rm`` unlinks by path and would
    follow a symlinked parent directory out of the repository. The choice
    is read from the index stages, never from git's wording. ``409
    unsupported`` for a submodule, a directory in the file's place, a
    symlink (or non-directory) where a parent directory should be, or a file
    the no-follow walk cannot reach; ``409 file_busy`` when the platform
    refuses the unlink.
  * ``POST …/git/conflict/restore`` with ``GitConflictRestoreRequest
    {"path", "style": "merge" | "diff3", "token"}`` runs ``git checkout
    --merge --conflict=<style>``: it rebuilds the markers of a file still in
    conflict (discarding edits made to it — ``diff3`` adds the original
    lines), or of a path in ``resolved_paths``. ``--conflict`` is always
    explicit, so ``merge.conflictStyle`` never decides. Git labels the
    rebuilt markers ``ours`` / ``theirs``, which reads backwards during a
    rebase, so the agent then swaps those two labels for the sides' names
    (best effort; ``||||||| base`` stays). ``409
    not_restorable`` unless the path is unmerged with both sides present or
    listed in ``resolved_paths`` — on any other path ``checkout --merge``
    silently overwrites the working file from the index, so this guard is a
    safety check. An optional ``token`` adds the ``file_changed`` check.
  * ``GitStatusResponse`` gains top-level ``resolved_paths`` (default
    ``[]``, at most 200): paths marked resolved during the operation in
    progress whose conflict git can still rebuild, read from the index's
    resolve-undo records. Listed only while ``repo.state`` is ``merging``,
    ``cherry_picking`` or ``reverting``, or ``rebasing`` with
    ``repo.rebase.stopped_on`` set — the stops whose own index rewrite
    cleared every older record; the records outlive a finished rebase or
    merge and a pick already committed, so a ``bisecting`` (or ``clean``)
    repository, a ``git am`` session and a rebase stopped on ``exec`` or
    ``break`` would offer a conflict nobody is in — only with both sides
    recorded (``checkout --merge`` cannot rebuild a modify/delete
    conflict), never for a submodule (git cannot merge a gitlink back into
    markers), never while unmerged again, and only while "ours" is still
    ``HEAD``'s blob for the path. It is a top-level list rather than an
    entry group because a resolution equal to ``HEAD`` has no status entry
    at all and one that differs already has a ``staged`` row. The etag
    covers it.
  * All three mutations refuse ``job_running`` first, share one 25 s budget
    across their git calls and publish ``git.changed`` whenever the file or
    the index may have moved — also when ``git add`` fails after the write.
    ``git checkout`` runs the repository's ``post-checkout`` hook and any
    configured filters under the agent's account, the trust boundary the
    rebase and stage routes already have.
  * ``GET /api/version`` ``capabilities.git`` gains ``"conflicts": true``
    (both transports). Clients open the resolver from a conflict row, show
    the *Resolved* section and word their "resolve the conflicts" hints for
    the app only with it; without it a conflict row keeps opening the
    ``HEAD`` diff and the hints keep pointing to the terminal.
  * Also: ``validate_repo_path`` now rejects ``\`` and ``:`` on Windows
    agents (``400 bad_path`` on every session git route) — ``os.path.join``
    honours the first as a separator and the second opens an alternate data
    stream; git never reports either.
  * Golden fixtures: new ``tests/protocol_fixtures/dto/agent/{
    GitConflictResponse,GitConflictResolveRequest,GitConflictTakeRequest,
    GitConflictRestoreRequest}.json``; ``GitStatusResponse.json`` gains
    ``resolved_paths`` on every case and a ``rebasing_with_resolved_paths``
    case — on the four registries.
* Additive delta within proto-v7 (2026-09-21, project Git management —
  delete remote branches, remove a worktree and keep its branch): **one
  widened request enum, six additive per-item error codes, one additive
  capability key, no version bump.** Same project-scoped
  ``POST /api/projects/{name}/git/items:delete``, both transports
  byte-identical (the relay already validates the body with
  ``GitDeleteRequest``):
  * ``GitDeleteItem.kind`` accepts ``remote_branch`` again, with the row's
    ``branch`` (``<remote>/<name>``) verbatim; ``force`` does not apply.
    The agent answers per item: ``invalid`` for a missing or NUL-bearing
    ``branch``, ``not_found`` unless it is one of the rows ``GET
    /items?remote=true`` lists (a configured remote, not the
    ``<remote>/HEAD`` symref), ``error`` unless the remote's fetch
    refspecs map the tracking ref back to exactly one ``refs/heads/``
    branch (a name-based guess would delete the wrong branch under a
    custom refspec), and ``error`` for every item of a remote whose
    configured push destinations — every ``remote.<r>.pushurl``, else
    every ``remote.<r>.url`` — are not exactly its one ``remote.<r>.url``
    (the row describes the fetch side; a push to another repository would
    delete there and still report success). The raw config strings are
    compared: a ``pushurl`` naming the same repository in another URL
    form is refused too, while ``url.<base>.pushInsteadOf`` rewrites are
    not checked, so an https-fetch / ssh-push setup written that way keeps
    working. The survivors go out as one ``git push --porcelain -- <remote>
    :refs/heads/<name>…`` per remote, the remotes in parallel with the
    batch's local deletes, under the fetch/pull/push jobs' never-prompt
    environment and a 20 s budget (inside the relay's 30 s). Hooks run. A
    ``-`` ref line is ``ok`` (git drops the tracking ref; a branch already
    gone on the remote reports the same and clears the stale ref); a ``!``
    line is ``rejected``, or ``hook_failed`` for a declining remote hook,
    with git's summary as ``error_detail``; a ref git never reported on
    takes the jobs' classifier, narrowed to ``auth_required`` /
    ``network`` / ``remote_not_found`` / ``rejected`` / ``hook_failed``
    (else ``error``), and a timeout is ``git_timeout``. These codes never
    fail the batch: ``results`` stays one entry per request item, in
    request order, as before. No busy guard (no worktree is touched).
    Each success is audited (``audit.git.delete``, ``kind=remote_branch``)
    and any success publishes ``git.changed``.
  * An ``orphan_worktree`` item removes only the worktree at
    ``worktree_path`` whatever row it came from — every agent already
    behaved this way — so clients send it for a ``branch_with_worktree``
    row's *Remove worktree* action and the branch survives as a
    ``branch_only`` row. *Delete* on that row keeps removing both.
  * ``GET /api/version`` ``capabilities.git`` gains ``"delete_remote":
    true`` (both transports). Clients offer delete on ``remote_branch``
    rows only behind it — an older agent answers ``422`` for the whole
    batch — and confirm a batch holding remote rows before it enters the
    undo window. *Remove worktree* needs no gate.
  * Clients render the Git screen's rows as a folder tree: a branch name
    splits on ``/`` (a remote row under a top folder named by its
    ``remote``), folders first then rows, each A→Z by lowercased UTF-16
    code units with the raw name as tiebreak; ``orphan_worktree`` rows
    follow the local tree by path. The New-branch start-point and rebase
    ``onto`` pickers use the same order. Presentation only — the wire order
    of ``GET /items`` is unchanged.
* Additive delta within proto-v7 (2026-09-17, in-session git rebase —
  start, continue, skip, abort, and Mark resolved): **four additive REST
  routes, one additive status field, one additive stage refusal, two
  additive capability keys, no version bump.** A rebase is a working-tree
  operation — git can only rebase the branch checked out in the directory
  it runs in, and a linked worktree's branch only from inside that
  worktree — so it lives on the session-scoped prefix next to the
  mutations, both transports byte-identical:
  * ``POST /api/sessions/{session_id}/git/rebase`` with ``GitRebaseRequest``
    ``{onto}`` runs ``git rebase --no-autostash --end-of-options <onto>``.
    Guards, in order: ``409 unknown_ref`` (message = ``onto``) for a
    ``-``-leading or NUL-carrying ref, then ``job_running``, then under the
    per-toplevel lock ``operation_in_progress`` (message = the state) unless
    the state is ``clean``, ``no_head`` on an unborn branch, and
    ``unknown_ref`` when ``rev-parse -q --verify --end-of-options
    <onto>^{commit}`` fails. Whether the tree is too dirty to start is
    git's own call (``dirty_worktree``: staged or unstaged changes to
    tracked files, or an untracked file the checkout would overwrite);
    ``--no-autostash`` keeps ``rebase.autoStash`` from stashing around it.
    An empty ``onto`` is a ``422``. The clients' branch pickers send fully
    qualified refs (``refs/heads/<name>``, ``refs/remotes/<remote>/<name>``)
    so a tag of the same name cannot shadow the branch; a typed ref goes as
    entered.
  * ``POST …/git/rebase/continue`` (no body) runs ``git rebase
    --continue``, refused with ``409 unmerged`` while ``ls-files -u`` lists
    anything; ``POST …/git/rebase/skip`` runs ``--skip`` (drops the commit
    git stopped at — for a rescheduled ``pick``/``reword``/``edit``, which
    ``--skip`` alone would retry, the agent first removes that line from
    ``git-rebase-todo`` under git's ``.lock`` and puts it back if the skip
    then fails without git touching the todo; a held
    ``git-rebase-todo.lock`` is ``409 index_locked``; a rescheduled
    ``squash``/``fixup`` is left for git to retry, because dropping it would
    corrupt the combined commit's message); ``POST …/git/rebase/abort`` runs
    ``--abort`` (back to the branch as it was). All three refuse ``409
    no_rebase`` (message = the state) unless the state is ``rebasing``, and
    ``job_running`` first.
  * All four answer ``GitRebaseResponse {"outcome", "rebase", "message"}``.
    ``outcome`` is an open vocabulary: ``rebased`` (finished), ``up_to_date``
    (a start that left HEAD where it was), ``stopped`` and ``aborted``.
    ``stopped`` is decided from the repository, never from git's wording
    (which quotes commit subjects and paths): the rebase is still in
    progress when git exits and the call moved it — a start always does; a
    continue, skip or abort did when it changed HEAD or ``REBASE_HEAD`` (the
    commit git stopped at), or left unmerged paths where there were none (a
    rescheduled pick — one an untracked file blocked — retried into a
    conflict). Not the todo step, which git bumps on every retry of a
    rescheduled pick, and never git's ``hint:`` lines, which quote the
    rescheduled pick's subject. ``message`` is ``null`` while unmerged paths
    explain the stop, else git's own reason (an untracked file in the way of a
    later pick, say). ``rebase`` is the ``GitRebaseInfo`` below, set only when
    ``stopped``. A call that fails without moving the rebase is a ``409``
    with the fetch/pull/push jobs' classifier codes and git's explanation
    as ``message``:
    ``dirty_worktree`` (``--continue`` with unstaged changes is reported
    this way too, although git words it like unmerged paths),
    ``hook_failed`` (the ``pre-rebase`` hook refused),
    ``operation_in_progress``, ``index_locked``, ``git_error``.
  * Editors are disabled with ``GIT_EDITOR=:`` and ``GIT_SEQUENCE_EDITOR=:``
    — ``:`` is git's own "no editor", so nothing is spawned on any OS and
    ``--continue`` keeps the original commit message. Each call runs under
    the per-toplevel lock with a 25 s budget (the relay gives a proxied
    request 30 s); a rebase the timeout cuts off is left in progress
    (``504 git_timeout``) for Abort to undo. ``git.changed {project_name,
    toplevel, common_dir}`` is published whenever git ran, whatever it
    answered. Repository hooks (``pre-rebase``, ``post-checkout``,
    ``post-rewrite``) run under the agent's account, the same trust
    boundary as commit.
  * ``GET …/git/status`` ``repo`` gains ``rebase``: ``null`` unless the
    state is ``rebasing``, else ``GitRebaseInfo {"branch", "onto_oid",
    "onto_short_oid", "onto_ref", "stopped_on": {"oid", "short_oid",
    "subject"} | null, "step", "total"}`` read from git's state directory
    (``head-name``, ``onto``, ``msgnum``/``end`` for the merge backend,
    ``next``/``last`` for the apply backend) and ``REBASE_HEAD``. ``branch``
    is null for a detached HEAD, ``onto_ref`` is a branch pointing at the
    base (a local branch first, then a remote-tracking one) or null, and
    every key is nullable because a rebase git was killed in need not have
    written every file. It stays ``null`` during a ``git am`` session, which
    shares ``rebase-apply``. The etag covers it, so skipping one conflicted
    commit to stop on another in the same file still reads as a change.
  * ``POST …/git/stage`` now refuses ``409 conflict_markers`` (message = the
    path) for an unmerged path whose working-tree file still holds a line
    starting with ``<<<<<<<`` or ``>>>>>>>``; staging an unmerged path is
    how a conflict is marked resolved. Binary files, symlinks, submodules
    and a deleted file (resolving by removal) are not scanned.
  * ``GET /api/version`` ``capabilities.git`` gains ``"rebase": true`` and
    ``"resolve": true`` (both transports). Clients gate the terminal menu's
    *Rebase…* entry on ``rebase``, the Changes screen's Continue / Skip /
    Abort on ``rebase`` with a non-null ``repo.rebase`` — a ``rebasing`` state
    without it (a ``git am`` session, or an older agent) gets a banner
    pointing to the terminal instead — and a conflict row's *Mark resolved*
    action on ``resolve`` (with ``stage``); an agent without ``resolve`` would
    stage the markers.
  * Golden fixtures: new ``tests/protocol_fixtures/dto/agent/{
    GitRebaseRequest,GitRebaseResponse}.json``; ``GitStatusResponse.json``
    gains ``rebase`` on every repo and a ``rebasing_stopped_on_conflict``
    case — on the four registries.
* Additive delta within proto-v7 (2026-09-16, project Git management —
  remote branches, create and check out): **one additive query flag, two
  additive REST routes, three additive row fields, one additive row kind,
  one additive capability key, no version bump.** Branches are objects of
  the repository, not of a session, so ref management lives on the
  project-scoped prefix ``/api/projects/{name}/git`` the delete-only Git
  screen already used, both transports byte-identical (the relay
  dispatcher hand-routes the new paths like the two existing ones):
  * ``GET /api/projects/{name}/git/items?remote=true`` — every row now
    carries ``subject`` and ``committed_at`` (the tip commit's subject line
    and committer date as ``iso-strict``, both ``null`` when the ref has no
    commit) and, with ``remote=true``, the remote-tracking branches follow
    the local rows as ``{"kind": "remote_branch", "branch":
    "<remote>/<name>", "remote": "<remote>", "is_current": false, …}``
    (``<remote>/HEAD`` symrefs skipped, as are refs under a name that
    matches no configured remote; ``remote`` resolved by the longest
    configured remote name because both halves may contain ``/``). The
    flag exists so clients that predate the kind never receive a row they
    would offer for delete; the new clients always send it and an older
    agent ignores it. ``remote`` is parsed as a pydantic bool on both
    transports (``true/t/yes/y/on/1`` and their negatives; anything else is
    ``422``).
  * ``POST /api/projects/{name}/git/branches`` ``{"name", "start_point"?,
    "checkout"?: false}`` → ``GitBranchResponse {"branch", "checked_out",
    "created": true}``. ``git branch -- <name> <start_point|HEAD>``;
    ``checkout: true`` runs ``git switch -c <name> -- <start_point>`` under
    the checkout guards below. The agent rejects an empty or > 255-byte
    name, a leading ``-``, ``HEAD``, ``@`` and any ``@{`` itself
    (``check-ref-format --branch`` expands revision syntax rather than
    refusing it), then asks ``git check-ref-format --branch``: ``400
    bad_branch_name``. ``409 exists`` when ``refs/heads/<name>`` resolves,
    ``409 unknown_start_point`` (message = the start point) when
    ``rev-parse --verify --end-of-options <start>^{commit}`` fails. A start
    point naming a local or remote-tracking branch is resolved as that
    branch (``refs/heads/…`` first, then ``refs/remotes/…``), so a tag of the
    same name cannot make it ambiguous; the item rows list local branches by
    their exact name (``%(refname:lstrip=2)``).
    Tracking follows git's own ``branch.autoSetupMerge``.
  * ``POST /api/projects/{name}/git/checkout`` ``{"ref"}`` →
    ``GitBranchResponse {"branch", "checked_out": true, "created"}``.
    ``ref`` is a row's ``branch`` verbatim: a local branch runs ``git
    switch --no-guess -- <ref>`` in the project root; a remote-tracking
    ``<remote>/<short>`` switches to the local ``<short>`` when it exists,
    else creates it tracking the remote (``git switch --no-guess --track -c
    <short> -- <remote>/<short>``, ``created: true``); anything else is
    ``404 {"detail": {"code": "ref_not_found"}}`` (detached checkouts are
    out of scope; ``400 bad_ref`` for an empty or ``-``-leading ref).
  * Checkout guards, all ``409``: ``job_running`` (message = job id) while a
    fetch/pull/push job owns the repository — asked first, before queuing on
    the per-toplevel lock, so a running job's whole duration is never
    awaited only to be refused — then, once queued, the project-git
    ``{"detail": {"busy_by": [...]}}`` shape when a session or auto-scan
    item works in the project root — a holder whose realpath-normalised cwd
    is the root or lies below it and not inside a linked worktree, so a
    session in a sub-folder blocks and a session in a linked worktree does
    not — and ``operation_in_progress`` (message = ``merging`` /
    ``rebasing`` / ``cherry_picking`` / ``reverting`` / ``bisecting``).
    ``dirty`` and ``checked_out_elsewhere``
    (message = the worktree path) are git's own refusals classified from
    stderr: ``git switch`` carries unrelated local changes along and only
    refuses an overwrite, so a modified tree does not by itself block a
    checkout. The mutation floor (``git_too_old`` below 2.26),
    ``not_a_repo``, ``bare_repository``, ``unsafe_directory``,
    ``cwd_missing`` (400), ``git_unavailable`` (503), ``git_timeout`` (504,
    15 s) and ``git_error`` (500) are the session vocabulary. Clients read
    ``detail.busy_by`` first, then ``detail.code``. Both routes serialise
    under the worktree manager's per-project lock *and* the session
    mutations' per-toplevel lock, audit (``audit.git.branch.created`` /
    ``audit.git.checked_out``) and publish ``git.changed {project_name,
    toplevel, common_dir}``.
  * ``GET /api/version`` ``capabilities.git`` gains ``"branches": true``
    (both transports). Clients gate the Git screen's New branch and Check
    out actions and the terminal menu's *Branches…* entry (a link to the
    project Git screen, only for a session with a ``project_name``) on it;
    the remote rows need no gate because the flag is sent regardless.
  * ``GitDeleteItem.kind`` is narrowed to the three deletable kinds, so a
    ``remote_branch`` row in a delete batch is a ``422``.
  * Golden fixtures: ``GitItem.json`` gains ``subject`` / ``committed_at``
    and a ``remote_branch`` case; new
    ``tests/protocol_fixtures/dto/agent/{GitBranchCreateRequest,
    GitCheckoutRequest,GitBranchResponse}.json`` on the four registries.
    The project-git shapes follow the "defaults materialise" convention,
    so the Python arm compares the dump minus nulls with
    ``decoded_canonical`` for them (``test_git_fixtures.py``).
* Additive delta within proto-v7 (2026-09-15, in-session git fetch/pull/push
  jobs): **five additive REST routes + two additive event types + three
  additive capability keys, no version bump.** Network git operations can
  outlast the relay's 30 s proxied-request budget and need progress, so —
  unlike the 2026-09-14 mutations — they run as agent-side **jobs**: the
  route answers at once with a snapshot and the work continues in a
  background task that reports over the agent events channel. Same
  session-scoped prefix, both transports byte-identical:
  * ``POST /api/sessions/{session_id}/git/fetch`` (no body) runs
    ``git fetch --progress --prune <remote>`` — the branch's upstream remote,
    else ``origin``, else the only (first listed) remote.
  * ``POST …/git/pull`` with ``GitPullRequest`` ``{mode: "ff_only" |
    "rebase" | "merge"}`` (optional, default ``ff_only``; an empty body is
    accepted) runs ``git pull --progress --no-stat`` plus ``--ff-only`` /
    ``--rebase`` / ``--no-rebase --no-edit``. Clients start with
    ``ff_only`` and offer the two history-rewriting modes only as a
    confirmed follow-up to ``not_fast_forward``.
  * ``POST …/git/push`` with ``GitPushRequest`` ``{force: false}``
    (optional) runs ``git push --progress``, with ``--force-with-lease``
    only when ``force`` is true (clients confirm first, and offer it only
    as a follow-up to a push that failed with ``not_fast_forward`` or
    ``rejected``) and ``--set-upstream <remote> <branch>`` when the
    branch has no upstream configured (remote = ``branch.<name>.pushRemote``
    / ``remote.pushDefault`` / ``branch.<name>.remote``, else ``origin``,
    else the only remote). A branch whose upstream is configured but whose
    remote-tracking ref is gone (the remote branch was deleted, then pruned;
    the status reports ``upstream`` without ``ahead``/``behind``) is pushed
    as ``<remote> refs/heads/<branch>:<branch.<name>.merge>``, recreating
    that branch — never as a new one. Plain ``git push`` is never used to
    publish a branch.
  * Every start route answers ``200`` ``GitJobResponse`` ``{job_id,
    session_id, op: fetch | pull | push, status: running | done, phase,
    percent, line, ok, error_code, message}`` — every key always present.
    ``phase`` is git's own progress label with any ``remote: `` prefix
    stripped (``Counting objects``, ``Writing objects``, ``Receiving
    objects``, ``Resolving deltas``, ``Rebasing``…), ``percent`` the
    per-phase 0–100 (it resets at each phase — clients label the bar with
    ``phase`` and never animate the width), ``line`` the latest stderr line
    (≤ 200 chars); ``ok`` / ``error_code`` / ``message`` stay ``null``
    until ``status`` is ``done``. ``message`` (≤ 500 chars) is the last
    meaningful output line on success (``443f30d..9419146 main -> main``,
    ``Everything up-to-date``, ``Fast-forward``, ``Already up to date.``)
    and git's own error line(s) on failure. ``error_code`` is an **open**
    vocabulary (clients render an unknown code from ``message``):
    ``cancelled`` | ``git_timeout`` | ``auth_required`` (git could not
    obtain credentials — ``GIT_TERMINAL_PROMPT=0``, ``GIT_ASKPASS=""``,
    ``SSH_ASKPASS_REQUIRE=never`` and ``GCM_INTERACTIVE=never`` guarantee
    nothing ever waits for a person at the host; a credential helper or
    ``ssh-agent`` there is the only fix) | ``network`` | ``remote_not_found``
    | ``not_fast_forward`` (the branch is behind the remote: an ``ff_only``
    pull on diverged branches, or a push the remote refused as
    ``non-fast-forward`` / ``fetch first`` — clients offer *Pull with
    rebase* / *Pull with merge*, and after a push also *Force push (with
    lease)*) | ``rejected`` (any other push refusal — a stale lease,
    ``[remote rejected]`` — clients offer only the lease push) | ``conflicts`` (the merge or rebase stopped; the repository
    is now ``merging`` / ``rebasing`` and the Changes list shows the
    conflicts group) | ``dirty_worktree`` | ``unmerged`` |
    ``operation_in_progress`` | ``no_upstream`` | ``no_remote`` |
    ``detached_head`` | ``no_head`` (no commits yet) | ``hook_failed`` |
    ``index_locked`` | ``git_error``.
  * ``GET …/git/jobs/{job_id}`` → the current ``GitJobResponse`` (finished
    jobs stay readable for 10 minutes, at most 100 of them); ``POST
    …/git/jobs/{job_id}/cancel`` kills the whole git process tree and
    answers the snapshot once the job settled: ``error_code: cancelled``
    unless git had already finished successfully before the cancel
    arrived, in which case the real outcome (``ok: true`` and git's
    summary) is reported because the remote or worktree may already have
    moved; a no-op on a finished job. Both answer ``404 {"detail": {"code":
    "job_not_found"}}`` for an unknown id — a coded detail, unlike the
    string detail of a missing session — and the id is looked up
    agent-wide (any session may read a job it learned of through
    ``job_running``).
  * Events on the agent events channel (both transports; the relay stays
    opaque): ``git.job.progress`` carries the full ``GitJobResponse`` with
    ``status: running`` — once immediately when the job is accepted (even
    before the HTTP answer reaches the caller, so every viewer of the
    session can adopt a job another client started), then at most **four
    per second per job**, newest counters winning; because every frame is
    the whole state a client that misses one loses nothing.
    ``git.job.done`` carries the final snapshot exactly once, and is
    followed by ``git.changed`` ``{project_name, toplevel, common_dir}``
    after **every** job, successful or not (refs and worktree may have
    moved either way). Clients treat the events as the fast path and
    ``GET …/jobs/{job_id}`` as the source of truth: while a job is active
    and the screen is visible they poll it every 3 s, and a ``running``
    snapshot never overwrites a ``done`` one.
  * One job per repository toplevel: a second start answers ``409
    {"detail": {"code": "job_running", "message": "<active job id>"}}``, and
    so do ``stage`` / ``unstage`` / ``discard`` / ``commit`` while a job
    owns that toplevel (the job holds the per-toplevel lock for its whole
    run, so a commit that already held it finishes first, and a write
    that arrives later is refused rather than queued behind the job's 120 s
    budget). The other start-time refusals are the mutation ones —
    ``404`` session / ``503 git_unavailable`` / ``400 cwd_missing`` /
    ``422`` / ``409 not_a_repo | bare_repository | unsafe_directory |
    git_too_old`` — everything after acceptance is reported through the
    job's ``error_code``.
  * ``GET /api/version`` ``capabilities.git`` becomes ``{"changes": true,
    "stage": true, "discard": true, "commit": true, "fetch": true, "pull":
    true, "push": true}`` (both transports). Clients show the terminal
    menu's *Fetch / Pull / Push…* entry and the Changes screen's Sync
    button when any of the three keys is true and gate each action on its
    own key.
  * Bounds: the network command has 120 s and a 1 MiB stdout cap
    (``--no-stat`` keeps a pull's output small; progress is on stderr and
    is streamed, not retained — the runner keeps the first 64 KiB of stderr
    and the classifier reads the job's own tail of the last 50 ordinary
    lines, because the lines that explain a failure come last). The
    pre-steps (``symbolic-ref``, ``rev-parse``, ``remote``,
    ``for-each-ref``) have 5 s each. The agent's shutdown cancels running
    jobs.
  * Golden fixtures ``tests/protocol_fixtures/dto/agent/{GitPullRequest,
    GitPushRequest,GitJobResponse}.json`` pin the three shapes on the same
    registries as the mutation fixtures; ``GitJobResponse`` carries
    ``decoded_canonical`` cases because its wire always includes nulls.
* Additive delta within proto-v7 (2026-09-14, in-session git mutations):
  **four additive REST routes + one additive read route + three additive
  capability keys, no version bump.** Building on the 2026-08-27 Changes
  API, the agent now also mutates the staged set of the directory a session
  was spawned in (same session-scoped prefix, both transports byte-identical):
  * ``POST /api/sessions/{session_id}/git/stage`` with ``GitPathsRequest``
    ``{paths: [..]}`` (1–5 000 repo-relative paths exactly as ``status``
    reported them; a renamed entry lists both ``path`` and ``orig_path``)
    runs ``git add -A`` limited to those paths — modifications, deletions,
    type changes and untracked files alike — and answers
    ``GitMutationResponse`` ``{ok: true}``.
  * ``POST …/git/unstage`` (same body) runs ``git reset -q`` on the paths
    (not ``restore --staged``, which cannot resolve ``HEAD`` on a branch
    with no commits yet); a staged rename is undone only when both of its
    paths are sent.
  * ``POST …/git/discard`` with ``GitDiscardRequest`` ``{paths, scope:
    "unstaged" | "untracked"}`` throws working-tree changes away:
    ``unstaged`` → ``git restore --worktree`` (a staged part of the same file
    survives; a modify-delete is recreated from the index), ``untracked`` →
    ``git clean -f`` (which can only ever delete untracked, non-ignored
    files). Clients must confirm before calling it; it is never one tap.
  * ``GET …/git/commit/context`` → ``GitCommitContextResponse`` ``{author
    {name, email} | null, head {oid, short_oid, subject, body} | null}``:
    ``author`` is what ``git var GIT_AUTHOR_IDENT`` resolves under the same
    strictness ``git commit`` applies (``null`` means the commit would be
    refused with ``identity_required``); ``head`` is ``null`` on an unborn
    branch, so the commit sheet can show who is committing, prefill an
    Amend, and hide Amend when there is nothing to amend.
  * ``POST …/git/commit`` with ``GitCommitRequest`` ``{message, amend:
    false, no_verify: false}`` (``message`` non-blank, ≤ 64 KiB, first line
    is the subject; ``amend`` and ``no_verify`` are optional and default to
    ``false``, so a client may omit them) commits **the staged set only**
    (never ``-a``): the
    message travels on stdin (``-F -``) and is cleaned with
    ``--cleanup=whitespace`` so a leading ``#`` survives; ``--amend`` /
    ``--no-verify`` are passed only when asked. Answers
    ``GitCommitResponse`` ``{oid, short_oid, subject}`` of the new ``HEAD``.
    Repository hooks (``pre-commit``, ``commit-msg``, …) run on the agent
    machine under the agent's account — the same trust boundary as the PTY
    — with the runner's scrubbed environment.
  * Every mutation resolves the repository first, then runs under a
    per-toplevel ``asyncio.Lock`` (two clients of one session cannot
    interleave index writes) and, on success, publishes ``git.changed``
    ``{"project_name": <session's project or "">, "toplevel", "common_dir"}``
    so every viewer of that repository — including the caller — refreshes
    through the etag poll; the reads still take no lock. Pathspecs travel
    NUL-separated on stdin (``--pathspec-from-file=- --pathspec-file-nul``):
    no argv limit on Windows, and for ``stage`` / ``discard`` (``add``,
    ``restore``) one unmatched path fails the whole call before anything is
    touched; ``unstage`` (``reset``) and an untracked ``discard`` (``clean``)
    silently skip a path that no longer matches. An empty ``paths`` list is
    refused by the schema (``422``) and again by the service itself
    (``400 bad_path``, for any caller that bypasses the schema) — an empty
    pathspec file would mean "everything" to git. Only ``git clean``
    lacks the stdin option and takes argv in chunks of 100.
  * Errors, identical on both transports: ``404`` session / ``503
    git_unavailable`` / ``504 git_timeout`` / ``500 git_error`` as for the
    reads; ``400 {"detail": {"code": "bad_path" | "bad_scope" |
    "cwd_missing" | "empty_message"}}``; ``422`` for a malformed body; and
    **new** ``409 {"detail": {"code": <refusal>, "message": <≤ 500 chars of
    git output>}}`` whenever git declines because of the repository's state:
    ``not_a_repo`` | ``bare_repository`` | ``unsafe_directory`` |
    ``git_too_old`` (mutations need git ≥ 2.26 for ``--pathspec-from-file``
    on ``reset``; the reads keep 2.15) | ``pathspec_mismatch`` (``add`` /
    ``restore`` found no file for a path — git touched nothing, refresh) |
    ``identity_required`` |
    ``unmerged`` (a commit is refused while conflicts are unresolved) |
    ``nothing_to_commit`` | ``no_head`` (amend on an unborn branch) |
    ``hook_failed`` (exit 1 after the pre-checks — ``message`` is the hook's
    output) | ``index_locked`` (``index.lock`` exists — another git process is
    running, retry). Old agents answer ``404`` for the new routes; clients
    never show a mutating control without the matching capability key.
  * ``GET /api/version`` ``capabilities.git`` becomes ``{"changes": true,
    "stage": true, "discard": true, "commit": true}`` (both transports).
    Clients gate row/section/selection Stage–Unstage on ``stage``, Discard on
    ``discard``, and the Commit sheet plus the terminal menu's *Commit…*
    entry on ``commit``; an agent without a key stays read-only.
  * Timeouts: stage / unstage / discard 15 s, commit 25 s (hooks run inside
    it and the relay gives a proxied request 30 s), context 5 s. Stdin is
    fed by a third runner thread so a 5 000-path list or a long message
    cannot deadlock against the output readers.
  * Golden fixtures ``tests/protocol_fixtures/dto/agent/{GitPathsRequest,
    GitDiscardRequest,GitMutationResponse,GitCommitRequest,GitCommitResponse,
    GitCommitContextResponse}.json`` pin the six shapes on the same three
    registries as the read fixtures (iOS ``FixtureRegistry``, web coverage
    manifest, ``mobaide_agent/tests/test_git_fixtures.py``).
* Additive delta within proto-v7 (2026-08-27, in-session git Changes API):
  **two additive read-only REST routes + one additive capability key + two
  additive event keys, no version bump.** The agent now answers, for the
  directory a session was spawned in (project root or linked worktree —
  clients never send a path to run in):
  * ``GET /api/sessions/{session_id}/git/status?etag=<opaque>`` →
    ``GitStatusResponse``: ``session_id``, ``cwd`` (nullable), ``etag``,
    ``unchanged``, ``unavailable_reason`` (``null`` | ``not_a_repo`` |
    ``bare_repository`` | ``cwd_missing`` | ``unsafe_directory`` |
    ``git_too_old``), ``repo`` (``toplevel``/``git_dir``/``common_dir`` as
    absolute paths with forward-slash separators (backslash bytes in a
    POSIX filename are kept verbatim), ``is_linked_worktree``, ``head``
    ``{oid, short_oid, branch, detached, initial}`` with nullable oid/branch,
    ``upstream`` ``{ref, ahead, behind} | null`` — ``ahead``/``behind`` are
    ``null`` when the upstream ref is gone — and ``state`` ``clean`` |
    ``merging`` | ``rebasing`` | ``cherry_picking`` | ``reverting`` |
    ``bisecting``), ``entries[]`` (``group`` ``conflicts`` | ``staged`` |
    ``unstaged`` | ``untracked``, ``path``, ``orig_path``, ``code`` — one
    letter, or two for conflicts such as ``UU`` — ``index_status``,
    ``worktree_status``, ``rename_score``, ``submodule``; a file that is both
    staged and modified yields one entry per group, like VS Code), ``counts``
    ``{conflicts, staged, unstaged, untracked, total}``, ``truncated`` and
    ``entry_limit`` (5 000; ``counts`` come from the full parse). **Every key
    is always present**; ``unchanged: true`` (the supplied ``etag`` still
    matches) carries ``repo: null``, ``entries: []``, ``counts: null`` and
    means "keep your cached body". Repo *conditions* are ``200`` with
    ``unavailable_reason`` and ``repo: null``, never HTTP errors. The etag
    covers the porcelain output, the resolved repo and the state, so a commit
    or a finished merge moves it; an untracked file's *content* change does
    not (porcelain carries no hash for it).
  * ``GET /api/sessions/{session_id}/git/diff?path=&scope=staged|unstaged|untracked&context=3&orig_path=``
    → ``GitDiffResponse``: ``path``, ``orig_path``, ``scope``, ``kind``
    (``text`` | ``binary`` | ``submodule`` | ``too_large`` | ``missing``),
    ``change`` (``modified`` | ``added`` | ``deleted`` | ``renamed`` |
    ``copied`` | ``type_changed``; ``null`` only for ``missing``),
    ``old_mode``/``new_mode`` (nullable), ``additions``, ``deletions``,
    ``hunks[] {old_start, old_lines, new_start, new_lines, heading, lines[]
    {t: ctx|add|del, old, new, text, no_newline}}``, ``truncated``,
    ``total_lines`` (body lines seen — a lower bound when the byte cap killed
    git), ``line_limit`` (5 000) and ``byte_limit`` (1 MiB). Past the line
    cap the agent keeps counting but returns no further hunks, so a sparse
    diff never ships header-only hunks after the preview (clients still skip
    a header-only hunk of a truncated diff). The **agent
    parses** the unified diff so the three clients are dumb renderers with
    one tested parser; ``\r`` is preserved verbatim in ``text``. ``path`` and
    the optional ``orig_path`` are repo-relative as returned by ``status``;
    pass the entry's ``orig_path`` for a rename, because a pathspec-limited
    ``git diff -M`` cannot see the rename's source otherwise. Conflicted
    files (group ``conflicts``) are requested with ``scope=unstaged`` and are
    diffed against ``HEAD`` (conflict markers appear as ``+`` lines; a
    modify/delete conflict whose working-tree file equals ``HEAD`` answers
    ``kind: text`` with no hunks); ``scope=staged`` on an unmerged path is
    ``kind: missing``. Untracked
    diffs are synthesised by the agent (no ``/dev/null`` — broken on Git for
    Windows) and only for paths git itself lists as untracked and not
    ignored (``ls-files --others --exclude-standard``; ``.git/`` and
    gitignored files answer ``kind: missing``); a symlink diffs as its
    target text with mode ``120000``.
    ``context`` is clamped to 0–5 000 (the line cap). At the cap an
    untruncated diff is the whole file as one hunk, which is how clients
    request their *Full file* view.
  * Errors, identical on the direct and relay transports: ``404
    {"detail": "Session '<id>' not found"}``; ``400 {"detail": {"code":
    "bad_scope" | "bad_path" | "<unavailable_reason>"}}`` (``path`` must
    normalise to a repo-relative POSIX path — no absolute, drive-letter,
    ``..`` or NUL); ``503 {"detail": {"code": "git_unavailable"}}`` when no
    ``git`` binary is on the agent's PATH; ``504 {"detail": {"code":
    "git_timeout"}}``; ``500 {"detail": {"code": "git_error", "message":
    …}}`` for an unexpected git failure — a repository path containing a
    literal newline is unsupported and always answers this. Old agents
    answer ``404`` for both routes → clients render an "update your agent"
    state; the capability key lets them gate proactively.
  * ``GET /api/version`` gains ``capabilities.git = {"changes": true}``
    (emitted by both transports, next to ``capabilities.cli``).
  * ``git.changed`` (published after a successful branch/worktree delete)
    gains ``toplevel`` and ``common_dir`` — the project's resolved repository
    root and common git dir, ``null`` when the project is no longer a repo —
    so a session whose ``repo.common_dir`` (linked worktree) or
    ``repo.toplevel`` matches can invalidate its Changes cache even when it
    has no ``project_name``. Payload: ``{"project_name", "toplevel",
    "common_dir"}``. All three clients read the payload by key and ignore
    unknown ones.
  * Bounds and safety: every git call goes through a dedicated runner
    (``mobaide/core/git_exec.py``) — bytes mode, two reader threads,
    ``timeout`` + ``max_bytes`` enforced by killing the whole process tree
    (``taskkill /T`` on Windows, where PATH usually resolves to the
    ``cmd\git.exe`` wrapper), a scrubbed environment (every inherited
    ``GIT_*`` variable removed — repository redirection, config injection,
    external programs and trace files alike; ``LC_ALL=C``,
    ``GIT_TERMINAL_PROMPT=0``, ``GIT_OPTIONAL_LOCKS=0`` set),
    ``--no-optional-locks --literal-pathspecs -c core.quotePath=false``, and
    ``--no-ext-diff --no-textconv`` on every diff call (numstat and patch
    alike) so a repository's ``.gitattributes`` diff drivers can never
    execute; the patch call alone also sets ``--submodule=short`` so a
    user's ``diff.submodule`` setting cannot change the parsed format.
    Git ≥ 2.15 is required (older → ``git_too_old``). The relay's 4 MB
    tunnel frame is why the caps
    are server-side (5 000 entries; 1 MiB / 5 000 lines per diff). For very
    large trees the user-side accelerators are ``git update-index
    --untracked-cache`` (run ``--test-untracked-cache`` first) and
    ``core.fsmonitor``; the agent never watches files.
  * Golden fixtures ``tests/protocol_fixtures/dto/agent/{GitStatusResponse,GitDiffResponse}.json``
    pin both shapes on the iOS registry, the web coverage manifest and a new
    Python arm (``mobaide_agent/tests/test_git_fixtures.py``) that validates
    every case against the pydantic models and checks that each
    ``decoded_canonical`` equals its ``wire`` with null-valued keys removed —
    the Swift ``Codable`` nil-dropping rule the other arms rely on.
* Additive delta within proto-v7 (2026-08-23, Codex model catalog): **additive
  optional key + one relaxed field, no version bump.** Each
  ``capabilities.cli.<cli>`` entry on ``GET /api/version`` (see the proto-v5
  CLI-availability bullet for the base ``{"available": <bool>}`` shape) may also
  carry ``models`` — the agent's startup enumeration of that CLI's launchable
  models, in CLI-reported order — and ``model_details`` — an array of
  ``{"id": <str>, "efforts": [<str>, …], "default_effort"?: <str>}`` for CLIs
  whose catalog exposes per-model reasoning levels (today: Codex, via
  ``codex debug models``). Both keys are omitted — never emitted empty or null —
  when enumeration is unsupported or produced nothing; clients then fall back to
  their hardcoded model/effort lists, so old and new agents degrade identically.
  An entry may also carry ``"models_pending": true`` (omitted, never false):
  the agent's startup enumeration was still running when this response was
  built (the handler waits a short bound, but a single probe attempt may
  legitimately take longer) and *this CLI's own* enumeration had not yet
  produced a result — a CLI that already memoized its list is never marked
  merely because a sibling's probe still runs. The absence of
  ``models``/``model_details`` in a marked entry is provisional, not
  authoritative: clients keep persisted catalog selections instead of
  reconciling them away, and re-probe until an unmarked answer arrives. Its
  presence also proves a catalog-era agent (pre-catalog agents never emit it).
  Emitted by the shared ``build_cli_capabilities`` builder on both the direct
  and relay transports. (``models`` itself had shipped earlier without a
  PROTOCOL.md entry; this bullet is its documentation of record.)
  * ``codex_options.reasoning_effort`` is relaxed from the proto-v5 enum
    (``minimal`` | ``low`` | ``medium`` | ``high``) to a free-form string on the
    agent, mirroring the ``model`` field's "free-form on the server; the client
    curates the list" posture: the effort vocabulary is now enumerated per-model
    at runtime, and a closed set would reject values the agent itself just
    advertised (e.g. ``xhigh``/``max``/``ultra`` on the gpt-5.6 catalog). The
    value is embedded after ``=`` inside the single exec'd argv element
    ``-c model_reasoning_effort=<v>``, so no flag-smuggling path opens up. The
    proto-v5 section below remains historically accurate for agents of that era.
* Additive delta within proto-v7 (2026-08-11, Claude debug toggle): **additive
  optional field, no version bump.** ``claude_options`` gains ``debug`` — bool,
  default ``false`` — emitted by the agent as ``--debug``. Previously
  ``ClaudeLauncher._build_argv`` passed ``--debug`` unconditionally on every
  Claude session (interactive, Auto Scan and worktree alike), so every user saw
  Claude Code's verbose diagnostics; it is now a per-session choice surfaced as
  a **Debug logging** toggle in all three clients' create-session wizards,
  default off. No other managed CLI ever carried the flag, and the relay
  forwards ``claude_options`` opaquely, so ``central_server`` is untouched.
  * The wire-absence default (``false``) **matches** the clients' UI default,
    which is what makes this additive rather than a ``skip_permissions``-style
    hazard: an omitted field means exactly what a client leaving the toggle off
    means. Old client + new agent therefore lands on debug **off** — the
    intended outcome. New client + old agent is a harmless no-op: the old
    agent ignores the unknown key and keeps hardcoding ``--debug`` until it
    updates.
  * Shared-fixture consumers (iOS ``ClaudeOptionsModel``, the web
    ``claudeOptionsSchema``) both carry the field, so all three fixtures that
    embed a ``claude_options`` object were updated:
    ``tests/protocol_fixtures/dto/agent/{ClaudeOptionsModel,CreateSessionRequest,StartScanRequest}.json``.
    Only ``ClaudeOptionsModel.json`` exercises both values — ``debug: true`` in
    its ``full`` case and ``debug: false`` materialized in
    ``empty_defaults_materialize_skip_permissions_true``; the
    ``CreateSessionRequest`` / ``StartScanRequest`` cases carry it explicitly as
    ``false`` (their own defaults cases have no ``claude_options`` block at
    all). Both consumers emit every boolean on re-encode, so an omitted key
    there fails the round-trip.
* Additive delta within proto-v7 (2026-08-05, stale-attach fix): **agent-side
  only — no wire change, no version bump.** ``RelayClient._handle_session_input``
  no longer auto-creates a session for a ``CH_SESSION_IO`` frame naming an
  unknown ``session_id``; it drops the frame and answers with the same
  ``stopped`` state frame ``_handle_mobile_attach`` already sends for a stale
  attach (both now share ``_send_stopped_frame``). Auto-create used to fire off
  a bare resize frame — which Android, iOS and the web client all send
  unconditionally on every socket connect to re-assert their cached PTY size —
  so merely attaching to a stale id (agent restarted, session swept, a resumed
  id from preferences, a stale notification, a restored navigation stack)
  silently spawned a real login shell. The empty-payload guard, which
  previously sat *after* the auto-create, now runs first. An unpatched client
  paired with a fixed agent needs no changes: it receives exactly the
  ``stopped`` frame the attach path has always sent for a stale id and renders
  a stopped session instead of a phantom shell — old client + new agent is the
  supported combination. Deciding that a session is *gone* (rather than merely
  stopped) stays a client-side judgement from the ``sessions.changed``
  snapshot, not a wire signal.

## proto-v6 (superseded by proto-v7)

* Introduced: 2026-06-26
* App support: ``APP_PROTOCOL_VERSION = 6`` (Android), ``PROTOCOL_VERSION = 6``
  (Python — both ``mobaide_agent`` and ``central_server``).
* Change vs proto-v5: **breaking** to the ``claude_options`` shape
  (pre-production, no compatibility window). Replaces Claude's boolean
  ``max_effort`` with a rich option set mirroring the Codex precedent (CLA-84).
  The version is bumped so the three artifacts stay in lockstep and an old client
  is cleanly rejected (``4002`` + HTTPS-bootstrap auto-update) rather than
  silently launching with the wrong reasoning level.
* Wire shape (delta from proto-v5), all on ``CH_CONTROL`` (forwarded verbatim by
  ``central_server`` — no server logic change):
  * The ``create_session`` control message (the ``POST /api/sessions`` body
    proxied over ``CH_CONTROL``, and the Auto Scan ``claude_options``) drops
    ``max_effort`` (bool) and gains five fields:
    * ``model`` — str|null: a curated Claude alias (``opus`` | ``sonnet`` |
      ``haiku`` | ``fable``) or null for the account default. Free-form on the
      server (the client curates the list); emitted as ``--model <alias>``.
    * ``effort`` — ``low`` | ``medium`` | ``high`` | ``xhigh`` | ``max`` | null.
      Every non-null level is emitted as ``--effort <level>`` (``high``
      included — with no flag the CLI falls back to the operator's persisted
      ``effortLevel`` / per-model default, which is not reliably ``high``);
      null emits no flag; suppressed entirely when ``ultracode`` is on.
    * ``fast_mode`` — bool (Opus-only fast mode) and ``ultracode`` — bool: both
      emitted through a single merged ``--settings`` dict
      (``{"fastMode": true, "ultracode": true}``), merged into the per-session
      attention-hooks settings file when one is written — never two separate
      ``--settings`` flags.
    * ``append_system_prompt`` — str|null: emitted as
      ``--append-system-prompt <text>``.
  * ``codex_options`` and all other CH_CONTROL / CH_EVENTS / CH_SESSION_IO shapes
    are unchanged.
* Managed **Gemini CLI** as a third coding agent (CLA-87 — **additive, no
  version bump**): Gemini is wired through the same extension points the Codex
  foundation established, so no JSON shape breaks and the version stays at ``6``.
  An old proto-v6 client is unaffected (it never sends ``cli:"gemini"``); a new
  client feature-detects Gemini via the ``capabilities.cli`` advertisement rather
  than a version gate — the same forward-compat contract that made the Codex
  auth/login and CLI-availability additions no-bump changes (see proto-v5).
  * ``create_session`` (``POST /api/sessions`` proxied over ``CH_CONTROL``):
    ``cli`` gains the value ``"gemini"`` and a new optional ``gemini_options``
    object — ``model`` (free-form string: a preset alias ``auto`` / ``pro`` /
    ``flash`` / ``flash-lite``, any concrete model id, or null),
    ``approval_mode`` (``default`` | ``auto_edit`` | ``yolo`` | ``plan``;
    ``--approval-mode`` replaces Gemini's deprecated ``--yolo``),
    ``include_directories`` (array of str), ``sandbox`` (bool), plus the shared
    ``worktree`` / ``branch_name`` intent. Exactly one of ``claude_options`` /
    ``codex_options`` / ``gemini_options`` is honoured per session.
  * ``session_to_wire_dict`` ``kind`` gains the value ``"gemini"`` — the Android
    client already coerces an unknown ``kind`` forward-compatibly (a typed
    ``Gemini`` enum member lands with the create-session UI, CLA-88).
  * Missing binary → ``gemini_cli_missing: true`` + ``searched_paths`` (``503`` /
    ``session_create_failed``); an unauthenticated host → ``gemini_auth_required:
    true`` (``409``, non-``401`` for the same app-JWT-interceptor reason as
    Codex). Auth is **detection-only**: Gemini has no ``login`` /
    ``--with-api-key`` subcommand and its OAuth needs a browser, so there is
    **no** ``POST /api/gemini/login`` route — the operator authenticates
    ``gemini`` on the host (``GEMINI_API_KEY`` / ``GOOGLE_API_KEY`` / Vertex-AI
    credentials / cached OAuth). In-app API-key entry is deferred (CLA-91).
  * ``capabilities.cli`` on ``GET /api/version`` gains a ``gemini`` entry from the
    same registry-driven builder, so the mode-select screen enables Gemini only
    when its binary resolves on the host.
* APNs push pipeline for session attention (CLA-106 — **additive, no version
  bump**; neither ``envelope.py`` copy changes, so the diff guard is untouched).
  Fills the iOS-09 gap: a suspended iOS app has no live socket, so the server
  must be able to *see* attention (today it rides the server-opaque
  ``CH_EVENTS``) and push it.
  * **User-scoped events channel** (``/relay/ws/user-events``, documented here
    for the first time): a one-way server→client text channel authenticated
    with the dashboard/user JWT. Each frame is a JSON envelope ``{"type":
    <kind>, "payload": {...}}``. Existing kinds (all server-originated):
    ``agent.status_changed`` ``{"agent_id", "online"}`` and
    ``template.created`` / ``template.updated`` (full template row) /
    ``template.deleted`` ``{"template_id", "version", "updated_at"}``. Clients
    must ignore unknown kinds (both apps already do — Android
    ``else -> Unit``, iOS ``default: .none``).
  * New user-event kind ``session.attention`` — payload ``{"session_id",
    "reason", "title"?, "project_name"?, "exit_reason"?, "agent_id"}`` (slim,
    not the full session wire dict; ``reason`` is the proto-v4 attention
    enum). ``agent_id`` is **stamped by the server** from the authenticated
    agent tunnel — an agent-supplied value is never trusted.
  * New agent→server ``CH_CONTROL`` message ``{"type": "user_event", "kind":
    "session.attention", "payload": {…}}``, sent by the agent alongside (not
    instead of) the existing opaque ``CH_EVENTS`` ``session.attention``
    publication, on attention **set** only. The server validates the kind
    against an allowlist (only ``session.attention``), broadcasts it via the
    user-events channel, and hands it to the push dispatcher along with the
    devices that channel already reaches. Back-compat: an old server
    WARN-counts the unknown control type and drops it; an old agent simply
    never sends it (no pushes, previous behaviour).
  * **Presence gating is per device, not per account.** A client may pass its
    registered ``device_id`` as a query param on ``/ws/user-events``; the
    server accepts it only when it matches one of that user's own non-revoked
    device rows, and treats it purely as a delivery hint (an absent or
    unrecognised value is logged and the socket serves events as before —
    it simply cannot suppress anything). The dispatcher then pushes to every
    registered device **except** those holding such a socket, so WebSocket
    delivery still wins for a connected device while a second, sleeping device
    is not muted by it. Gating on the account instead — the original CLA-106
    rule — meant one always-on client (Android's foreground service) silenced
    pushes to every other device the user owned.
  * **Push dispatch** (server→APNs, not a wire-protocol surface but recorded
    for the delivery contract): alert push (never silent), one per targeted
    device of the user, ``apns-collapse-id`` = ``thread-id`` = ``session_id``
    (matches the clients' notification identifier so replace/suppress/
    reconcile semantics hold), ``aps.alert`` title/body from the shared
    attention content map, custom data ``session_id`` + ``agent_id`` for the
    tap deep link. Requires ``APNS_KEY_PATH`` / ``APNS_KEY_ID`` /
    ``APNS_TEAM_ID`` / ``APNS_TOPIC`` on the server; unconfigured → dispatch
    disabled, WebSocket-only delivery (no errors).
  * **REST**: ``POST /api/devices/{device_id}/push-token`` ``{"platform":
    "ios", "token": <hex>, "environment": "sandbox"|"production"}`` → ``204``
    (``404`` when the device isn't the caller's or is revoked); ``DELETE`` on
    the same path clears the stored token (sign-out). ``mobile_devices``
    gains nullable ``platform`` / ``push_token`` / ``push_environment``
    columns; ``MobileTokenRequest`` and the pairing-claim body gain an
    optional ``platform`` field (iOS sends ``"ios"``; Android sends nothing —
    server-side optional). A cross-tenant device reassign, a device revoke,
    and an APNs ``410 Unregistered``/``BadDeviceToken`` result all clear the
    stored token.
* Server-synced saved keyboard shortcuts (CLA-137 — **additive, no version
  bump**; ``central_server`` + clients only, the agent protocol is untouched).
  Persistent named chord buttons per session kind, shared across the user's
  devices — the server stores ``kind`` (SessionKind wire name) and
  ``chord_token`` (the client's ``KeyChord.token()`` string) opaquely and
  never interprets either.
  * New user-event kinds (server-originated, same envelope as ``template.*``):
    ``shortcut.created`` / ``shortcut.updated`` (full shortcut row:
    ``{"shortcut_id", "kind", "chord_token", "name"?, "position",
    "created_at", "updated_at", "version"}``) and ``shortcut.deleted``
    ``{"shortcut_id", "version", "updated_at"}``. Clients ignoring unknown
    kinds (both apps) make this a no-op for older builds.
  * **REST** (dashboard/user JWT, non-admin): ``GET /api/shortcuts`` →
    list; ``POST /api/shortcuts`` ``{"kind", "chord_token", "name"?}`` →
    ``201`` — an **upsert**: re-saving an existing live (kind, chord_token)
    updates its name (when provided) instead of duplicating, still ``201``;
    ``PATCH /api/shortcuts/{id}`` ``{"name"}`` renames — the field is
    required (an empty body ``{}`` → ``422``, it must not silently clear);
    ``""`` (or explicit null) clears the name back to null (the Android JSON
    encoder omits null defaults, so the empty string is the wire's "clear"
    sentinel); ``DELETE`` → ``204`` soft-delete. Ownership violations →
    ``403`` — and since the ownership probe filters soft-deleted rows, a
    **repeat** ``DELETE`` of an already-deleted shortcut also answers
    ``403``, not ``204``. DELETE is therefore **not idempotent**: a client
    whose UI lets the same row be removed twice must treat ``403``/``404``
    on an id from its own list as "already gone" rather than reporting a
    failure the user would rightly disbelieve.

## proto-v5 (superseded by proto-v6)

* Introduced: 2026-06-21
* App support: ``APP_PROTOCOL_VERSION = 5`` (Android), ``PROTOCOL_VERSION = 5``
  (Python — both ``mobaide_agent`` and ``central_server``).
* Change vs proto-v4: **additive** (no frame-layout or existing-shape break).
  Foundation for managing the **Codex CLI** as a second coding agent alongside
  Claude (CLA-68). The version is bumped so the three artifacts stay in lockstep
  and ``protocol-integrity`` has a single authoritative integer to assert against.
* Wire shape (delta from proto-v4), all on ``CH_CONTROL`` (forwarded verbatim by
  ``central_server`` — no server logic change):
  * The ``create_session`` control message (and the ``POST /api/sessions`` body
    proxied over ``CH_CONTROL``) gains three optional fields:
    * ``cli`` — ``"claude"`` | ``"codex"``: selects the managed CLI. When
      omitted, the agent infers it from whichever options block is present, so a
      legacy ``claude_options``-only payload still launches Claude
      (forward/backward compatible).
    * ``codex_options`` — object mirroring ``claude_options`` for Codex:
      ``model`` (str|null), ``sandbox`` (``read-only`` | ``workspace-write`` |
      ``danger-full-access``), ``approval`` (``untrusted`` | ``on-request`` |
      ``never``), ``reasoning_effort`` (``minimal`` | ``low`` | ``medium`` |
      ``high`` | null), ``full_auto`` (bool — Codex
      ``--dangerously-bypass-approvals-and-sandbox``), ``initial_prompt``
      (str|null). Exactly one of ``claude_options`` / ``codex_options`` is
      honoured per session.
  * The single-session wire dict (``session_to_wire_dict``) ``kind`` field gains
    a new value ``"codex"`` (alongside ``claude`` / ``auto_scan`` / ``shell``).
    The Android app models ``kind`` as a typed ``SessionKind`` enum and now
    carries a ``Codex`` member (CLA-69), so it decodes a v5 session directly
    rather than relying on the proto-5 version gate to reject it. For
    forward-compatibility it also coerces an unknown future ``kind`` to the
    enum's default (``coerceInputValues`` on the event-bus JSON decoder) instead
    of dropping the whole event.
  * ``session_create_failed`` / proxy ``503`` responses for a missing Codex
    binary carry ``codex_cli_missing: true`` + ``searched_paths`` — the exact
    parallel of the existing ``claude_cli_missing`` marker.
  * Codex auth/login (CLA-71 — **additive, no version bump**): when Codex is
    installed but not signed in, the agent guards the spawn path and returns a
    ``codex_auth_required: true`` marker on session create — HTTP ``409`` (direct
    REST: marker inside a dict ``detail``; relay proxy: top-level in the body),
    and top-level on the ``session_create_failed`` control message. ``409`` (not
    ``401``) so the Android app-JWT interceptor's refresh-retry-on-401 can't
    swallow the marker before the client reads it. Parallel to
    ``codex_cli_missing``. A new authenticated endpoint ``POST /api/codex/login``
    (body ``{"api_key": "<secret>"}``) runs ``codex login --with-api-key`` (key
    fed on stdin, never argv / never persisted by the agent) and returns
    ``{"authenticated": true}`` on success, or ``400`` with a sanitized detail on
    a rejected key (also non-``401`` for the same interceptor reason). The app
    shows a login prompt on the marker, then retries the create.
  * CLI-availability advertisement (**additive, no version bump**): the
    ``capabilities`` field on ``GET /api/version`` — previously always ``{}`` —
    now carries ``{"cli": {"<cli>": {"available": <bool>}, …}}`` with one entry
    per registered managed CLI (e.g. ``claude``, ``codex``). ``available``
    reflects whether that CLI's binary resolves on the agent host via the same
    override → env → PATH → fallbacks resolution a real spawn uses. Emitted
    identically by the direct FastAPI handler and the relay proxy path (one
    shared builder). The Android client parses it tolerantly — an absent /
    old-agent / malformed value is treated as available (optimistic) — and the
    create-session ``503`` ``<cli>_cli_missing`` / ``409`` ``<cli>_auth_required``
    markers remain the authoritative backstop. Drives the mode-select screen's
    per-CLI button enablement. (Later extended — see the proto-v7 additive delta
    of 2026-08-23 for the ``models`` / ``model_details`` keys an entry may also
    carry.)
* Out of scope (downstream issues): Android create-session UI for Codex
  (CLA-69, done), terminal control-key / approval affordances (CLA-70, done).
  The agent defaults Codex sessions to a non-interactive sandboxed preset
  (``--sandbox workspace-write --ask-for-approval never``) and cannot yet
  mediate in-session Codex approval prompts. Further Codex-auth work is tracked
  downstream: device-code login (CLA-79), mid-session re-auth (CLA-80),
  credential-validity probing (CLA-81).

## proto-v4 (superseded by proto-v5)

* Introduced: 2026-06-09
* App support: ``APP_PROTOCOL_VERSION = 4`` (Android), ``PROTOCOL_VERSION = 4``
  (Python — both ``mobaide_agent`` and ``central_server``).
* Change vs proto-v3: **additive** (no frame-layout or existing-shape break).
  Adds hook-driven session-attention notifications (CLA-64). The version is
  bumped so the three artifacts stay in lockstep and ``protocol-integrity``
  has a single authoritative integer to assert against.
* Wire shape (delta from proto-v3), all on ``CH_EVENTS`` (forwarded verbatim
  by ``central_server``):
  * New event type ``session.attention`` — payload ``{"session": <session dict>}``.
    Emitted when a Claude Code hook reports the session needs the user:
    ``attention`` field is one of ``awaiting_permission`` (permission prompt),
    ``awaiting_os_permission`` (the agent's macOS startup-stall watchdog: a
    spawned child producing no output is most likely blocked on an invisible
    host-desktop TCC file-access modal — additive within proto-v4, no version
    bump), ``turn_finished`` (your move — see the attention-gate delta below),
    or ``ended`` (SessionEnd). It is a one-shot notification trigger; clients
    that miss it (the in-process ``EventBus`` drops on a full queue) still
    recover from the snapshot field below. Older clients ignore the unknown
    type (forward-compatible).
  * The single-session wire dict (``session_to_wire_dict`` — used by
    ``sessions.changed``, ``session.updated``, the ``/ws/sessions`` snapshot and
    the ``/sessions`` REST list) gains two nullable fields:
    ``attention`` (``awaiting_permission`` | ``awaiting_os_permission`` |
    ``turn_finished`` | ``ended`` | ``null``) and ``exit_reason`` (human-readable end cause from the Claude Code
    ``SessionEnd`` ``reason``, or ``process_exited`` on an unexpected death).
    These let a reconnecting/foregrounding client resync the "awaiting you"
    state after a missed one-shot event.
  * Detection source: the agent spawns interactive Claude sessions with a
    per-session ``--settings`` file wiring ``http`` hooks to a local
    ``POST /internal/hook`` endpoint (loopback + per-agent token guarded); the
    old terminal-buffer ``PromptDetector`` heuristic is retired.
* Additive delta within proto-v4 (2026-08-03, attention gate): **agent-side
  only — no wire change, no version bump.** The reason enum, the
  ``session.attention`` event and both snapshot fields are untouched; what
  changes is *when* the agent raises ``turn_finished``.
  * Claude Code's ``Stop`` fires once per model turn, including every
    re-invocation a completing background task or subagent causes, so mapping it
    straight to ``turn_finished`` alerted N+1 times for a session that fanned out
    N background agents. It is now debounced and deduped by
    ``mobaide/core/attention_gate.py`` before it reaches
    ``TerminalSession.set_attention``: a ``Stop`` whose payload reports
    ``stop_hook_active``, in-flight ``background_tasks``, or pending
    ``session_crons`` is dropped, and ``prompt_id`` dedupes repeat stops within
    one turn. A suppressed hook never writes the public ``attention`` field,
    so a client resyncing from a snapshot cannot resurrect a withheld alert.
  * The per-session settings file now registers ``Notification``
    (``permission_prompt`` **and** ``idle_prompt``), ``Stop``,
    ``UserPromptSubmit`` and ``SessionEnd``, and bakes a ``hook=<kind>`` query
    param into each URL alongside ``session_id``/``token`` so the endpoint
    classifies without parsing the body. ``idle_prompt`` is the backstop that
    still alerts when the payload-driven suppression was wrong.
    ``UserPromptSubmit`` is the turn boundary: it resets the gate and clears
    the public ``attention`` field, so a turn answered on the host cannot
    leave a stale badge that mutes later turns.
  * ``set_attention`` gained an equality guard: re-raising the reason already
    pending refreshes state without re-emitting the one-shot event. A changed
    ``exit_reason`` still emits, so the second ``ended`` that a ``/clear``
    produces (``SessionEnd`` reason ``clear``, PTY still running) still
    reaches clients.
  * **Registration constraint** (``hook_settings.REGISTERED_HOOK_EVENTS``):
    Claude Code strips an unrecognised ``hooks.<Event>`` key with a validation
    *warning* and raises a blocking invalid-settings dialog for any validation
    record when interactive, so registering an event newer than the oldest
    supported CLI would hang every session at a modal. Matcher values and
    optional payload fields are safe; new event keys are not.
* Additive delta within proto-v4 (2026-06-12, Send-button submit fix): the
  PTY frame set on ``/ws/session/<id>`` and the ``CH_SESSION_IO`` channel
  gains one C->S type and one semantic change. No version bump: the frame
  *layout* (1 type byte + payload) is unchanged, the new type is purely
  additive, ``central_server`` forwards ``CH_SESSION_IO`` payloads opaquely,
  and the app + agent ship together pre-production.
  * New C->S frame ``0x6d`` / ``'m'`` (**submit**): the agent writes the
    payload to the PTY, pauses ``SUBMIT_CR_DELAY_SECONDS`` (0.2 s), then
    writes CR as a **separate** PTY write. Ink TUIs (Claude CLI) treat a CR
    embedded in the same stdin chunk as pasted content; only a CR arriving
    as its own later read registers as the Enter key. Frames with an empty
    payload are dropped — a bare Enter could submit stale composer text.
  * Changed ``0x78`` / ``'x'`` (**execute**) semantics: the agent now ends
    the sequence with the same delayed CR (Ctrl+C → 0.1 s pause → payload →
    0.2 s pause → CR). Clients no longer append a trailing ``\r`` to submit
    or execute payloads; an old client that still sends ``text\r`` gets a
    harmless extra Enter on an empty composer.

## proto-v3 (superseded by proto-v4)

* Introduced: 2026-05-23
* App support: ``APP_PROTOCOL_VERSION = 3`` (Android), ``PROTOCOL_VERSION = 3``
  (Python — both ``mobaide_agent`` and ``central_server``).
* Breaking change vs proto-v2: the reconnect replay handshake is now
  **chunked** and explicitly terminated. Before v3 the handshake was
  ``state → 'b' → 'o'(entire-ring)`` and the client treated the first
  ``'o'`` after the ``'b'`` marker as both the only replay frame and the
  trigger to flip back to *Live*. That made it impossible to replay more
  than one frame worth of history without the client dropping the
  *ReplayingBuffer* overlay prematurely, which in turn forced the server
  ring buffer to stay tiny (``256 KB``) and caused visible truncation of
  long sessions on reconnect. proto-v3 introduces a new frame type
  ``FRAME_REPLAY_END`` (byte ``0x65`` / ``'e'``) and changes the handshake
  to ``state → 'b' → 'o'(chunk_0) → 'o'(chunk_1) → … → 'e'``. The server
  ring buffer default is also bumped to ``4 MB`` (configurable per session
  via ``SessionConfig.ring_buffer_bytes``); chunk size is ``64 KB``.
* Wire shape (delta from proto-v2):
  * Reconnect handshake on ``/ws/session/<id>`` and on the
    ``CH_SESSION_IO`` envelope channel through ``central_server``:
    ``state-frame → FRAME_REPLAY → N × FRAME_OUTPUT(chunk) → FRAME_REPLAY_END
    → (live frames…)``. ``N >= 1`` always; an empty ring still emits one
    empty ``FRAME_OUTPUT`` between the markers so clients can rely on at
    least one output frame inside the replay span.
  * Mobile clients now hold *ReplayingBuffer* state until ``'e'`` is
    received; chunked ``'o'`` frames in between are appended without state
    transitions.
  * ``incompatible`` rejection frame (server → agent, text, immediately
    before the ``4002`` close) now carries an additional ``manifest_url``
    field: ``{type:"incompatible", reason, min_client_version,
    server_protocol_versions, manifest_url}``. The value is a relative
    path (``/api/v1/agent/manifest.json``) the agent resolves against the
    relay's HTTPS origin. The agent's HTTPS-bootstrap path uses it to
    fetch the signed manifest out-of-band, breaking the chicken-and-egg
    in which the rejected protocol version is also the channel that
    would deliver the manifest pointer the agent needs to update past
    it. The signature remains the trust anchor — a misleading
    ``manifest_url`` can at worst force a fail-closed
    ``signature_failed`` outcome.

### HTTPS-bootstrap auto-update path (proto-v3+)

In addition to the in-band manifest delivery (``auth_ok`` and
``agent_manifest_update`` control frames), the agent reaches the same
signed manifest over plain HTTPS at
``GET /api/v1/agent/manifest.json`` on the central server. The bootstrap
fetch is wired on three triggers:

* **Startup pre-flight** — the lifespan calls
  ``run_https_update_check`` before the first relay connect, so an
  outdated agent self-heals during cold boot.
* **Protocol-incompatible recovery** — on close-code ``4002`` the
  relay's connect loop fires a callback that runs
  ``run_https_update_check(force=True)`` against the
  ``manifest_url`` hint from the rejection payload (falling back to
  the URL derived from ``RELAY_SERVER_URL``).
* **Periodic poll** — a 60-minute background task reconciles any
  manifest the relay channel missed (queue saturation, missed
  rebroadcast, etc.).

The HTTPS bootstrap is the floor below the relay-driven path. As long
as a deployed agent has shipped with the bootstrap and a pinned trust
key (``<prefix>/trust/central_server.pub``), any future
``PROTOCOL_VERSION`` bump is recoverable without operator
intervention.

## proto-v2 (superseded by proto-v3)

* Introduced: 2026-05-13; retired: 2026-05-23.
* App support: ``APP_PROTOCOL_VERSION = 2`` (Android), ``PROTOCOL_VERSION = 2``
  (Python — both ``mobaide_agent`` and ``central_server``).
* Breaking change vs proto-v1: the signed manifest **body** is now delivered
  inline over the relay WebSocket (in ``auth_ok`` and ``agent_manifest_update``
  control frames). The previous design carried only a relative URL pointer
  (``/api/v1/agent/manifest.json``) and required the agent to fetch the
  manifest over HTTP — but the agent treated any ``/``-prefixed value as a
  local filesystem path, so the user-facing "Install now" path always failed
  with ``signature_failed`` (FileNotFoundError → fail-closed → wrong error
  message). proto-v2 hands the body directly to ``verify_signature``.
* Wire shape:
  * Auth handshake (agent ↔ central_server, text frame):
    * Agent sends ``{type:"auth", agent_id, token, protocol_version,
      agent_version, capabilities}``.
    * Server replies ``{type:"auth_ok", server_protocol_versions,
      min_client_protocol_version, server_version, capabilities,
      latest_agent_version, manifest_body, manifest_signature}`` OR closes
      with code ``4002`` and emits ``{type:"incompatible", reason,
      min_client_version, server_protocol_versions}``.
      ``manifest_body`` is the full signed JSON body
      (``version``, ``tarball_url``, ``tarball_sha256``, ``wheel_url``,
      ``wheel_sha256``, ``released_at``, …), exactly the bytes the release
      pipeline Ed25519-signed.
  * Push (server → agent, CH_CONTROL): same shape as ``auth_ok`` for the
    manifest fields — ``{type:"agent_manifest_update", version,
    manifest_body, manifest_signature}``.
  * Mobile ↔ agent (text frame, after auth + before binary handshake):
    ``{type:"server_info", agent_version, protocol_version,
    supported_protocol_versions, capabilities}``.
  * REST: ``GET /api/version`` returns ``{name, server_version,
    protocol_versions, min_client_version, capabilities,
    latest_agent_version}``. The manifest body / signature is intentionally
    NOT exposed here — agents receive it on the relay tunnel; install scripts
    fetch ``GET /api/v1/agent/manifest.json`` directly during bootstrap.

## Application-level WebSocket close codes

The following codes are used in the application-private range (4000–4999).
They are version-independent (not tied to any ``protocol_version`` integer)
and apply to every current proto-v revision.

| Code | Constant | Direction | Semantics |
|------|----------|-----------|-----------|
| 4001 | ``AUTH_FAILED_CLOSE_CODE`` | server → client | JWT missing, expired, or invalid. Android treats this as a session-expiry trigger (force logout). |
| 4002 | ``PROTOCOL_INCOMPATIBLE_CLOSE_CODE`` | server → agent | Agent's ``protocol_version`` is not in the server's ``SUPPORTED_PROTOCOL_VERSIONS``. The relay never authenticates — but the agent's HTTPS-bootstrap path (see "HTTPS-bootstrap auto-update path" in the proto-v3 section) fetches the signed manifest from ``/api/v1/agent/manifest.json``, stages the new release, and the supervisor respawns the agent on the upgraded binary. The relay's reconnect-backoff jumps to ``reconnect_max_delay`` on this code so the per-60s log churn stops while the binary swap is in flight. |
| 4010 | ``WS_CLOSE_ADMIN_REVOKED`` | server → client/agent | Admin permanently deleted the user or unregistered the agent, **or** the user self-scheduled deletion via ``POST /api/auth/me/delete`` (CLA-61) and the server is tearing the live session down before committing the schedule. **Also** emitted by the auth dependencies (`ws_authenticate_user`, `ws_authenticate_mobile`) when a still-cryptographically-valid JWT references a user/agent that has been cascade-deleted — so the offline-then-reconnect logout path renders the same "account removed" message as the synchronous teardown. The CLA-61 sweeper also fires this code at final-purge time so any reconnected client on a stale token is forced out. All associated sessions are terminated and credentials are invalid. Android displays a user-visible "account removed" snackbar and permanently suppresses auto-reconnect. |
| 4011 | ``WS_CLOSE_ADMIN_FORCE_DISCONNECT`` | server → client/agent | Admin issued a non-destructive force-disconnect. Credentials remain valid; the client/agent **may** reconnect. Deliberately distinct from 4001 so Android does not treat this as an auth failure. |
| 4012 | ``WS_CLOSE_AGENT_TRANSIENT_OFFLINE`` | server → mobile client | The mobile client tried to attach to a per-agent relay endpoint while the agent's tunnel was not currently connected (transient: agent restart, crash, network blip, deploy). Also emitted to paired mobile clients when an agent's tunnel disconnects mid-session. Credentials remain valid; the client **must** continue its exponential-backoff reconnect loop. Deliberately distinct from 4010 so Android does not treat a transient agent restart as a destructive admin-revoke. |

## proto-v1 (superseded by proto-v2)

* Introduced: 2026-04-26; retired: 2026-05-13.
* Carried ``manifest_url`` + ``manifest_signature`` in ``auth_ok`` and
  ``agent_manifest_update``. The agent treated the relative URL as a local
  file path on the apply-now path, producing a fail-closed
  ``signature_failed`` whenever a user tapped "Install now" on a real
  deployment. See proto-v2 above for the fix and the new shape.

## How to bump

1. Increment ``PROTOCOL_VERSION`` and update ``SUPPORTED_PROTOCOL_VERSIONS``
   in **both** ``mobaide_agent/mobaide/relay/envelope.py`` and
   ``central_server/central_server/relay/envelope.py``. The CI
   ``protocol-integrity`` workflow rejects any commit that drifts the two.
2. Update the ``BuildConfig.APP_PROTOCOL_VERSION`` field in
   ``android/app/build.gradle.kts`` to match, the
   ``APP_PROTOCOL_VERSION`` / ``MIN_SERVER_PROTOCOL_VERSION`` values in
   ``ios/Config/Protocol.xcconfig``, and ``APP_PROTOCOL_VERSION`` in
   ``web/src/api/bootstrap.ts``. The ``protocol-integrity`` workflow
   asserts all three ``APP_PROTOCOL_VERSION`` values are equal (and iOS's
   ``MIN_SERVER_PROTOCOL_VERSION`` equals Android's — the web gate is set
   membership against the server's advertised list and carries no floor of
   its own), so a bump that misses a client fails CI.
3. Update ``tests/protocol_golden.json`` to match the new shape; any change
   must update the golden file or CI fails.
4. Add a new section to this file documenting the change.
5. Tag the protocol commit ``proto-v<N>``.

### HTTPS-bootstrap pre-requisite

Before bumping ``PROTOCOL_VERSION``, confirm that **every deployed agent
release in the support window already speaks the HTTPS-bootstrap path**
(this means every release at or after ``agent-v0.2.5``). The bootstrap is
the only channel that can deliver a manifest pointer to an agent whose
``protocol_version`` is below ``MIN_CLIENT_PROTOCOL_VERSION``; an agent
that pre-dates the bootstrap is stranded by any incompatible bump and
must be reinstalled by hand. The release pipeline does not enforce this
gate yet — it is a release-checklist item until the
``test_e2e_autoupdate`` suite grows coverage for the bootstrap path
itself.
