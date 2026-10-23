# Android Changelog

## [3.6.10](https://github.com/syrax90/claude_mobile/compare/android-v3.6.9...android-v3.6.10) (2026-10-19)


### Features

* add Focus view toggle and collapsible Advanced group to session launch ([eff1cec](https://github.com/syrax90/claude_mobile/commit/eff1cecbc3b109044c58ba5b54a328de4a611e30))
* add Focus view toggle and collapsible Advanced group to session launch ([166acfa](https://github.com/syrax90/claude_mobile/commit/166acfa071e61ba1a4a35bddea8ac8ba0ffaf8b3))
* sessions panel for switching sessions from the terminal (Android, iOS, web) ([81d847d](https://github.com/syrax90/claude_mobile/commit/81d847d6f67a83c9ea09a8bb125156b0ee684e44))
* sessions panel for switching sessions from the terminal (Android, iOS, web) ([87e572f](https://github.com/syrax90/claude_mobile/commit/87e572fa192a64c7a466d1447596d874cf5f0ee6))


### Bug Fixes

* keep the sessions list beneath a session created from the drawer; stay silent on a signed-out switch failure ([2fbdf72](https://github.com/syrax90/claude_mobile/commit/2fbdf72e1c4cc876951e1bcb88115a336ee9169b))
* refuse to persist agent credentials after a mid-switch sign-out ([9fa31a5](https://github.com/syrax90/claude_mobile/commit/9fa31a5334a1c23e7b884eccf21e429b1c76c1a4))

## [3.6.9](https://github.com/syrax90/claude_mobile/compare/android-v3.6.8...android-v3.6.9) (2026-10-17)


### Features

* suggest existing folders while typing project and custom paths ([4f13cfc](https://github.com/syrax90/claude_mobile/commit/4f13cfc1e729eb026f982d3f991a3b8976153e9f))
* suggest existing folders while typing project and custom paths ([6f2bb9e](https://github.com/syrax90/claude_mobile/commit/6f2bb9e1e5db9b14e066e3986031ae4d2ce1f7ea))


### Bug Fixes

* **android:** auto-scroll terminal selection when dragging a handle to an edge ([20b2274](https://github.com/syrax90/claude_mobile/commit/20b22745af9afb0a8103c5d42d4722c7cb17c86b))
* **android:** throttle selection edge autoscroll to the tick pace ([dae6504](https://github.com/syrax90/claude_mobile/commit/dae65049441846d1dbd16db321e4132fe4848ebf))
* close folder suggestions on Done and word paths for Windows agents ([78740a9](https://github.com/syrax90/claude_mobile/commit/78740a9ffb2271259eaf64c26d9388fb23a6496b))
* close folder suggestions on Done and word paths for Windows agents ([f1ccacc](https://github.com/syrax90/claude_mobile/commit/f1ccacce058bf59f4aa869926e5bb0de8b65abf3))

## [3.6.8](https://github.com/syrax90/claude_mobile/compare/android-v3.6.7...android-v3.6.8) (2026-10-15)


### Features

* let removing an agent also uninstall it from its host ([f2addc4](https://github.com/syrax90/claude_mobile/commit/f2addc424dd34644e34000a360705497431b03d8))
* let removing an agent also uninstall it from its host ([51d77ac](https://github.com/syrax90/claude_mobile/commit/51d77ac05d19d51a39754bf8f7e94a57984ae25f))


### Bug Fixes

* stop a retained hidden client from resizing the PTY when another client attaches ([cb21cfb](https://github.com/syrax90/claude_mobile/commit/cb21cfbf09742c4874446d8c1555a54a855f80c4))
* stop a retained hidden client from resizing the PTY when another client attaches ([e134336](https://github.com/syrax90/claude_mobile/commit/e134336abbab00bfabfc9cecdac56d79fe9eef10))

## [3.6.7](https://github.com/syrax90/claude_mobile/compare/android-v3.6.6...android-v3.6.7) (2026-09-30)


### Features

* show a magnifier while dragging terminal selection handles ([47d6529](https://github.com/syrax90/claude_mobile/commit/47d65294fbfcd4f0c27a752018c7a384db0a738a))
* show a magnifier while dragging terminal selection handles ([dc72c6a](https://github.com/syrax90/claude_mobile/commit/dc72c6af2a6cfa1f0171cbd31bf4117e32a9544a))

## [3.6.6](https://github.com/syrax90/claude_mobile/compare/android-v3.6.5...android-v3.6.6) (2026-09-22)


### Features

* **agent,android,ios,web:** delete remote branches, remove worktrees and show branches as an A→Z folder tree on the project Git screen ([fec2fab](https://github.com/syrax90/claude_mobile/commit/fec2fabf8405ae14153bc38d91c4874081c7682e))
* **agent,android,ios,web:** delete remote branches, remove worktrees and show branches as an A→Z folder tree on the project Git screen ([1f6de49](https://github.com/syrax90/claude_mobile/commit/1f6de49d16b956b0bf1e1ed0ede6f1a5b300202a))
* **agent,android,ios,web:** fetch, pull and push as background jobs with streamed progress from the in-session Changes screen ([20bd4f1](https://github.com/syrax90/claude_mobile/commit/20bd4f1d42a78c8b27d98f4ecef3899ad982c225))
* **agent,android,ios,web:** fetch, pull and push as background jobs with streamed progress from the in-session Changes screen ([f72f22d](https://github.com/syrax90/claude_mobile/commit/f72f22d5c5885885c796b6e5fdc82b58e3228073))
* **agent,android,ios,web:** manage branches from the project Git screen — remote branches, create and check out ([97fe98f](https://github.com/syrax90/claude_mobile/commit/97fe98f6481a1f576e54663bac8c8dafff7a2815))
* **agent,android,ios,web:** manage branches from the project Git screen — remote branches, create and check out ([e5c5953](https://github.com/syrax90/claude_mobile/commit/e5c59537944ae94f2b18f68d6a2bddf43a1725c6)), closes [#677](https://github.com/syrax90/claude_mobile/issues/677)
* **agent,android,ios,web:** rebase onto a branch with continue/abort driven by the reported repo state ([c68e318](https://github.com/syrax90/claude_mobile/commit/c68e31897e3def04ef560534c30ae9dd4474b498))
* **agent,android,ios,web:** rebase onto a branch with continue/abort driven by the reported repo state ([f6db316](https://github.com/syrax90/claude_mobile/commit/f6db316b2fecaf619716421b1e960e383765a2a2)), closes [#678](https://github.com/syrax90/claude_mobile/issues/678)
* **agent,android,ios,web:** resolve rebase conflicts in the app with a whole-file editor, whole-file picks and Restore conflict ([f411e87](https://github.com/syrax90/claude_mobile/commit/f411e87cd7698961fc9cc0a36de9cd86a475e6e5))
* **agent,android,ios,web:** resolve rebase conflicts in the app with a whole-file editor, whole-file picks and Restore conflict ([58fca0e](https://github.com/syrax90/claude_mobile/commit/58fca0e1e461cfeaf644715ac12758fe700cad4a))
* **agent,android,ios,web:** stage, unstage, discard and commit from the in-session Changes screen ([0721ebb](https://github.com/syrax90/claude_mobile/commit/0721ebba30bb299eae9c4f05e387f9574d948c5d))
* **agent,android,ios,web:** stage, unstage, discard and commit from the in-session Changes screen ([58ec5bb](https://github.com/syrax90/claude_mobile/commit/58ec5bb165b68a2cb7a64826a7e97371de018e00))


### Bug Fixes

* **agent,android,ios,web:** decide rebase stops from repository state and hide rebase actions without rebase details ([66c7c1c](https://github.com/syrax90/claude_mobile/commit/66c7c1c3df50847354a3d1b4a6fd3301ac685926))
* **agent,android,ios,web:** offer Restore conflict only at a live conflict stop, cap the resolver's write on the encoded bytes and warn when a saved draft was overwritten ([53dabf1](https://github.com/syrax90/claude_mobile/commit/53dabf1f79a513487b2611f85f7cf89e3b8a3f17))
* **agent,android,ios,web:** pick rebase targets by full ref and list branches by exact name ([49480d9](https://github.com/syrax90/claude_mobile/commit/49480d9808da111b162695838aa1aa80e85d5c82))
* **agent,android,ios,web:** recreate a pruned upstream on push and hide pull follow-ups without one ([1d15359](https://github.com/syrax90/claude_mobile/commit/1d153598c5b5deb31af45b2be331f94ffef4d145))
* **agent,android,ios:** keep the conflict resolver's delete pick inside the repository and never offer a submodule conflict for restore ([a814b88](https://github.com/syrax90/claude_mobile/commit/a814b883d3ccbb957aedff67842d34e3f7c2a792))
* **android,ios,web:** offer only branch-backed rows as a new branch's start point ([739c131](https://github.com/syrax90/claude_mobile/commit/739c131ed3af95ecb3e8908baa881985306c9fbf))
* **android:** drop vanished rows from the Changes selection source, not only its projection ([59389d6](https://github.com/syrax90/claude_mobile/commit/59389d63ea507ef3870effd621cc6c7a5f23b966))

## [3.6.5](https://github.com/syrax90/claude_mobile/compare/android-v3.6.4...android-v3.6.5) (2026-09-14)


### Features

* **agent,android,ios,web:** raise the diff ceiling and add a Full file switch to Git Changes ([2e9ac53](https://github.com/syrax90/claude_mobile/commit/2e9ac53fcea55d382e6daafffbe984a5adaa9034))
* **agent,android,ios,web:** raise the diff ceiling and add a Full file switch to Git Changes ([41758ac](https://github.com/syrax90/claude_mobile/commit/41758ac473dec3772ab8c3c32182e2c1d8b33ced))
* **agent:** add session-scoped read-only git status/diff API ([0f87c33](https://github.com/syrax90/claude_mobile/commit/0f87c338516de5f455c042e4061a717ac1229869))
* **android:** add in-session Git Changes and Diff screens behind a terminal overflow menu ([6bfff1c](https://github.com/syrax90/claude_mobile/commit/6bfff1cb01cf165365c30a865b1f664d393f1866))


### Bug Fixes

* **agent:** address Codex review of the Android git Changes viewer ([f396277](https://github.com/syrax90/claude_mobile/commit/f396277b28723e9851f0b68032c7abbf8cb79900))
* **android,ios:** measure the widest diff line for the shared horizontal scroll width ([9232738](https://github.com/syrax90/claude_mobile/commit/9232738ef6fa22984e49671a1d442efe3545a851))
* **android,ios:** report the shipped entry count in the truncated Changes notice ([6f380e2](https://github.com/syrax90/claude_mobile/commit/6f380e2de4528571aac94b5fabf8358954fabed9))
* bound the status parse and make Changes list keys collision-free ([e92e1b2](https://github.com/syrax90/claude_mobile/commit/e92e1b20bdd057463a712a75c7821ad5be06e970))
* **web,android,ios:** hide the diff Context control when it can't change the diff ([ae0633b](https://github.com/syrax90/claude_mobile/commit/ae0633b123a7981fdac0e4ec100b40bc9f487c4e))

## [3.6.4](https://github.com/syrax90/claude_mobile/compare/android-v3.6.3...android-v3.6.4) (2026-09-04)


### Features

* **android:** log the Firebase agent_paired conversion once per account ([f59eb55](https://github.com/syrax90/claude_mobile/commit/f59eb552759b8b2e79308825c577cf66847fe764))
* in-app "Agent paired" conversion via Firebase Analytics (Stage 5) ([50a74de](https://github.com/syrax90/claude_mobile/commit/50a74dea63f69efbc447dee2252ef22b2b30604e))


### Bug Fixes

* **android:** arm the conversion window at launch, before any sign-in ([0303914](https://github.com/syrax90/claude_mobile/commit/03039145cd237ef32c3de2731e758268adc723bf))
* **android:** keep the conversion state out of backups and device transfers ([e6feda1](https://github.com/syrax90/claude_mobile/commit/e6feda1792bd09b3bd556e68873869d383af9a86))

## [3.6.3](https://github.com/syrax90/claude_mobile/compare/android-v3.6.2...android-v3.6.3) (2026-08-30)


### Bug Fixes

* **android,ios,web:** never report a paired, online agent as a failed pairing ([efabb00](https://github.com/syrax90/claude_mobile/commit/efabb00ef2ab51219253b1ee17b71e0d80bbfef2))
* **android,ios,web:** never report a paired, online agent as a failed pairing ([8e83bfd](https://github.com/syrax90/claude_mobile/commit/8e83bfd82c03fd1cf4594e997afb50c68bc48389))
* **android:** let the "No agents yet" state scroll when it overflows ([7f09308](https://github.com/syrax90/claude_mobile/commit/7f093082fd056d4b1664748480b0f9a02233ebfa))
* **ios,android:** show the "No agents yet" actions as ordered steps ([9b49a42](https://github.com/syrax90/claude_mobile/commit/9b49a425e68b14f6a74cb8c2dcc25110c73d3c5f))

## [3.6.2](https://github.com/syrax90/claude_mobile/compare/android-v3.6.1...android-v3.6.2) (2026-08-28)


### Features

* **android:** add Auto/Light/Dark theme picker and pixel window chrome ([0d09ff8](https://github.com/syrax90/claude_mobile/commit/0d09ff8bfeeae25f6ad2c71b0bea4c8de7eaa071))
* **android:** add modern-pixel theme foundation and component kit ([7df371c](https://github.com/syrax90/claude_mobile/commit/7df371cc29aac25f1439a5820f3a7269e6dba0d6))
* **android:** adopt the modern-pixel design system from the web app ([2b36f59](https://github.com/syrax90/claude_mobile/commit/2b36f59e6028b1add407b01c76459640f203c1dd))
* **android:** apply pixel treatment to status dots, chips, cards, banners ([7b07338](https://github.com/syrax90/claude_mobile/commit/7b07338a89a0f8f5ea2b0956a7d8b30d228d77a0))
* **android:** migrate screens to pixel scaffold, bars, dialogs, buttons ([8fd943c](https://github.com/syrax90/claude_mobile/commit/8fd943c665934e3ab89fb9acca6030a832e4d17e))
* **android:** pixel mode cards, segments, switches, fields, key bar ([21cfa28](https://github.com/syrax90/claude_mobile/commit/21cfa28b8108a72207fc3a0808761d9575ebbd4a))


### Bug Fixes

* **android:** center the "Scan the pairing QR" empty-state label ([fbac152](https://github.com/syrax90/claude_mobile/commit/fbac152e0bf3ad368da1d1d2c024041e53f5f5b0))
* **android:** center the "Scan the pairing QR" empty-state label ([bcb0a4a](https://github.com/syrax90/claude_mobile/commit/bcb0a4ac900f56433f1b0d849a55dcc5a0f08eb9))
* **android:** CSS-faithful hard shadows, pixel FABs, radios, 2dp rules ([57e6c99](https://github.com/syrax90/claude_mobile/commit/57e6c9909ffd5426a6a2d4bdec173161c0980d8f))
* **android:** give PixelBlinkCursor indeterminate progress semantics ([8e6cc57](https://github.com/syrax90/claude_mobile/commit/8e6cc578679ac51fafda71c0115199c4c599af21))
* **android:** hard-bound the pre-frame theme read with a 500ms timeout ([a4d92b2](https://github.com/syrax90/claude_mobile/commit/a4d92b21603d890d9a17962c76cb5bc5ef30b680))
* **android:** load saved theme before first frame; document error-container reuse ([30f6d10](https://github.com/syrax90/claude_mobile/commit/30f6d104f39b79295a7793949d684e9b44053298))
* **android:** pixel icon buttons, login app icon, single dropdown labels ([9e7f4ac](https://github.com/syrax90/claude_mobile/commit/9e7f4aceb5099cd2a13d0622484324d5b31dcaee))
* **android:** raster brand mark on sign-in; splash follows the saved theme ([b2993bd](https://github.com/syrax90/claude_mobile/commit/b2993bdf8418b9e75bbd4169d10c47a1b81418e3))
* **android:** resolve saved theme synchronously before the first frame ([5d0d473](https://github.com/syrax90/claude_mobile/commit/5d0d473006eb2200ea5d7e5651b1619f25ba5c76))

## [3.6.1](https://github.com/syrax90/claude_mobile/compare/android-v3.6.0...android-v3.6.1) (2026-08-27)


### Features

* advertise models_pending while the agent's model warmup outlasts the version wait ([e6170d7](https://github.com/syrax90/claude_mobile/commit/e6170d7e83641876c8ef900523e56d63801d8b94))
* **clients:** hide non-actionable account/version details from users ([0986c7b](https://github.com/syrax90/claude_mobile/commit/0986c7b433e2b58f2558930b88fe05b003965d81))
* **clients:** warn that externally started sessions won't appear ([f2a41f8](https://github.com/syrax90/claude_mobile/commit/f2a41f8e3d0a6b62f5dbb55fdb0557e6c116d790))


### Bug Fixes

* **android,ios,web:** reconcile persisted catalog efforts against detail-less agents ([df2393f](https://github.com/syrax90/claude_mobile/commit/df2393f6056a75207e48a5987b9512abaf513f41))
* **android,ios,web:** warn that externally started sessions won't appear ([034f677](https://github.com/syrax90/claude_mobile/commit/034f677d281bfda47e3175794e23b760bf61889d))
* **clients:** hide non-actionable account/version details from users ([bd35511](https://github.com/syrax90/claude_mobile/commit/bd35511ea381ad5b7739eda42f44f33c99d3b890))
* Codex model picker missed current models (gpt-5.6-*) — enumerate without --json, advertise per-model efforts ([7dad428](https://github.com/syrax90/claude_mobile/commit/7dad4285374b5b567fbb56cb7830805d3b41e529))
* harden the codex effort reconcile against races, stale catalogs, and cold caches ([8fd8452](https://github.com/syrax90/claude_mobile/commit/8fd8452cc1f83878d7ad93760592da193aeb210a))
* **mobaide_agent,android,ios,web:** enumerate Codex models without --json, advertise per-model efforts ([bde0c30](https://github.com/syrax90/claude_mobile/commit/bde0c3032fec462ece892c1b94bc4f458499cca1))
* name the session by its label in stop and busy dialogs ([5edb148](https://github.com/syrax90/claude_mobile/commit/5edb1486a35165267e959806fe00b7d8d5253ff4))
* name the session by its label in stop and busy dialogs ([65684b3](https://github.com/syrax90/claude_mobile/commit/65684b37debd5878d5b6cf888f9d0b163a0a2d71))
* scope models_pending to each CLI's own cold cache, retry mobile re-probes on a budget ([bb57197](https://github.com/syrax90/claude_mobile/commit/bb57197116ebbbe3dc7fcbe10f900ea3182b1d3f))

## [3.6.0](https://github.com/syrax90/claude_mobile/compare/android-v3.5.0...android-v3.6.0) (2026-08-13)


### Features

* **android:** allow removing a recent custom path ([5784c31](https://github.com/syrax90/claude_mobile/commit/5784c318d2193c9a199a38a57e0c14558e72ec95))
* **central_server,mobaide_agent,web,android,ios:** reversed device-code pairing + account trust-key pin (CLA-199) ([6e56f2f](https://github.com/syrax90/claude_mobile/commit/6e56f2f00a3ee8e22825ed18b7b13dc3e9d95cd1))
* make Claude Code --debug flag opt-in via toggle ([36af32b](https://github.com/syrax90/claude_mobile/commit/36af32b1122b2dfc6e57dcce88841c14c97e8d7e))
* purge per-agent recent custom paths on sign-out/account deletion (CLA-258) ([8d7b5a3](https://github.com/syrax90/claude_mobile/commit/8d7b5a3a5f1188c9b431a9ae51c4837552030f4e))
* **web,android,ios:** suspend session PTY sockets when unwatched ([9e385c3](https://github.com/syrax90/claude_mobile/commit/9e385c34509e8f725ff265a66c33e56f4abbeb6b))


### Bug Fixes

* **android,ios,mobaide_agent:** follow status-response deadline, canonicalize typed device codes ([0f23c75](https://github.com/syrax90/claude_mobile/commit/0f23c75095c6365b2c8341d324e368fc4b5cfafd))
* **android,ios,web:** stop replayed OSC 52 from hijacking the clipboard on session open ([309fc93](https://github.com/syrax90/claude_mobile/commit/309fc93d93a95cd1f7ed43010b537c356a4a6f0c))
* **android,ios,web:** stop replayed OSC 52 from hijacking the clipboard on session open ([56a58be](https://github.com/syrax90/claude_mobile/commit/56a58be2ce5d3159e1f110e945c864f77e2e1098))
* **android:** allow removing a recent custom path ([1a0045a](https://github.com/syrax90/claude_mobile/commit/1a0045accde24019146a96dd240f1f63e03b8c79))
* **android:** anchor device-code poll deadline to an overridable clock ([e438d10](https://github.com/syrax90/claude_mobile/commit/e438d104e8d95bf06f86c5e449ea856e6d2c6e61))
* **android:** close socket orphaned by pause landing across newWebSocket ([a024912](https://github.com/syrax90/claude_mobile/commit/a02491289837140fe17b093b485d1fa3d5478810))
* **android:** recheck shouldReconnect after URL resolution in doConnect ([8b939e7](https://github.com/syrax90/claude_mobile/commit/8b939e797abdb37bf563e9a5bb53b1f7bac24b6a))
* make Claude Code --debug flag opt-in via toggle ([1fa0e8d](https://github.com/syrax90/claude_mobile/commit/1fa0e8df0713abe5c2852f9dcaf33ba22c96837d))
* **web,android,ios:** fix unwatched-suspend regressions from Codex review ([15f37c7](https://github.com/syrax90/claude_mobile/commit/15f37c7860f1962d3306d32d48af58f342481858))
* **web,android,ios:** re-assert PTY size on client-switch edges ([454c420](https://github.com/syrax90/claude_mobile/commit/454c4208bdcfbd4782ace47ba89338e19156cfd5))
* **web,android,ios:** re-assert PTY size on client-switch edges ([7a5fcf0](https://github.com/syrax90/claude_mobile/commit/7a5fcf0db0b920bbc9c4d8d06645276d9bb4dd82))

## [3.5.0](https://github.com/syrax90/claude_mobile/compare/android-v3.4.0...android-v3.5.0) (2026-08-09)


### Features

* **web,android,ios:** desktop keyboard & shortcuts, shared chord fixture, CLA-158 (CLA-197) ([1157916](https://github.com/syrax90/claude_mobile/commit/115791676121559b70ad5310b3a928bcaa994a42))


### Bug Fixes

* **agent:** stop auto-creating a session for an unknown relay session_id ([a6a6001](https://github.com/syrax90/claude_mobile/commit/a6a60019f88621d51605376bf7784a406131bb8c))
* **agent:** stop auto-creating a session for an unknown relay session_id ([35a433b](https://github.com/syrax90/claude_mobile/commit/35a433bbe65184b8ed93114888bf530407c316fe))
* **android,ios:** don't let the header's REST seed overwrite a live title ([be7326a](https://github.com/syrax90/claude_mobile/commit/be7326a93d48ba045cf3da1a19b349f865f5881f))
* **android,web:** resolve the header label on cold start, drop it on route change ([01e696e](https://github.com/syrax90/claude_mobile/commit/01e696ebcad1e717e3bd3bcccc7d98339cc1c355))
* **ios,android,web:** name the terminal header by session title, not id ([0ea96ff](https://github.com/syrax90/claude_mobile/commit/0ea96ff697d7c9fdb3a9aadcd3c62b030fbe3a0b))
* **ios,android,web:** name the terminal header by session title, not id ([b32a097](https://github.com/syrax90/claude_mobile/commit/b32a097bfd7cfb26472d95e7d12eb67c8d26fa30))
* **ios,android:** disable autocapitalization/autocorrection on verbatim-input fields (CLA-146) ([05910c7](https://github.com/syrax90/claude_mobile/commit/05910c711b82138b1c4be576afebcfde268c102e))

## [3.4.0](https://github.com/syrax90/claude_mobile/compare/android-v3.3.0...android-v3.4.0) (2026-08-03)


### Features

* **web:** auth & account — email/password + Google Sign-In, dual-axis tokens, devices, account deletion (CLA-190) ([7ab4df6](https://github.com/syrax90/claude_mobile/commit/7ab4df654dae97cf326c1679329c8864b53b8606))


### Bug Fixes

* **android:** don't resume follow mode for emulator's own query replies ([6f63df0](https://github.com/syrax90/claude_mobile/commit/6f63df0f5742be2308cd5e2a2f73189728a04de2))
* gate terminal row-tap execute while the display is frozen (CLA-182) ([2396ed5](https://github.com/syrax90/claude_mobile/commit/2396ed554cd6bb043c0332eef07a2ad2b4643b33))
* **mobile:** stop unread-row badge counting scroll events instead of scrollback rows ([64c2328](https://github.com/syrax90/claude_mobile/commit/64c2328419e605b31ea111a8bb83986087e04a06))
* **mobile:** stop unread-row badge counting scroll events instead of scrollback rows ([f77f842](https://github.com/syrax90/claude_mobile/commit/f77f8424e9209c3508ce242ee4943b39ec1db162))
* **terminal:** gate row-tap execute on synchronized/deferred output ([166b8cf](https://github.com/syrax90/claude_mobile/commit/166b8cf32c30ffd063cbb575daddd8413014c061))
* **terminal:** latch the synchronized-output gate at touch-up ([0b825bd](https://github.com/syrax90/claude_mobile/commit/0b825bdf320b6e464a6b590b5beb90801188a8cc))

## [3.3.0](https://github.com/syrax90/claude_mobile/compare/android-v3.2.0...android-v3.3.0) (2026-07-31)


### Features

* **mobile:** make git branch/worktree deletion discoverable ([874942a](https://github.com/syrax90/claude_mobile/commit/874942a74d24b81679a9f7ebcf44ee837dea2a4f))


### Bug Fixes

* **android:** keep a reload from cancelling the in-flight git delete ([696bbe0](https://github.com/syrax90/claude_mobile/commit/696bbe0d874079e81313ce6486cd37bdef0bc27b))
* **android:** keep form fields above the soft keyboard ([cb40fc2](https://github.com/syrax90/claude_mobile/commit/cb40fc2b8e84ffa4d1e2dfbc58e957d13e898b78))
* **mobile:** address Codex review on the paste affordances ([45ed7b5](https://github.com/syrax90/claude_mobile/commit/45ed7b52faafd91c101ccf24817db472a0cf1401))
* **mobile:** allow pasting the verification code ([8f5814c](https://github.com/syrax90/claude_mobile/commit/8f5814cd8bb819bfb8c36eba097e5e38654ad86a))
* **mobile:** stop a terminal tap submitting the message, report it as a click ([684ea5e](https://github.com/syrax90/claude_mobile/commit/684ea5ed21e65c1997c79dc2ade92a885957d040))
* **mobile:** stop a terminal tap submitting the message, report it as a click ([b96c94e](https://github.com/syrax90/claude_mobile/commit/b96c94e09f0b5c20f92cfc2858b5dcc38221cd87))

## [3.2.0](https://github.com/syrax90/claude_mobile/compare/android-v3.1.0...android-v3.2.0) (2026-07-30)


### Features

* **sessions:** remember per-kind launch prefs, live Auto Scan skip-permissions ([7e2721f](https://github.com/syrax90/claude_mobile/commit/7e2721f826346d2512b4dc736f44a49015b8dc3d))


### Bug Fixes

* **android:** commit mode selection on tap, remove Continue button ([28bf9e2](https://github.com/syrax90/claude_mobile/commit/28bf9e22def1de902ab8d7e9cbbc2c98dc20ba20))
* **android:** default Claude skip-permissions toggle to off ([610ea47](https://github.com/syrax90/claude_mobile/commit/610ea47adf456bbdf5c2aa26d764d7031426d1ea))
* **android:** remember per-kind launch prefs, live Auto Scan skip-permissions ([9754e2b](https://github.com/syrax90/claude_mobile/commit/9754e2b694e10aa7759f9b06f8dbf71740240c2c))
* **sessions:** default Claude skip-permissions toggle to off ([4f169dc](https://github.com/syrax90/claude_mobile/commit/4f169dce665ea7ed57f98d4102172c6daf88aaf0))
* **sessions:** don't display a Skip-permissions control Auto Scan can't honour ([6dfcbe2](https://github.com/syrax90/claude_mobile/commit/6dfcbe21ceb33cf959b0a26d33a7079d4e900a4d))
* **sessions:** keep wire-absence default true, force bypass for Auto Scan ([9ad4b69](https://github.com/syrax90/claude_mobile/commit/9ad4b69ba38f5f7be0e030b2d9c71a35777f399f))

## [3.1.0](https://github.com/syrax90/claude_mobile/compare/android-v3.0.1...android-v3.1.0) (2026-07-28)


### Features

* **android:** custom AI agent types — mode-picker cards + editor sheet (CLA-154) ([0ba30d4](https://github.com/syrax90/claude_mobile/commit/0ba30d461487c014b61a9f915e45d3905e73b4a1))
* **android:** custom AI agent types — mode-picker cards + editor sheet (CLA-154) ([517bdcd](https://github.com/syrax90/claude_mobile/commit/517bdcd560b12de9c8d485e99c6c96bc9a89cb68))


### Bug Fixes

* **android:** key custom-launch 404 on relay detail and self-heal MRU recents on kind resolution (CLA-154) ([7d62bba](https://github.com/syrax90/claude_mobile/commit/7d62bbad2b1f355b4bbf17b9b44edb861b086140))
* **android:** scope custom-agent-type editor save completions and gate busy dismissal (CLA-154) ([2971e62](https://github.com/syrax90/claude_mobile/commit/2971e62ed30b722156eb54ef7a807ee2389dca3b))
* **android:** veto sheet dismissal pre-hide and count type-editor limits in code points (CLA-154) ([1dec742](https://github.com/syrax90/claude_mobile/commit/1dec7424340ffb6f71ebe6d1d01ebf7ebee81d70))

## [3.0.1](https://github.com/syrax90/claude_mobile/compare/android-v3.0.0...android-v3.0.1) (2026-07-24)


### Bug Fixes

* **android:** fill terminal height by re-asserting PTY size on connect ([8a5afdb](https://github.com/syrax90/claude_mobile/commit/8a5afdb08e193a3385da6dda648c4ed9ba4966d4))
* **android:** fill terminal height by re-asserting PTY size on connect ([9d3b3a5](https://github.com/syrax90/claude_mobile/commit/9d3b3a568e5e0a5088f80ddcbb7b990da3d79c1d))

## [3.0.0](https://github.com/syrax90/claude_mobile/compare/android-v2.2.0...android-v3.0.0) (2026-07-22)


### ⚠ BREAKING CHANGES

* **android:** onSaveShortcut is now (KeyChord) -> Unit; TerminalViewModel.saveShortcut(chord) drops its name parameter; the never-visible save-success snackbar is gone.

### Features

* add persistent named custom shortcut buttons synced via central server ([8ed089a](https://github.com/syrax90/claude_mobile/commit/8ed089abd66bcaa404559ee4d1840636aa659359))
* add persistent named custom shortcut buttons synced via central server ([04bde6f](https://github.com/syrax90/claude_mobile/commit/04bde6fb0738973d4eadab5ec7398b91823b3d9d))
* **android:** add custom keyboard shortcut chord picker to control-key row ([d89040f](https://github.com/syrax90/claude_mobile/commit/d89040fb48e92c36b4e1be7d13c5239818325d11))
* **android:** add custom keyboard shortcut chord picker to control-key row ([5dbb7c6](https://github.com/syrax90/claude_mobile/commit/5dbb7c6ce9e9e5b0b6359b897527fd11bb1dad91))
* order terminal control keys consistently across session types ([11b0db0](https://github.com/syrax90/claude_mobile/commit/11b0db0d7ef6400eb42b2b8cf14cc9890b300a5b))
* order terminal control keys consistently across session types ([a6de294](https://github.com/syrax90/claude_mobile/commit/a6de2945baf6657bf9a2f3a1fcefad6f954b9397))


### Bug Fixes

* address third Codex review round for saved shortcut buttons (CLA-137) ([740dd2f](https://github.com/syrax90/claude_mobile/commit/740dd2fc018315f0ee179edbfdb4f0ea913787b8))
* allow xhigh/max effort for Claude Sonnet in session wizard ([6b1e0d5](https://github.com/syrax90/claude_mobile/commit/6b1e0d5f1d2b0f0e8041a5448c9e6464ae850edc))
* allow xhigh/max effort for Claude Sonnet in session wizard ([607a771](https://github.com/syrax90/claude_mobile/commit/607a77155d557edbb51c078dbdc0e196dab5aa29))
* **android:** align custom shortcut chips with control-key buttons in InputBar ([92830a2](https://github.com/syrax90/claude_mobile/commit/92830a2d2a85a20c5dad2e9027a794930e579142))
* **android:** center-align control keys and saved shortcut chips in InputBar row ([6a502ee](https://github.com/syrax90/claude_mobile/commit/6a502ee1fb3f3db904577c1dc827087ee585173e))
* **android:** drop concurrent duplicate saves so a removed chord cannot resurrect ([235be35](https://github.com/syrax90/claude_mobile/commit/235be35f0714e2a8ec42301b32ed078778bd276f))
* **android:** gate shortcut picker send surfaces on session-active state ([7eaa975](https://github.com/syrax90/claude_mobile/commit/7eaa975ce3d33208d4a8ad46628c359ba6e6e251))
* **android:** replace the shortcut sheet's double Save with one toggle ([b628ec5](https://github.com/syrax90/claude_mobile/commit/b628ec5d6536e6bf65985333413a4ae23bdbfeb9))
* **android:** replace the shortcut sheet's double Save with one toggle ([7f6c4f6](https://github.com/syrax90/claude_mobile/commit/7f6c4f644b22fd3a0d13876a313711b1656f5d8d))
* **android:** target Android 16 (API 36) for Play compliance ([b89414b](https://github.com/syrax90/claude_mobile/commit/b89414b43d4ce7b075bcad0ec2dfe26518208de1))
* **android:** target Android 16 (API 36) for Play compliance ([c938665](https://github.com/syrax90/claude_mobile/commit/c938665a7b7b862c693502a700f4a1d5acaf0f94))
* **android:** use clearer icon for custom shortcut trigger button ([3925ffc](https://github.com/syrax90/claude_mobile/commit/3925ffc316c80a8c6bd182d84d87bd415f2e8b7d))
* **android:** use clearer icon for custom shortcut trigger button ([a551bfd](https://github.com/syrax90/claude_mobile/commit/a551bfdeb2c217f4451478a8458d4551606c7511))
* resolve review findings for custom shortcut persistence (CLA-137) ([4b90ef2](https://github.com/syrax90/claude_mobile/commit/4b90ef28ceef21993d9ff193d4e83e4356ff1e36))

## [2.2.0](https://github.com/syrax90/claude_mobile/compare/android-v2.1.0...android-v2.2.0) (2026-07-11)


### Features

* **agent:** move control-key row under Command field chip ([972ba42](https://github.com/syrax90/claude_mobile/commit/972ba42bdbcf42731d3df066ae78e033bff0b825))
* **android:** add Paste to terminal long-press selection menu (CLA-132) ([4b78c6d](https://github.com/syrax90/claude_mobile/commit/4b78c6d2d76341ba12848081e8b713157d276aa7))
* **android:** add Paste to terminal long-press selection menu (CLA-132) ([aa3a79f](https://github.com/syrax90/claude_mobile/commit/aa3a79fd92e3bd57325348675d0ca95ef22c5f3e))
* **android:** auto-raise keyboard on terminal input readiness + direct TUI input routing (CLA-123) ([8af7e13](https://github.com/syrax90/claude_mobile/commit/8af7e139999add293ddfce0111fd9b743b3c4b56))
* **android:** auto-raise keyboard on terminal input readiness + direct TUI input routing (CLA-123) ([3b10b7e](https://github.com/syrax90/claude_mobile/commit/3b10b7e1805fa9ce694dc19e5968497fd743db6a))
* **push:** APNs push pipeline for session attention (CLA-106 / iOS-10) ([14178f4](https://github.com/syrax90/claude_mobile/commit/14178f4ae572f2259151b27c6c49b7092b6d5e1f))
* **push:** APNs push pipeline for session attention (CLA-106 / iOS-10) ([a1f2877](https://github.com/syrax90/claude_mobile/commit/a1f2877251ef9350c99fdade5504c6d13b0007da))


### Bug Fixes

* **android:** gate direct typing and raise taps on running session, stop raise taps clicking mouse-tracking TUIs (CLA-123 Codex round 2) ([d28d842](https://github.com/syrax90/claude_mobile/commit/d28d84228380e2b898aae31fe469c44e6c8cf85a))
* **android:** gate direct typing on live running session and flush coalesced sync frames (CLA-123 Codex follow-up) ([1e40afe](https://github.com/syrax90/claude_mobile/commit/1e40afe1b68910a559e4db2b225a8ec83d5b1ec8))
* **android:** keep show-keyboard button on the Command field row ([9786d93](https://github.com/syrax90/claude_mobile/commit/9786d9383cdccb30e2ed578f07d307616b49206e))
* **android:** keep show-keyboard button on the Command field row ([a55ed2e](https://github.com/syrax90/claude_mobile/commit/a55ed2e97c6aab550c08c3907876cfced47658aa))
* **android:** keep terminal cursor row visible when soft keyboard shows ([337b0dc](https://github.com/syrax90/claude_mobile/commit/337b0dc086c98b90117a427a376feeaee8a8e331))
* **android:** keep terminal cursor row visible when soft keyboard shows ([401a193](https://github.com/syrax90/claude_mobile/commit/401a1932b6b3f4581bd765e611c353ee84bcd1f1))
* **android:** make terminal keyboard explicit-only and dismiss on leaving session ([87a928a](https://github.com/syrax90/claude_mobile/commit/87a928a4921ab29d718afc9bd2c387c5831e0bf8))
* **android:** make terminal keyboard explicit-only and dismiss on leaving session ([0037781](https://github.com/syrax90/claude_mobile/commit/0037781106a2bba67fb66aca84a619ec856a0322))
* **android:** move control-key row under Command field chip ([9ec99f7](https://github.com/syrax90/claude_mobile/commit/9ec99f7c5423b64b483285978a2f5af329fffff2))
* **android:** pan terminal for soft keyboard instead of resizing grid ([df7dcf3](https://github.com/syrax90/claude_mobile/commit/df7dcf3faa0c18d6e540db04e04d06b28e81d1a5))
* **android:** pan terminal for soft keyboard instead of resizing grid ([8bdde66](https://github.com/syrax90/claude_mobile/commit/8bdde667a267cfb2eee8bafef12c22cca516e097))
* **android:** reject URI-only clipboard items to avoid ContentProvider I/O on main thread ([621a5f3](https://github.com/syrax90/claude_mobile/commit/621a5f3cd3b69d4ebd997567369124563c9209aa))
* **android:** retarget IME on direct-mode exit and release deferred replay raise only at replay end (CLA-123 Codex follow-up) ([b982823](https://github.com/syrax90/claude_mobile/commit/b98282368f3a47e3363fc0fcc413d95818cdc98e))
* **android:** show unavailability reason text in warning yellow ([14061b8](https://github.com/syrax90/claude_mobile/commit/14061b875b8fa45fa0b0c6886fdea341e7827af1))
* **android:** show unavailability reason text in warning yellow ([c63674d](https://github.com/syrax90/claude_mobile/commit/c63674d06f0904e8adab2ef841894369ea27e964))
* **android:** stop terminal screen shift and add composer show/hide controls (CLA-123 follow-up) ([e1809a1](https://github.com/syrax90/claude_mobile/commit/e1809a1276ea5d99b8fd92c48280d709e29f5a21))
* **android:** stop terminal screen shift and add composer show/hide controls (CLA-123 follow-up) ([c8ab6be](https://github.com/syrax90/claude_mobile/commit/c8ab6bec98bdbc5bd1d192a2b09401276c284600))

## [2.1.0](https://github.com/syrax90/claude_mobile/compare/android-v2.0.2...android-v2.1.0) (2026-07-05)


### Features

* **agent,android:** replace Gemini CLI with Antigravity CLI (agy) [CLA-95] ([ca777e1](https://github.com/syrax90/claude_mobile/commit/ca777e15a5a06b6bb54df3d037c8367d8348925f))
* **agent,android:** replace Gemini CLI with Antigravity CLI (agy) [CLA-95] ([0dadd0f](https://github.com/syrax90/claude_mobile/commit/0dadd0ffc514f63f3523a7b787181c70af7b3379))
* **android:** add Gemini control-key bar to the terminal InputBar (CLA-89) ([2437a01](https://github.com/syrax90/claude_mobile/commit/2437a016511cfbfbb5d38da4fb5e2731f8e7604a))
* **android:** add Gemini control-key bar to the terminal InputBar (CLA-89) ([0b968d6](https://github.com/syrax90/claude_mobile/commit/0b968d62d64d40f64025639168c5ba61f10dc5e4))
* **android:** add Gemini to the create-session wizard + autonomy presets (CLA-88) ([47706ca](https://github.com/syrax90/claude_mobile/commit/47706cab72441bd9d655669ff99e81936ce69ebf))
* **android:** add Gemini to the create-session wizard + autonomy presets (CLA-88) ([1728695](https://github.com/syrax90/claude_mobile/commit/172869541446b1e8a67526feace2acf3a058fc37))


### Bug Fixes

* **android:** keep Rewind (Esc-Esc) in the Gemini InputBar control keys (CLA-89) ([bb44c9d](https://github.com/syrax90/claude_mobile/commit/bb44c9d914c8b791c2bdbbde29e7a34d7dfd9aa5))
* **android:** refresh templates cache before opening prompt picker ([fcf6d7d](https://github.com/syrax90/claude_mobile/commit/fcf6d7dc543b9a06fa6d0016efd7426242c86a59))
* **android:** show host-side auth message on gemini_auth_required (CLA-88) ([f4f294a](https://github.com/syrax90/claude_mobile/commit/f4f294a70d454d780e4df392d4a73b3468f947b9))
* **android:** show unavailability reason text in error red ([e1247f7](https://github.com/syrax90/claude_mobile/commit/e1247f7b7f22a1b1600e59a4fef4682f6dbf01a7))
* **android:** show unavailability reason text in error red ([7b88f83](https://github.com/syrax90/claude_mobile/commit/7b88f830624f11c1342111c1db1ce5320a4372e3))

## [2.0.2](https://github.com/syrax90/claude_mobile/compare/android-v2.0.1...android-v2.0.2) (2026-06-30)


### Bug Fixes

* **android:** grey out Auto Scan mode card instead of hiding it ([dce0ee8](https://github.com/syrax90/claude_mobile/commit/dce0ee8bf9dad0f789f18ed70e71a315e87011f2))
* **android:** remove hardcoded AutoPipeline from Auto Scan mode label ([0aea911](https://github.com/syrax90/claude_mobile/commit/0aea91183b96d926fa1c2b07137cd07a2ad4e0bf))
* **ui:** gate Continue button on Auto Scan availability ([0d9b7a2](https://github.com/syrax90/claude_mobile/commit/0d9b7a2ade54a4bc90450c09d2d2d3a9105267a5))
* **ui:** grey out Auto Scan mode card instead of hiding it ([17245ef](https://github.com/syrax90/claude_mobile/commit/17245ef0595be27c8ff6b48bf8e1e82d306e7014))
* **ui:** remove hardcoded AutoPipeline from Auto Scan mode label ([7b45a75](https://github.com/syrax90/claude_mobile/commit/7b45a75d8b59b84d668c3282030e763805d273b6))

## [2.0.1](https://github.com/syrax90/claude_mobile/compare/android-v2.0.0...android-v2.0.1) (2026-06-29)


### Bug Fixes

* **android:** persist last-used Codex session settings ([a5a16d6](https://github.com/syrax90/claude_mobile/commit/a5a16d628beb7c0a5b98450f33339b2e6d401304))
* **android:** render terminal control-key arrows as uniform Material icons ([8b23787](https://github.com/syrax90/claude_mobile/commit/8b237871eda1fe5338c571845e495664bad0b974))

## [2.0.0](https://github.com/syrax90/claude_mobile/compare/android-v1.15.0...android-v2.0.0) (2026-06-26)


### ⚠ BREAKING CHANGES

* **sessions:** wire protocol bumped 5→6. The claude_options envelope field changed shape: dropped max_effort (bool); added model (str), effort (str|null), fast_mode (bool), ultracode (bool), append_system_prompt (str|null). Any relay or client built against proto 5 will fail the version handshake with a proto 6 agent. New sessions default to effort="high" instead of always "max".

### Features

* **sessions:** replace Claude max-effort toggle with model + effort controls (CLA-84) ([3b52a46](https://github.com/syrax90/claude_mobile/commit/3b52a46439d066fc15c95fbd8084d84b79bf195e))


### Bug Fixes

* **android:** Replace the boolean Max effort toggle with rich Claude session controls (CLA-84) ([3b052b1](https://github.com/syrax90/claude_mobile/commit/3b052b1cda8438c98b9375128ac378612c97f46c))

## [1.15.0](https://github.com/syrax90/claude_mobile/compare/android-v1.14.0...android-v1.15.0) (2026-06-25)


### Features

* **agent+android:** gray out Claude/Codex mode buttons when CLI is not installed ([7134c33](https://github.com/syrax90/claude_mobile/commit/7134c33f1480c8e33be6d4c0b0c172380a09096c))
* **android:** gray out Claude/Codex mode buttons when CLI is not installed ([cf95d70](https://github.com/syrax90/claude_mobile/commit/cf95d708274f048bdb9f5dc47e27b36563e6ddfb))

## [1.14.0](https://github.com/syrax90/claude_mobile/compare/android-v1.13.0...android-v1.14.0) (2026-06-24)


### Features

* add Codex to the create-session wizard (CLA-69) ([f41919c](https://github.com/syrax90/claude_mobile/commit/f41919c1c51f5191cf754c80d9e2471e108a04f3))
* add Codex to the create-session wizard (CLA-69) ([c7600f6](https://github.com/syrax90/claude_mobile/commit/c7600f6df2a019fec6a37f73ba9376a932a2eaf4))
* **agent:** preserve per-project Auto Scan initial prompt across sessions ([a58be75](https://github.com/syrax90/claude_mobile/commit/a58be75a100c0e0e276f02451b1b1c6a777af114))
* **agent:** preserve per-project Auto Scan initial prompt across sessions ([dc95191](https://github.com/syrax90/claude_mobile/commit/dc9519167d024895b2bbaf90a42126db5ba44e70))
* **android:** make terminal control-key bar CLI-aware for Codex (CLA-70) ([c0f50ac](https://github.com/syrax90/claude_mobile/commit/c0f50ac10cb8bcf15d7f3ca4f02473d46d56cd68))
* **android:** make terminal control-key bar CLI-aware for Codex (CLA-70) ([4abfc10](https://github.com/syrax90/claude_mobile/commit/4abfc1005ad3cda9a418baba406009e5696016f8))
* **codex:** add in-app Codex CLI authentication/login flow (CLA-71) ([63b72b6](https://github.com/syrax90/claude_mobile/commit/63b72b6dd6d1dfb24f81f057b3de95778de05e0e))
* **codex:** add in-app Codex CLI authentication/login flow (CLA-71) ([6f7fb00](https://github.com/syrax90/claude_mobile/commit/6f7fb00953d89d44e9cad2ed17e29e066185b599))


### Bug Fixes

* **agent:** address Codex review — harden auto_scan_prompt persistence and seeding ([456a5f8](https://github.com/syrax90/claude_mobile/commit/456a5f874779dabed3391306ed16ba2703bc8dee))
* **android:** seed Codex sessionKind from REST on cold launch (CLA-70 review) ([88012f8](https://github.com/syrax90/claude_mobile/commit/88012f8b96ea3b9371206aca15b02b71de1b5ca8))
* **codex:** address PR [#270](https://github.com/syrax90/claude_mobile/issues/270) review-round-2 — launcher test CI fix + SecretTextField IME hardening (CLA-71) ([1348d83](https://github.com/syrax90/claude_mobile/commit/1348d8386e92ac0ef311e4ee69bfef5fd57d6db3))
* **codex:** remap auth-required codes to 409/400 to avoid AuthInterceptor retry (CLA-71) ([62d38c4](https://github.com/syrax90/claude_mobile/commit/62d38c4ff5cd96adf6f60afd5c184e2d0366170a))

## [1.13.0](https://github.com/syrax90/claude_mobile/compare/android-v1.12.0...android-v1.13.0) (2026-06-23)


### Features

* **agent:** generalize CLI launch abstraction and add Codex CLI support (CLA-68) ([de719f2](https://github.com/syrax90/claude_mobile/commit/de719f27d013f277ede0bac900ca4ff8bd4fb59d))
* **agent:** generalize CLI launch abstraction and add Codex CLI support (CLA-68) ([f9c0d83](https://github.com/syrax90/claude_mobile/commit/f9c0d8375d5a5106cb78561881b9cb93b8c584b3))

## [1.12.0](https://github.com/syrax90/claude_mobile/compare/android-v1.11.0...android-v1.12.0) (2026-06-17)


### Features

* **agent:** surface macOS file-access (TCC) freezes to the app instead of hanging ([24740f0](https://github.com/syrax90/claude_mobile/commit/24740f0b212e20616ab516f8a18c661f2079cab5))
* **agent:** surface macOS file-access (TCC) freezes to the app instead of hanging ([ce03474](https://github.com/syrax90/claude_mobile/commit/ce034747322213b587cdbb41603f849b2baa2d8f))
* **terminal:** default Terminal Display to Static mode ([625da1b](https://github.com/syrax90/claude_mobile/commit/625da1b40b497205732c3fcbb27f93b99c405c44))
* **terminal:** default Terminal Display to Static mode ([711034d](https://github.com/syrax90/claude_mobile/commit/711034d770dbb660b11ec0ef1df0630823c4c223))

## [1.11.0](https://github.com/syrax90/claude_mobile/compare/android-v1.10.1...android-v1.11.0) (2026-06-16)


### Features

* **android:** guide users to apply Agent ID + Token on the agent host ([09677a4](https://github.com/syrax90/claude_mobile/commit/09677a4d5b82fb170b82e77be379ba34e24d5828))
* **android:** guide users to apply Agent ID + Token on the agent host ([84563d6](https://github.com/syrax90/claude_mobile/commit/84563d607e3a7864f67064752b2c35c918442eb4))

## [1.10.1](https://github.com/syrax90/claude_mobile/compare/android-v1.10.0...android-v1.10.1) (2026-06-13)


### Bug Fixes

* **agent:** allow force-deleting worktrees with modified/untracked files ([9ab4892](https://github.com/syrax90/claude_mobile/commit/9ab4892a1ff1b8245470f39303d6b88cbe812952))
* **agent:** fix Send button not executing commands without manual Enter ([086699f](https://github.com/syrax90/claude_mobile/commit/086699fd3276f2be494f4a32f8df6820c48c5bc4))
* **git:** allow force-deleting worktrees with modified/untracked files ([003fb66](https://github.com/syrax90/claude_mobile/commit/003fb6673efe7f8b0fe77ac37c19473bf1418d74))
* **terminal:** fix Send button not executing commands without manual Enter ([261fd96](https://github.com/syrax90/claude_mobile/commit/261fd961bf5dbd5b36c2477c72b3016c132e463c))

## [1.10.0](https://github.com/syrax90/claude_mobile/compare/android-v1.9.0...android-v1.10.0) (2026-06-09)


### Features

* **agent:** add Terminal session worktree config page ([755a94b](https://github.com/syrax90/claude_mobile/commit/755a94bc314162c358159a5b29cf7bd0269c5f87))
* **sessions:** add Terminal session worktree config page ([0a65eee](https://github.com/syrax90/claude_mobile/commit/0a65eee9343c8a52dc066ba337acd30001c9d253))

## [1.9.0](https://github.com/syrax90/claude_mobile/compare/android-v1.8.2...android-v1.9.0) (2026-06-09)


### Features

* **agent:** pop-up notifications when a Claude session awaits input or ends(CLA-64) ([6410cdb](https://github.com/syrax90/claude_mobile/commit/6410cdb50d471709d1aa560a62b8040e516d1d40))
* pop-up notifications when a Claude session awaits input or ends (CLA-64) ([848af44](https://github.com/syrax90/claude_mobile/commit/848af446a8cb6db7c699c34bbfa0a8093ede7271))

## [1.8.2](https://github.com/syrax90/claude_mobile/compare/android-v1.8.1...android-v1.8.2) (2026-06-09)


### Bug Fixes

* **android:** make Settings screen scrollable so the Logout button isn't clipped ([3aed327](https://github.com/syrax90/claude_mobile/commit/3aed327a65d38d6fdf693fd311524ee3fabd6737))
* **android:** remove agent id from connection label ([15c08f5](https://github.com/syrax90/claude_mobile/commit/15c08f52217f49e0d687714efc2370f301edfbb1))
* **android:** source app version from BuildConfig instead of hardcoded v1.0.0 ([8ef3f5e](https://github.com/syrax90/claude_mobile/commit/8ef3f5e80a1978e886ad21f106d23ca1d49735aa))
* **settings:** make Settings screen scrollable so the Logout button isn't clipped ([947d020](https://github.com/syrax90/claude_mobile/commit/947d020ab1de4895ebc84dfe39acd9e9418343e8))
* **settings:** remove agent id from connection label ([84e005d](https://github.com/syrax90/claude_mobile/commit/84e005d3a857c44c4d1fdc22c9aa91bb2b8b999e))
* **settings:** source app version from BuildConfig instead of hardcoded v1.0.0 ([0030184](https://github.com/syrax90/claude_mobile/commit/00301845d8b6cb00901b0ba4f2ee174d7fd01685))

## [1.8.1](https://github.com/syrax90/claude_mobile/compare/android-v1.8.0...android-v1.8.1) (2026-06-08)


### Bug Fixes

* **terminal:** prepend Ctrl+A/Ctrl+K line clear before every composer send ([8739eb4](https://github.com/syrax90/claude_mobile/commit/8739eb41f80ac5003e217920e67607fa452a11c0))

## [1.8.0](https://github.com/syrax90/claude_mobile/compare/android-v1.7.1...android-v1.8.0) (2026-05-29)


### Features

* **android:** add Mobaide adaptive launcher icon ([3030f87](https://github.com/syrax90/claude_mobile/commit/3030f87e8a1456819be8cfa285685bb9875f464c))


### Bug Fixes

* **android:** add Mobaide adaptive launcher icon ([441f0bc](https://github.com/syrax90/claude_mobile/commit/441f0bc5148068e125d8bac99e7eeea2afea3811))
* **android:** remove Paste and More from long-press selection toolbar ([6f9208e](https://github.com/syrax90/claude_mobile/commit/6f9208e04389f971f7f2119987da022dba064ff8))
* **terminal:** remove Paste and More from long-press selection toolbar ([979f0a9](https://github.com/syrax90/claude_mobile/commit/979f0a994ea241107593a8402a9b4014f81d794d))

## [1.7.1](https://github.com/syrax90/claude_mobile/compare/android-v1.7.0...android-v1.7.1) (2026-05-29)


### Bug Fixes

* **android:** add long-press Send-to-Input action and fix silent Copy no-op (CLA-63) ([299a7e8](https://github.com/syrax90/claude_mobile/commit/299a7e8c78e4dbb5b14530f7633dcf84eec8a5f4))
* **android:** make TerminalView focusable so long-press selection toolbar appears (CLA-63) ([79384b6](https://github.com/syrax90/claude_mobile/commit/79384b6fc3c50e9934453e6705fcfe24fea37d25))
* **android:** remove dead foreground service and its artifacts ([1475656](https://github.com/syrax90/claude_mobile/commit/1475656689a68bdc942fd8f2a3feabd3fd4f2052))
* **android:** remove dead foreground service and its artifacts ([d6d98b3](https://github.com/syrax90/claude_mobile/commit/d6d98b3677f74edfc6ad3fb27216c1e8aecedf85))
* **terminal:** make TerminalView focusable so long-press selection toolbar appears (CLA-63) ([1c61af7](https://github.com/syrax90/claude_mobile/commit/1c61af735e7b20d072ab404f1abb98a542d483e6))

## [1.7.0](https://github.com/syrax90/claude_mobile/compare/android-v1.6.0...android-v1.7.0) (2026-05-25)


### Features

* **auth:** replace hardcoded OTP placeholder with segmented CodeInputField ([d351ac3](https://github.com/syrax90/claude_mobile/commit/d351ac32488a09905ba6689d7ed25ebe1630355a))


### Bug Fixes

* **android:** update network security config to disallow cleartext traffic ([b5cc408](https://github.com/syrax90/claude_mobile/commit/b5cc40896f61cb8ba0815e9a963a6498e026289f))
* **android:** update network security config to disallow cleartext traffic ([6a7e1a1](https://github.com/syrax90/claude_mobile/commit/6a7e1a1630958a56dbd08b85cfdc75f6d321db65))

## [1.6.0](https://github.com/syrax90/claude_mobile/compare/android-v1.5.2...android-v1.6.0) (2026-05-23)


### Features

* **ui:** rename "Your devices" to "My devices" and add Google logo to sign-in buttons ([657fd92](https://github.com/syrax90/claude_mobile/commit/657fd924ddf2d6e5bc8d39a3b70775922d98a086))


### Bug Fixes

* **android:** rename "Your devices" to "My devices" and add Google logo to sign-in buttons ([bbc4eab](https://github.com/syrax90/claude_mobile/commit/bbc4eab6c4617c47b35e5729b093eab55a200905))
* **android:** replace 256 KB single-frame replay with 4 MB chunked ring buffer (proto v3) ([b1740e9](https://github.com/syrax90/claude_mobile/commit/b1740e92bbe3fcc77edcf620490ae9000985217b))
* **replay:** replace 256 KB single-frame replay with 4 MB chunked ring buffer (proto v3) ([bd8bb63](https://github.com/syrax90/claude_mobile/commit/bd8bb633f83e7a6552b0db15f00291b71ec53504))

## [1.5.2](https://github.com/syrax90/claude_mobile/compare/android-v1.5.1...android-v1.5.2) (2026-05-23)


### Bug Fixes

* **android:** force-reconnect on screen-on + clear emulator before server replay to unstick "Reconnecting" overlay ([c1aea71](https://github.com/syrax90/claude_mobile/commit/c1aea7117000373a5179677d4176c5156a5712b9))
* **terminal:** force-reconnect on screen-on + clear emulator before server replay to unstick "Reconnecting" overlay ([51ebff2](https://github.com/syrax90/claude_mobile/commit/51ebff2ef958b03aca9adc8a007437a7a2e942a4))

## [1.5.1](https://github.com/syrax90/claude_mobile/compare/android-v1.5.0...android-v1.5.1) (2026-05-23)


### Bug Fixes

* **android:** respect lp.width in TerminalView.onMeasure + direction-aware HSV intercept to restore horizontal pan in Static display mode ([9162b23](https://github.com/syrax90/claude_mobile/commit/9162b23d66e70f858ddc607c3ed9d3da21d0e992))
* **terminal:** respect lp.width in TerminalView.onMeasure + direction-aware HSV intercept to restore horizontal pan in Static display mode ([4e2a6c6](https://github.com/syrax90/claude_mobile/commit/4e2a6c66176082fde34deb575b3f55a5f4574e06))

## [1.5.0](https://github.com/syrax90/claude_mobile/compare/android-v1.4.0...android-v1.5.0) (2026-05-22)


### Features

* self-host privacy policy at /privacy.html and surface links in Android UI ([9eb0b90](https://github.com/syrax90/claude_mobile/commit/9eb0b90c184b13617810b52d08faa9c25b6834dd))
* self-host privacy policy at /privacy.html and surface links in Android UI ([882251b](https://github.com/syrax90/claude_mobile/commit/882251b75e5f32777dfa356ae8906f7f48ed18ca))


### Bug Fixes

* **android:** replace Compose horizontalScroll with native HorizontalScrollView for terminal gesture handling ([4d5168d](https://github.com/syrax90/claude_mobile/commit/4d5168d3c977fe4690b42fe9d6e5a0a3f9492b28))
* **terminal:** replace Compose horizontalScroll with native HorizontalScrollView for terminal gesture handling ([b7edd46](https://github.com/syrax90/claude_mobile/commit/b7edd46b239c8c632949b426937b508772332846))

## [1.4.0](https://github.com/syrax90/claude_mobile/compare/android-v1.3.0...android-v1.4.0) (2026-05-22)


### Features

* **account-deletion:** user self-service deletion + portal + 14-day grace (CLA-61) ([8fb14dd](https://github.com/syrax90/claude_mobile/commit/8fb14ddb3d18c83dd2c9cae4451ddabeda4bcee1))
* **android:** user self-service deletion + portal + 14-day grace (CLA-61) ([c8ea9cb](https://github.com/syrax90/claude_mobile/commit/c8ea9cb97c4dcf86dfc3967be84611230dfd4c90))
* **devices:** per-install device UUID + user-scoped devices API + "Your devices" screen (CLA-62) ([354fb68](https://github.com/syrax90/claude_mobile/commit/354fb6890ed16de899a1952db81f6fce8d630891))
* **devices:** per-install device UUID + user-scoped devices API + "Your devices" screen (CLA-62) ([60bc57a](https://github.com/syrax90/claude_mobile/commit/60bc57a38aa91f5be0aa14143c70a300205ec93e))


### Bug Fixes

* **account-deletion:** resolve 5 reported issues end-to-end ([0775b9d](https://github.com/syrax90/claude_mobile/commit/0775b9d544ea1165d1d4c0c3523c841faf87ba7e))
* **android:** resolve 5 reported issues end-to-end ([1a43c9b](https://github.com/syrax90/claude_mobile/commit/1a43c9becff506141f30eef1fac930a512b0bba7))

## [1.3.0](https://github.com/syrax90/claude_mobile/compare/android-v1.2.0...android-v1.3.0) (2026-05-21)


### Features

* **agent:** add Custom Path option to project picker for ad-hoc sessions (CLA-60) ([6ec5287](https://github.com/syrax90/claude_mobile/commit/6ec52878013b2ba1790cec88dc4386c24e6a3631))
* **sessions:** add Custom Path option to project picker for ad-hoc sessions (CLA-60) ([72cb848](https://github.com/syrax90/claude_mobile/commit/72cb848a9552f667d3cf696b58269b2d61bcb0e0))

## [1.2.0](https://github.com/syrax90/claude_mobile/compare/android-v1.1.0...android-v1.2.0) (2026-05-20)


### Features

* **agent:** add Terminal session mode as third create-session tile (CLA-59) ([874171a](https://github.com/syrax90/claude_mobile/commit/874171a4078ecb9e67455af0ace9f46c81847197))
* **sessions:** add Terminal session mode as third create-session tile (CLA-59) ([5d0b61e](https://github.com/syrax90/claude_mobile/commit/5d0b61e49d195874de8bf6d28b02d0757757fedc))
* **terminal:** refactor InputBar for orientation-aware layout and tidy control-key panel ([1e81e00](https://github.com/syrax90/claude_mobile/commit/1e81e00701c6ca560b4f2e830054d24a07bf489c))
* **terminal:** refactor InputBar for orientation-aware layout and tidy control-key panel ([73ed6f2](https://github.com/syrax90/claude_mobile/commit/73ed6f2c03b9caf56e162e67f3a259f50ad82391))


### Bug Fixes

* **settings:** display user email in "Logged in as" and rename "Standard mode" to "Claude code session" ([666bf18](https://github.com/syrax90/claude_mobile/commit/666bf18e7fd65a7b3f7d670db9bfec73480513de))

## [1.1.0](https://github.com/syrax90/claude_mobile/compare/android-v1.0.3...android-v1.1.0) (2026-05-19)


### Features

* **sessions:** show Claude Code OSC terminal title on Android session list ([95f7ea7](https://github.com/syrax90/claude_mobile/commit/95f7ea738aa62e0151d8385c9d5490da7b61c863))


### Bug Fixes

* **agent:** show Claude Code OSC terminal title on Android session list ([969a878](https://github.com/syrax90/claude_mobile/commit/969a878ac2fba99176d3048ab17b61837ae4dcec))
* **infra:** restore release tag creation by deleting empty Releases ([67c45ba](https://github.com/syrax90/claude_mobile/commit/67c45ba6bf685c9d2ccd0d8ed66e71f89e34e25a))
* **infra:** restore release tag creation by deleting empty Releases ([81933be](https://github.com/syrax90/claude_mobile/commit/81933be753698a1a00bcb584f6a5d2bd21c11111))

## [1.0.3](https://github.com/syrax90/claude_mobile/compare/android-v1.0.2...android-v1.0.3) (2026-05-18)


### Bug Fixes

* **android:** setting GOOGLE_WEB_CLIENT_ID for release ([be04036](https://github.com/syrax90/claude_mobile/commit/be04036489d380c417035cc2e997f3c4bbaba351))
* **android:** setting GOOGLE_WEB_CLIENT_ID for release ([2e8d847](https://github.com/syrax90/claude_mobile/commit/2e8d8471c2d1c9a4ef4eba598974ad7b0c010cf2))

## [1.0.2] - 2026-05-18

### Features

* **agents:** add install-commands bottom sheet with empty-state guide and overflow recall
