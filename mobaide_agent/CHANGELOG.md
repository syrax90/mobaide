# mobaide_agent Changelog

## [0.3.24](https://github.com/syrax90/mobaide/compare/agent-v0.3.23...agent-v0.3.24) (2026-11-08)


### Bug Fixes

* **agent,server:** download the voice model from our R2 mirror, not GitHub ([#839](https://github.com/syrax90/mobaide/issues/839)) ([04c4c6c](https://github.com/syrax90/mobaide/commit/04c4c6c65ac65b29fccceae9defc8764a2bd543e))

## [0.3.23](https://github.com/syrax90/mobaide/compare/agent-v0.3.22...agent-v0.3.23) (2026-11-08)


### Features

* **agent,web,android,ios,server:** AI cleanup of dictated text ([#835](https://github.com/syrax90/mobaide/issues/835)) ([596856d](https://github.com/syrax90/mobaide/commit/596856d50924c51145047a58f6e2f97e3d3a2306))
* **git:** allow checkout while sessions use the project folder ([#828](https://github.com/syrax90/mobaide/issues/828)) ([b9311a6](https://github.com/syrax90/mobaide/commit/b9311a6d8bd1eb266856f4b5999b0809fc2b77a1))

## [0.3.22](https://github.com/syrax90/claude_mobile/compare/agent-v0.3.21...agent-v0.3.22) (2026-11-04)


### Bug Fixes

* **agent:** recheck the staged version under the updater lock ([189161d](https://github.com/syrax90/claude_mobile/commit/189161d1b14bb9ad3478958daf9de4398ae50c96))
* **agent:** restart the Windows agent after an update ([b25dd39](https://github.com/syrax90/claude_mobile/commit/b25dd39a27ff053301a2d9389bedee9b67cea04a))
* **agent:** restart the Windows agent after an update ([95a7cde](https://github.com/syrax90/claude_mobile/commit/95a7cdea4f80358b29cb59ae4f4644d7232ae1ae))

## [0.3.21](https://github.com/syrax90/claude_mobile/compare/agent-v0.3.20...agent-v0.3.21) (2026-11-04)


### Bug Fixes

* **agent:** say where the manifest signing key lives ([230888b](https://github.com/syrax90/claude_mobile/commit/230888be46abcc4f7d56ec39996751eec87f2ffe))
* **agent:** say where the manifest signing key lives ([0bcdcb6](https://github.com/syrax90/claude_mobile/commit/0bcdcb62cfb66c8398985776adda6da095a7ae59))

## [0.3.20](https://github.com/syrax90/claude_mobile/compare/agent-v0.3.19...agent-v0.3.20) (2026-10-30)


### Bug Fixes

* **agent,server:** stop the unpaired-agent pairing loop and cap anonymous initiate per IP ([14bc4de](https://github.com/syrax90/claude_mobile/commit/14bc4de477d2fba1dd9ef9aabf459ed0802bcea9))
* **agent,server:** stop the unpaired-agent pairing loop and cap anonymous initiate per IP ([6b72528](https://github.com/syrax90/claude_mobile/commit/6b72528939746f3332bfbb01b670663565a2f1d9))
* **agent:** drop the unsaved pairing nonce after a relay 403 handshake rejection ([415ef56](https://github.com/syrax90/claude_mobile/commit/415ef560683da1f875532a1baa3f8316b06eb521))

## [0.3.19](https://github.com/syrax90/claude_mobile/compare/agent-v0.3.18...agent-v0.3.19) (2026-10-26)


### Bug Fixes

* **agent:** put mobaide-agent on PATH after install ([cff50c5](https://github.com/syrax90/claude_mobile/commit/cff50c50e7fa029ca64569f237515f7c0241944b))
* **agent:** stop the orphaned agent so re-pair can pair again on Windows ([02cc41c](https://github.com/syrax90/claude_mobile/commit/02cc41c31b584abb30571bb6583e1809b4bca3f6))
* **agent:** stop voice dictation silently dropping quiet speech ([95d5978](https://github.com/syrax90/claude_mobile/commit/95d59786ceba2868b3852a8671ee9e4ac8042b4b))

## [0.3.18](https://github.com/syrax90/claude_mobile/compare/agent-v0.3.17...agent-v0.3.18) (2026-10-25)


### Bug Fixes

* **agent:** submit long multi-line prompts in one tap ([4440e52](https://github.com/syrax90/claude_mobile/commit/4440e52f793f8e6464d2a623db0d677823351a9d))

## [0.3.17](https://github.com/syrax90/claude_mobile/compare/agent-v0.3.16...agent-v0.3.17) (2026-10-23)


### Features

* opt-in on-PC voice input in the agent and Android, iOS and web clients ([#736](https://github.com/syrax90/claude_mobile/issues/736), [#737](https://github.com/syrax90/claude_mobile/issues/737)) ([7fb5482](https://github.com/syrax90/claude_mobile/commit/7fb5482a39eb2a7d4fd33e84c05e3237f23ec175))
* **web:** mic icon, dictate more and a review opt-out; ~700 MB voice memory ([#737](https://github.com/syrax90/claude_mobile/issues/737)) ([a5b49e8](https://github.com/syrax90/claude_mobile/commit/a5b49e8c0a0c0d56a4a9ec2a84c8d570c4b55403))


### Bug Fixes

* **agent:** delete the voice model when the agent uninstalls itself ([c83d4e2](https://github.com/syrax90/claude_mobile/commit/c83d4e2adc4c3a296af9647fd4c1ae3430498b2a))
* **agent:** reap a voice worker whose spawn was cancelled ([e56e064](https://github.com/syrax90/claude_mobile/commit/e56e0649d7a5453e6e9c7b8b0d35bd5cc6ab31b6))
* **agent:** report ~700 MB of dictation memory instead of 2 GB ([ba8df34](https://github.com/syrax90/claude_mobile/commit/ba8df341bdbb4483796f1ff5c89011e1a0efd984))
* **agent:** report ready only after a passed self-test ([b190424](https://github.com/syrax90/claude_mobile/commit/b1904243b5ad029f2c550c2129c9aca2984a08a6))

## [0.3.16](https://github.com/syrax90/claude_mobile/compare/agent-v0.3.15...agent-v0.3.16) (2026-10-19)


### Features

* add Focus view toggle and collapsible Advanced group to session launch ([eff1cec](https://github.com/syrax90/claude_mobile/commit/eff1cecbc3b109044c58ba5b54a328de4a611e30))
* add Focus view toggle and collapsible Advanced group to session launch ([166acfa](https://github.com/syrax90/claude_mobile/commit/166acfa071e61ba1a4a35bddea8ac8ba0ffaf8b3))

## [0.3.15](https://github.com/syrax90/claude_mobile/compare/agent-v0.3.14...agent-v0.3.15) (2026-10-17)


### Features

* suggest existing folders while typing project and custom paths ([4f13cfc](https://github.com/syrax90/claude_mobile/commit/4f13cfc1e729eb026f982d3f991a3b8976153e9f))
* suggest existing folders while typing project and custom paths ([6f2bb9e](https://github.com/syrax90/claude_mobile/commit/6f2bb9e1e5db9b14e066e3986031ae4d2ce1f7ea))


### Bug Fixes

* close folder suggestions on Done and word paths for Windows agents ([78740a9](https://github.com/syrax90/claude_mobile/commit/78740a9ffb2271259eaf64c26d9388fb23a6496b))
* close folder suggestions on Done and word paths for Windows agents ([f1ccacc](https://github.com/syrax90/claude_mobile/commit/f1ccacce058bf59f4aa869926e5bb0de8b65abf3))

## [0.3.14](https://github.com/syrax90/claude_mobile/compare/agent-v0.3.13...agent-v0.3.14) (2026-10-15)


### Features

* let removing an agent also uninstall it from its host ([f2addc4](https://github.com/syrax90/claude_mobile/commit/f2addc424dd34644e34000a360705497431b03d8))
* let removing an agent also uninstall it from its host ([51d77ac](https://github.com/syrax90/claude_mobile/commit/51d77ac05d19d51a39754bf8f7e94a57984ae25f))

## [0.3.13](https://github.com/syrax90/claude_mobile/compare/agent-v0.3.12...agent-v0.3.13) (2026-10-01)


### Bug Fixes

* **agent:** pass the chosen Claude effort to the CLI when it is high ([eb67a39](https://github.com/syrax90/claude_mobile/commit/eb67a394f5cada2d5d3b41b08667b76d0f02ee83))

## [0.3.12](https://github.com/syrax90/claude_mobile/compare/agent-v0.3.11...agent-v0.3.12) (2026-09-22)


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
* **agent:** drop a rescheduled pick on rebase skip instead of retrying it ([d35eb05](https://github.com/syrax90/claude_mobile/commit/d35eb05fb1ce022ac2fa94079bf4ae497928d8cd))
* **agent:** keep the complete rows of a ref listing cut at the byte cap ([6b3107f](https://github.com/syrax90/claude_mobile/commit/6b3107f7d90bff69eaff65c96c75a8e67e4653de))
* **agent:** refuse a remote branch delete when the remote pushes to a different repository than it fetches from ([194d978](https://github.com/syrax90/claude_mobile/commit/194d9783f83367e2aaafeb0f927da05ba4a50edd))
* **agent:** reopen a branch's existing worktree instead of failing to create a second one ([7320c62](https://github.com/syrax90/claude_mobile/commit/7320c62cd683921db607b68cc9b6f7ee3d2ad1ff))
* **agent:** reopen a branch's existing worktree instead of failing to create a second one ([d5e8fe9](https://github.com/syrax90/claude_mobile/commit/d5e8fe9622042d6a3217f98966ebe152505a9ddc))
* **agent:** report a git job's real outcome when a cancel lands after git exited ([0d86eae](https://github.com/syrax90/claude_mobile/commit/0d86eaeb06060c5363e4cddadbacd7869fc51b71))
* **agent:** report a timed-out git job pre-step as git_timeout ([f038b73](https://github.com/syrax90/claude_mobile/commit/f038b7343b4bb31c29871d37ccbb608ac4c68db2))
* **agent:** skip remote-tracking refs with no configured remote and refuse to check them out ([60f8742](https://github.com/syrax90/claude_mobile/commit/60f87425a3632ea85fa2de8b6f1344d04e3d554a))


### Documentation

* UPDATED (PROTOCOL.md). ([0d86eae](https://github.com/syrax90/claude_mobile/commit/0d86eaeb06060c5363e4cddadbacd7869fc51b71))

## [0.3.11](https://github.com/syrax90/claude_mobile/compare/agent-v0.3.10...agent-v0.3.11) (2026-09-14)


### Features

* **agent,android,ios,web:** raise the diff ceiling and add a Full file switch to Git Changes ([2e9ac53](https://github.com/syrax90/claude_mobile/commit/2e9ac53fcea55d382e6daafffbe984a5adaa9034))
* **agent,android,ios,web:** raise the diff ceiling and add a Full file switch to Git Changes ([41758ac](https://github.com/syrax90/claude_mobile/commit/41758ac473dec3772ab8c3c32182e2c1d8b33ced))
* **agent:** add session-scoped read-only git status/diff API ([0f87c33](https://github.com/syrax90/claude_mobile/commit/0f87c338516de5f455c042e4061a717ac1229869))
* **agent:** add session-scoped read-only git status/diff API ([42d953f](https://github.com/syrax90/claude_mobile/commit/42d953fad3df1e60619018959e3b93eec4ed05ff))


### Bug Fixes

* **agent:** address Codex review of the Android git Changes viewer ([f396277](https://github.com/syrax90/claude_mobile/commit/f396277b28723e9851f0b68032c7abbf8cb79900))
* **agent:** address Codex review on session git status/diff API ([0125ec9](https://github.com/syrax90/claude_mobile/commit/0125ec93c4b59dfa5dba18ecec40e307cf1c0944))
* **agent:** fix no-newline marker and DU-conflict edge cases in git diff parsing ([bcff490](https://github.com/syrax90/claude_mobile/commit/bcff490466753721b4c85b7516353ce25c88bc04))
* **agent:** fix trailing-whitespace repo paths and cwd-inside-.git in resolve_repo ([0481741](https://github.com/syrax90/claude_mobile/commit/0481741207304bec9861d4e437775f2b4a8033a1))
* **agent:** split git output on \n only in session git status/diff service ([b214cd6](https://github.com/syrax90/claude_mobile/commit/b214cd6fe5883c084e2629682404eb615426d41b))
* bound the status parse and make Changes list keys collision-free ([e92e1b2](https://github.com/syrax90/claude_mobile/commit/e92e1b20bdd057463a712a75c7821ad5be06e970))

## [0.3.10](https://github.com/syrax90/claude_mobile/compare/agent-v0.3.9...agent-v0.3.10) (2026-08-27)


### Features

* advertise models_pending while the agent's model warmup outlasts the version wait ([e6170d7](https://github.com/syrax90/claude_mobile/commit/e6170d7e83641876c8ef900523e56d63801d8b94))


### Bug Fixes

* Codex model picker missed current models (gpt-5.6-*) — enumerate without --json, advertise per-model efforts ([7dad428](https://github.com/syrax90/claude_mobile/commit/7dad4285374b5b567fbb56cb7830805d3b41e529))
* harden the codex effort reconcile against races, stale catalogs, and cold caches ([8fd8452](https://github.com/syrax90/claude_mobile/commit/8fd8452cc1f83878d7ad93760592da193aeb210a))
* **mobaide_agent,android,ios,web:** enumerate Codex models without --json, advertise per-model efforts ([bde0c30](https://github.com/syrax90/claude_mobile/commit/bde0c3032fec462ece892c1b94bc4f458499cca1))
* scope models_pending to each CLI's own cold cache, retry mobile re-probes on a budget ([bb57197](https://github.com/syrax90/claude_mobile/commit/bb57197116ebbbe3dc7fcbe10f900ea3182b1d3f))

## [0.3.9](https://github.com/syrax90/claude_mobile/compare/agent-v0.3.8...agent-v0.3.9) (2026-08-13)


### Features

* **central_server,mobaide_agent,web,android,ios:** reversed device-code pairing + account trust-key pin (CLA-199) ([6e56f2f](https://github.com/syrax90/claude_mobile/commit/6e56f2f00a3ee8e22825ed18b7b13dc3e9d95cd1))
* make Claude Code --debug flag opt-in via toggle ([36af32b](https://github.com/syrax90/claude_mobile/commit/36af32b1122b2dfc6e57dcce88841c14c97e8d7e))


### Bug Fixes

* **android,ios,mobaide_agent:** follow status-response deadline, canonicalize typed device codes ([0f23c75](https://github.com/syrax90/claude_mobile/commit/0f23c75095c6365b2c8341d324e368fc4b5cfafd))
* **central_server,mobaide_agent,web:** reserve before pairing, keep the nonce, expire locally ([153fee4](https://github.com/syrax90/claude_mobile/commit/153fee4180b381e090b250273e3ff7eb44fce617))
* **central_server,mobaide_agent:** gate the confirm on terminal approval, drop the rotated tunnel ([956630c](https://github.com/syrax90/claude_mobile/commit/956630c37eeee1da487321535364056b72f9d8a0))
* make Claude Code --debug flag opt-in via toggle ([1fa0e8d](https://github.com/syrax90/claude_mobile/commit/1fa0e8df0713abe5c2852f9dcaf33ba22c96837d))
* **mobaide_agent:** match relay's code alphabet, persist nonce before the confirm prompt ([3768a53](https://github.com/syrax90/claude_mobile/commit/3768a5399566b6f626fc116ee4bc055707bb04eb))

## [0.3.8](https://github.com/syrax90/claude_mobile/compare/agent-v0.3.7...agent-v0.3.8) (2026-08-09)


### Features

* **web,mobaide_agent:** auto scan monitor, attention notifications, and settings (CLA-196) ([d6d257f](https://github.com/syrax90/claude_mobile/commit/d6d257f79ea222db612f4e735486ae45ff75d11f))
* **web:** agents & pairing phase 1 — /pair and /agents routes (CLA-194) ([5dfa073](https://github.com/syrax90/claude_mobile/commit/5dfa073e3badde4cccdbe5a4668613d6cfaec7a9))


### Bug Fixes

* **agent:** stop auto-creating a session for an unknown relay session_id ([a6a6001](https://github.com/syrax90/claude_mobile/commit/a6a60019f88621d51605376bf7784a406131bb8c))
* **agent:** stop auto-creating a session for an unknown relay session_id ([35a433b](https://github.com/syrax90/claude_mobile/commit/35a433bbe65184b8ed93114888bf530407c316fe))

## [0.3.7](https://github.com/syrax90/claude_mobile/compare/agent-v0.3.6...agent-v0.3.7) (2026-08-03)


### Bug Fixes

* **agent:** gate Claude Code Stop hook to stop notification flood ([b92241e](https://github.com/syrax90/claude_mobile/commit/b92241e529bc7aa32f62d74c9340933e629cbc7a))
* **agent:** gate Claude Code Stop hook to stop notification flood ([a53908d](https://github.com/syrax90/claude_mobile/commit/a53908d6526b2bb13a6c35a99dcc297e7ea322af))


### Documentation

* **agent:** clarify why the hook kind rides in the URL, not the body ([8589381](https://github.com/syrax90/claude_mobile/commit/8589381f78363ecba61c7f0df8087c22590f6f46))

## [0.3.6](https://github.com/syrax90/claude_mobile/compare/agent-v0.3.5...agent-v0.3.6) (2026-07-30)


### Features

* **sessions:** remember per-kind launch prefs, live Auto Scan skip-permissions ([7e2721f](https://github.com/syrax90/claude_mobile/commit/7e2721f826346d2512b4dc736f44a49015b8dc3d))


### Bug Fixes

* **android:** default Claude skip-permissions toggle to off ([610ea47](https://github.com/syrax90/claude_mobile/commit/610ea47adf456bbdf5c2aa26d764d7031426d1ea))
* **android:** remember per-kind launch prefs, live Auto Scan skip-permissions ([9754e2b](https://github.com/syrax90/claude_mobile/commit/9754e2b694e10aa7759f9b06f8dbf71740240c2c))
* **sessions:** default Claude skip-permissions toggle to off ([4f169dc](https://github.com/syrax90/claude_mobile/commit/4f169dce665ea7ed57f98d4102172c6daf88aaf0))
* **sessions:** keep wire-absence default true, force bypass for Auto Scan ([9ad4b69](https://github.com/syrax90/claude_mobile/commit/9ad4b69ba38f5f7be0e030b2d9c71a35777f399f))

## [0.3.5](https://github.com/syrax90/claude_mobile/compare/agent-v0.3.4...agent-v0.3.5) (2026-07-29)


### Bug Fixes

* **agent:** gate OpenAPI docs and default-deny undocumented admin/internal routes ([4406c0e](https://github.com/syrax90/claude_mobile/commit/4406c0efc7e8026d73c2f52f45df86d5522f03bd))
* **security:** gate OpenAPI docs and default-deny undocumented admin/internal routes ([fee5d16](https://github.com/syrax90/claude_mobile/commit/fee5d16a6bd025873d5b1159904aa214c82afe47))

## [0.3.4](https://github.com/syrax90/claude_mobile/compare/agent-v0.3.3...agent-v0.3.4) (2026-07-28)


### Bug Fixes

* **agent:** Increase max sessions to 32; rename worktree dir to mobaide_worktree ([f41caa0](https://github.com/syrax90/claude_mobile/commit/f41caa01382abcdeff36708295d2d2a0c6dfda12))

## [0.3.3](https://github.com/syrax90/claude_mobile/compare/agent-v0.3.2...agent-v0.3.3) (2026-07-28)


### Features

* **agent:** add account-scoped custom AI agent types registry (CLA-153) ([e3372d1](https://github.com/syrax90/claude_mobile/commit/e3372d10aaeebeae141160ab6dba694989c43d87))
* **backend:** add account-scoped custom AI agent types registry (CLA-153) ([dd061d1](https://github.com/syrax90/claude_mobile/commit/dd061d1a53b364abe73510c07c0e61151f97ab56))


### Bug Fixes

* **agent:** Fix terminal display-mode gestures and PTY resize propagation ([28ee7c6](https://github.com/syrax90/claude_mobile/commit/28ee7c60ccd9a93aef0f22bd11decfda715a84d5))
* **agent:** make PTY slave the controlling terminal so resizes reach running TUIs ([29dcd46](https://github.com/syrax90/claude_mobile/commit/29dcd46f568ff23def46ce9cb33bcab8f3c2dad1))
* **agent:** strip COLUMNS/LINES from inherited and caller-supplied env ([eeba174](https://github.com/syrax90/claude_mobile/commit/eeba174d45a3701597e6199378f0e3f723071824))

## [0.3.2](https://github.com/syrax90/claude_mobile/compare/agent-v0.3.1...agent-v0.3.2) (2026-07-11)


### Features

* **push:** APNs push pipeline for session attention (CLA-106 / iOS-10) ([14178f4](https://github.com/syrax90/claude_mobile/commit/14178f4ae572f2259151b27c6c49b7092b6d5e1f))
* **push:** APNs push pipeline for session attention (CLA-106 / iOS-10) ([a1f2877](https://github.com/syrax90/claude_mobile/commit/a1f2877251ef9350c99fdade5504c6d13b0007da))

## [0.3.1](https://github.com/syrax90/claude_mobile/compare/agent-v0.3.0...agent-v0.3.1) (2026-07-05)


### Features

* **agent,android:** replace Gemini CLI with Antigravity CLI (agy) [CLA-95] ([ca777e1](https://github.com/syrax90/claude_mobile/commit/ca777e15a5a06b6bb54df3d037c8367d8348925f))
* **agent,android:** replace Gemini CLI with Antigravity CLI (agy) [CLA-95] ([0dadd0f](https://github.com/syrax90/claude_mobile/commit/0dadd0ffc514f63f3523a7b787181c70af7b3379))
* **agent:** add Gemini CLI as a managed CLI agent (CLA-87) ([c3d69be](https://github.com/syrax90/claude_mobile/commit/c3d69be5efec781c5b679c5eb94e32213a396bdc))
* **mobaide_agent:** add Gemini CLI as a managed CLI agent (CLA-87) ([df27b50](https://github.com/syrax90/claude_mobile/commit/df27b50a8896949024fd5435ca3e4dffa9ed53ae))
* **mobaide_agent:** add MOBAIDE_ENV=test agent test-mode (CLA-77) ([1fd9fbf](https://github.com/syrax90/claude_mobile/commit/1fd9fbf9e75a925499189bb6f0e8db9ace26dba3))
* **mobaide_agent:** add MOBAIDE_ENV=test agent test-mode (CLA-77) ([ec1c41c](https://github.com/syrax90/claude_mobile/commit/ec1c41c7453f1164c976dac3a4ac0b177ada786f))


### Bug Fixes

* **mobaide_agent:** address Codex review — Gemini .env auth detection, GEMINI_CLI_HOME, free-form model (CLA-87) ([d6420a1](https://github.com/syrax90/claude_mobile/commit/d6420a1e971f371dd206760a07d01e090faa17a0))
* **mobaide_agent:** anchor test-mode state from resolved config, not os.environ ([2466a39](https://github.com/syrax90/claude_mobile/commit/2466a39df227a9e6e1506ecfb1572ec42a6f3aff))

## [0.3.0](https://github.com/syrax90/claude_mobile/compare/agent-v0.2.19...agent-v0.3.0) (2026-06-26)


### ⚠ BREAKING CHANGES

* **sessions:** wire protocol bumped 5→6. The claude_options envelope field changed shape: dropped max_effort (bool); added model (str), effort (str|null), fast_mode (bool), ultracode (bool), append_system_prompt (str|null). Any relay or client built against proto 5 will fail the version handshake with a proto 6 agent. New sessions default to effort="high" instead of always "max".

### Features

* **sessions:** replace Claude max-effort toggle with model + effort controls (CLA-84) ([3b52a46](https://github.com/syrax90/claude_mobile/commit/3b52a46439d066fc15c95fbd8084d84b79bf195e))


### Bug Fixes

* **android:** Replace the boolean Max effort toggle with rich Claude session controls (CLA-84) ([3b052b1](https://github.com/syrax90/claude_mobile/commit/3b052b1cda8438c98b9375128ac378612c97f46c))

## [0.2.19](https://github.com/syrax90/claude_mobile/compare/agent-v0.2.18...agent-v0.2.19) (2026-06-25)


### Bug Fixes

* **updater:** resolve venv bad-interpreter after atomic release swap ([67151e7](https://github.com/syrax90/claude_mobile/commit/67151e792ae8b40da0f771b0d52d0fb1546871cb))

## [0.2.18](https://github.com/syrax90/claude_mobile/compare/agent-v0.2.17...agent-v0.2.18) (2026-06-25)


### Features

* **agent+android:** gray out Claude/Codex mode buttons when CLI is not installed ([7134c33](https://github.com/syrax90/claude_mobile/commit/7134c33f1480c8e33be6d4c0b0c172380a09096c))
* **android:** gray out Claude/Codex mode buttons when CLI is not installed ([cf95d70](https://github.com/syrax90/claude_mobile/commit/cf95d708274f048bdb9f5dc47e27b36563e6ddfb))

## [0.2.17](https://github.com/syrax90/claude_mobile/compare/agent-v0.2.16...agent-v0.2.17) (2026-06-24)


### Features

* add Codex to the create-session wizard (CLA-69) ([f41919c](https://github.com/syrax90/claude_mobile/commit/f41919c1c51f5191cf754c80d9e2471e108a04f3))
* add Codex to the create-session wizard (CLA-69) ([c7600f6](https://github.com/syrax90/claude_mobile/commit/c7600f6df2a019fec6a37f73ba9376a932a2eaf4))
* **agent:** preserve per-project Auto Scan initial prompt across sessions ([a58be75](https://github.com/syrax90/claude_mobile/commit/a58be75a100c0e0e276f02451b1b1c6a777af114))
* **agent:** preserve per-project Auto Scan initial prompt across sessions ([dc95191](https://github.com/syrax90/claude_mobile/commit/dc9519167d024895b2bbaf90a42126db5ba44e70))
* **codex:** add in-app Codex CLI authentication/login flow (CLA-71) ([63b72b6](https://github.com/syrax90/claude_mobile/commit/63b72b6dd6d1dfb24f81f057b3de95778de05e0e))
* **codex:** add in-app Codex CLI authentication/login flow (CLA-71) ([6f7fb00](https://github.com/syrax90/claude_mobile/commit/6f7fb00953d89d44e9cad2ed17e29e066185b599))
* **installer:** auto-detect Codex binary and write MOBAIDE_CODEX_BIN (CLA-72) ([b9d7922](https://github.com/syrax90/claude_mobile/commit/b9d79224103dd63086161b572fadc3f0730e8583))
* **installer:** auto-detect Codex binary and write MOBAIDE_CODEX_BIN (CLA-72) ([70760f7](https://github.com/syrax90/claude_mobile/commit/70760f7a8f63df24af5c6a3f0695637a3fc857b9))


### Bug Fixes

* **agent:** address Codex review — harden auto_scan_prompt persistence and seeding ([456a5f8](https://github.com/syrax90/claude_mobile/commit/456a5f874779dabed3391306ed16ba2703bc8dee))
* **agent:** carry split compound OSC color queries; scrub compound from ring ([c7f555b](https://github.com/syrax90/claude_mobile/commit/c7f555b7a304b3df7e1071b3b8c1ceca6886fe8c))
* **agent:** handle mixed OSC color query+set controls in OscColorResponder ([2b04023](https://github.com/syrax90/claude_mobile/commit/2b040230874012ad3cbd5a4ba5ab46c4a62fbd8d))
* **agent:** make Auto Scan prompt save best-effort so a persist error can't fail a running scan ([ee2060e](https://github.com/syrax90/claude_mobile/commit/ee2060e56909e4ff99257ccf7d561261c53396fb))
* **agent:** stop OSC color-query responses leaking into the terminal input field ([da915bd](https://github.com/syrax90/claude_mobile/commit/da915bd483416439bc2ff372c437e2238b527ef0))
* **agent:** stop OSC color-query responses leaking into the terminal input field ([a54c208](https://github.com/syrax90/claude_mobile/commit/a54c20851c9f0f2571d7245755ca3a177b953626))
* **codex:** address PR [#270](https://github.com/syrax90/claude_mobile/issues/270) review-round-2 — launcher test CI fix + SecretTextField IME hardening (CLA-71) ([1348d83](https://github.com/syrax90/claude_mobile/commit/1348d8386e92ac0ef311e4ee69bfef5fd57d6db3))
* **codex:** address PR [#270](https://github.com/syrax90/claude_mobile/issues/270) review-round-3 — keyring-aware auth, login timeout, body-level scrub (CLA-71) ([b0b60da](https://github.com/syrax90/claude_mobile/commit/b0b60da02bbe7dc5643e0f711d4d21995d3d948e))
* **codex:** address PR [#270](https://github.com/syrax90/claude_mobile/issues/270) review-round-4 — make `codex login status` authoritative over a stale auth.json (CLA-71) ([fc2e15a](https://github.com/syrax90/claude_mobile/commit/fc2e15a0f9a3d2847dc9ea8f4df2ae7ff2b23d04))
* **codex:** remap auth-required codes to 409/400 to avoid AuthInterceptor retry (CLA-71) ([62d38c4](https://github.com/syrax90/claude_mobile/commit/62d38c4ff5cd96adf6f60afd5c184e2d0366170a))


### Documentation

* **agent:** note OscColorResponder handles pure-query OSC controls only ([27f5950](https://github.com/syrax90/claude_mobile/commit/27f59503f51c4f8e61ebb9847abe6e8c79941ec1))

## [0.2.16](https://github.com/syrax90/claude_mobile/compare/agent-v0.2.15...agent-v0.2.16) (2026-06-23)


### Features

* **agent:** generalize CLI launch abstraction and add Codex CLI support (CLA-68) ([de719f2](https://github.com/syrax90/claude_mobile/commit/de719f27d013f277ede0bac900ca4ff8bd4fb59d))
* **agent:** generalize CLI launch abstraction and add Codex CLI support (CLA-68) ([f9c0d83](https://github.com/syrax90/claude_mobile/commit/f9c0d8375d5a5106cb78561881b9cb93b8c584b3))
* enhance state management for agent configuration and add tests for writable state prefix ([4862dc2](https://github.com/syrax90/claude_mobile/commit/4862dc220902392f74eece81f88728758bd7ebd9))
* enhance state management for agent configuration and add tests for writable state prefix ([c70f4f1](https://github.com/syrax90/claude_mobile/commit/c70f4f1cf41b43e41bda50a71456c1a3bc1f7c40))

## [0.2.15](https://github.com/syrax90/claude_mobile/compare/agent-v0.2.14...agent-v0.2.15) (2026-06-19)


### Bug Fixes

* **agent:** make cross-platform auto-update non-destructive and self-healing ([bf56671](https://github.com/syrax90/claude_mobile/commit/bf56671028154065947369ff62c827165cb4f297))

## [0.2.14](https://github.com/syrax90/claude_mobile/compare/agent-v0.2.13...agent-v0.2.14) (2026-06-19)


### Bug Fixes

* **agent:** add `re-pair --fresh` to recover from missing-agent 404 ([8db9e00](https://github.com/syrax90/claude_mobile/commit/8db9e0058de2c46f7fb98dbd34caf35a011e08e4))
* **pairing:** add `re-pair --fresh` to recover from missing-agent 404 ([a6b261a](https://github.com/syrax90/claude_mobile/commit/a6b261a6fe6b02be6cc173c88222333a2fe1095c))

## [0.2.13](https://github.com/syrax90/claude_mobile/compare/agent-v0.2.12...agent-v0.2.13) (2026-06-17)


### Features

* **agent:** surface macOS file-access (TCC) freezes to the app instead of hanging ([24740f0](https://github.com/syrax90/claude_mobile/commit/24740f0b212e20616ab516f8a18c661f2079cab5))
* **agent:** surface macOS file-access (TCC) freezes to the app instead of hanging ([ce03474](https://github.com/syrax90/claude_mobile/commit/ce034747322213b587cdbb41603f849b2baa2d8f))


### Bug Fixes

* **agent:** prevent session teardown hanging when child ignores SIGTERM ([9b11714](https://github.com/syrax90/claude_mobile/commit/9b117146f1e1ecc7bad87861eb8a80a3c05917d0))
* **agent:** prevent session teardown hanging when child ignores SIGTERM ([d4a18a3](https://github.com/syrax90/claude_mobile/commit/d4a18a3e5693a2ccfc7d673ca51d30673edbd0b8))
* **agent:** select .env by prefix detection to support dev-alongside-installed use case ([f3bfa87](https://github.com/syrax90/claude_mobile/commit/f3bfa87c217b2ecf542f2fd98edb80a068accd7c))
* **agent:** select .env by prefix detection to support dev-alongside-installed use case ([2cd2955](https://github.com/syrax90/claude_mobile/commit/2cd29553f406eaf9bd88da81c760cc0b009e46f0))

## [0.2.12](https://github.com/syrax90/claude_mobile/compare/agent-v0.2.11...agent-v0.2.12) (2026-06-16)


### Bug Fixes

* **agent:** verify relay TLS against certifi instead of host trust store ([5cdd9e1](https://github.com/syrax90/claude_mobile/commit/5cdd9e1c7059d966b47e92e19479eaa339b2cbb9))
* **agent:** verify relay TLS against certifi instead of host trust store ([8f287e8](https://github.com/syrax90/claude_mobile/commit/8f287e8e88e794b062327f6699f3f2fa81c2964f))

## [0.2.11](https://github.com/syrax90/claude_mobile/compare/agent-v0.2.10...agent-v0.2.11) (2026-06-13)


### Bug Fixes

* **agent:** allow force-deleting worktrees with modified/untracked files ([9ab4892](https://github.com/syrax90/claude_mobile/commit/9ab4892a1ff1b8245470f39303d6b88cbe812952))
* **agent:** fix Send button not executing commands without manual Enter ([086699f](https://github.com/syrax90/claude_mobile/commit/086699fd3276f2be494f4a32f8df6820c48c5bc4))
* **git:** allow force-deleting worktrees with modified/untracked files ([003fb66](https://github.com/syrax90/claude_mobile/commit/003fb6673efe7f8b0fe77ac37c19473bf1418d74))
* **terminal:** fix Send button not executing commands without manual Enter ([261fd96](https://github.com/syrax90/claude_mobile/commit/261fd961bf5dbd5b36c2477c72b3016c132e463c))


### Performance Improvements

* **central-server:** capacity hardening — WAL tuning, batched heartbeats, bounded argon2, backoff de-escalation, container limits (CLA-65) ([817f3c0](https://github.com/syrax90/claude_mobile/commit/817f3c06596fba2f8bb7ab98c8414db612c13c58))
* **central-server:** SQLite WAL tuning, batched heartbeats, bounded argon2, reconnect backoff de-escalation, container limits (CLA-65) ([814eb2c](https://github.com/syrax90/claude_mobile/commit/814eb2c19f6b84f3529d035f4897dc20d234e0e2))

## [0.2.10](https://github.com/syrax90/claude_mobile/compare/agent-v0.2.9...agent-v0.2.10) (2026-06-09)


### Features

* **agent:** add Terminal session worktree config page ([755a94b](https://github.com/syrax90/claude_mobile/commit/755a94bc314162c358159a5b29cf7bd0269c5f87))
* **sessions:** add Terminal session worktree config page ([0a65eee](https://github.com/syrax90/claude_mobile/commit/0a65eee9343c8a52dc066ba337acd30001c9d253))

## [0.2.9](https://github.com/syrax90/claude_mobile/compare/agent-v0.2.8...agent-v0.2.9) (2026-06-09)


### Features

* **agent:** pop-up notifications when a Claude session awaits input or ends(CLA-64) ([6410cdb](https://github.com/syrax90/claude_mobile/commit/6410cdb50d471709d1aa560a62b8040e516d1d40))
* pop-up notifications when a Claude session awaits input or ends (CLA-64) ([848af44](https://github.com/syrax90/claude_mobile/commit/848af446a8cb6db7c699c34bbfa0a8093ede7271))

## [0.2.8](https://github.com/syrax90/claude_mobile/compare/agent-v0.2.7...agent-v0.2.8) (2026-06-09)


### Bug Fixes

* **pairing:** release stale device lock and forbid cross-user agent takeover ([01b940c](https://github.com/syrax90/claude_mobile/commit/01b940cf0c9218eedfb63ab3706e72a68cb5672e))
* **pairing:** release stale device lock and forbid cross-user agent takeover ([40392b4](https://github.com/syrax90/claude_mobile/commit/40392b41d02ff780b512aa9de73ef1bbb0b0e6b8))

## [0.2.7](https://github.com/syrax90/claude_mobile/compare/agent-v0.2.6...agent-v0.2.7) (2026-06-08)


### Features

* **launcher:** prune superseded release dirs on every boot ([3f6ea8e](https://github.com/syrax90/claude_mobile/commit/3f6ea8e4e6f9b4b3e09e6719f0d95158c1056f84))

## [0.2.6](https://github.com/syrax90/claude_mobile/compare/agent-v0.2.5...agent-v0.2.6) (2026-05-23)


### Features

* **agent:** self-heal protocol-version mismatch via HTTPS-bootstrap update ([174cd93](https://github.com/syrax90/claude_mobile/commit/174cd936307c53228d8b457e66d910426e9893d7))
* **agent:** self-heal protocol-version mismatch via HTTPS-bootstrap update ([2243d31](https://github.com/syrax90/claude_mobile/commit/2243d31040091f57908092556fb03e36883c5a8d))

## [0.2.5](https://github.com/syrax90/claude_mobile/compare/agent-v0.2.4...agent-v0.2.5) (2026-05-23)


### Bug Fixes

* **android:** replace 256 KB single-frame replay with 4 MB chunked ring buffer (proto v3) ([b1740e9](https://github.com/syrax90/claude_mobile/commit/b1740e92bbe3fcc77edcf620490ae9000985217b))
* **replay:** replace 256 KB single-frame replay with 4 MB chunked ring buffer (proto v3) ([bd8bb63](https://github.com/syrax90/claude_mobile/commit/bd8bb633f83e7a6552b0db15f00291b71ec53504))

## [0.2.4](https://github.com/syrax90/claude_mobile/compare/agent-v0.2.3...agent-v0.2.4) (2026-05-21)


### Features

* **agent:** add Custom Path option to project picker for ad-hoc sessions (CLA-60) ([6ec5287](https://github.com/syrax90/claude_mobile/commit/6ec52878013b2ba1790cec88dc4386c24e6a3631))
* **sessions:** add Custom Path option to project picker for ad-hoc sessions (CLA-60) ([72cb848](https://github.com/syrax90/claude_mobile/commit/72cb848a9552f667d3cf696b58269b2d61bcb0e0))

## [0.2.3](https://github.com/syrax90/claude_mobile/compare/agent-v0.2.2...agent-v0.2.3) (2026-05-20)


### Features

* **agent:** add Terminal session mode as third create-session tile (CLA-59) ([874171a](https://github.com/syrax90/claude_mobile/commit/874171a4078ecb9e67455af0ace9f46c81847197))
* **sessions:** add Terminal session mode as third create-session tile (CLA-59) ([5d0b61e](https://github.com/syrax90/claude_mobile/commit/5d0b61e49d195874de8bf6d28b02d0757757fedc))

## [0.2.2](https://github.com/syrax90/claude_mobile/compare/agent-v0.2.1...agent-v0.2.2) (2026-05-19)


### Features

* **sessions:** show Claude Code OSC terminal title on Android session list ([95f7ea7](https://github.com/syrax90/claude_mobile/commit/95f7ea738aa62e0151d8385c9d5490da7b61c863))


### Bug Fixes

* **agent:** show Claude Code OSC terminal title on Android session list ([969a878](https://github.com/syrax90/claude_mobile/commit/969a878ac2fba99176d3048ab17b61837ae4dcec))

## [0.2.1](https://github.com/syrax90/claude_mobile/compare/agent-v0.2.0...agent-v0.2.1) (2026-05-15)


### Bug Fixes

* **agent:** hide py.exe console window for per-user Scheduled Task ([7494c80](https://github.com/syrax90/claude_mobile/commit/7494c80343cdb47f5cc9d63ee35705dde2a556f2))
* **windows:** hide py.exe console window for per-user Scheduled Task ([9c49515](https://github.com/syrax90/claude_mobile/commit/9c4951506cdf20edd53e71172122f2286a9cd5b4))

## [0.2.0](https://github.com/syrax90/claude_mobile/compare/agent-v0.1.16...agent-v0.2.0) (2026-05-15)


### ⚠ BREAKING CHANGES

* **windows:** the Windows SCM service (MobaideAgent) is removed. On upgrade, install.ps1 detects and unregisters the old service before registering the new Scheduled Task, which runs as the current interactive user instead of SYSTEM.

### Features

* **agent:** replace LocalSystem SCM service with per-user Scheduled Task (CLA-53) ([21d1007](https://github.com/syrax90/claude_mobile/commit/21d1007de612a56dc4b820aac5209d44eaee3e39))
* **windows:** replace LocalSystem SCM service with per-user Scheduled Task (CLA-53) ([761ff72](https://github.com/syrax90/claude_mobile/commit/761ff72069102851e8322e84356b882423851f3d))

## [0.1.16](https://github.com/syrax90/claude_mobile/compare/agent-v0.1.15...agent-v0.1.16) (2026-05-15)


### Bug Fixes

* **agent:** prevent long-paste truncation across Android UI, ViewModel, and PTY backend ([836fd89](https://github.com/syrax90/claude_mobile/commit/836fd898293d930a708e016adc82d03e42a4ab03))

## [0.1.15](https://github.com/syrax90/claude_mobile/compare/agent-v0.1.14...agent-v0.1.15) (2026-05-15)


### Bug Fixes

* **agent:** deliver manifest body inline over relay (proto-v2) ([a7ebd08](https://github.com/syrax90/claude_mobile/commit/a7ebd08837774992fb60f28fbe89891ae1408d26))

## [0.1.14](https://github.com/syrax90/claude_mobile/compare/agent-v0.1.13...agent-v0.1.14) (2026-05-14)


### Bug Fixes

* **agent:** auto-detect & persist claude.cmd for Windows service ([2d3abd8](https://github.com/syrax90/claude_mobile/commit/2d3abd83b3bc1d174d429a5bb3fe51c48504f97e))
* **claude-cli:** auto-detect & persist claude.cmd for Windows service ([6f6471d](https://github.com/syrax90/claude_mobile/commit/6f6471db2a7ac9399bf3c5e45d45ea2a5ffb2df2))

## [0.1.13](https://github.com/syrax90/claude_mobile/compare/agent-v0.1.12...agent-v0.1.13) (2026-05-14)


### Bug Fixes

* **agent:** tolerate non-UTF-8 stdout when rendering pairing QR ([cb0cb1b](https://github.com/syrax90/claude_mobile/commit/cb0cb1bd7ea490909e54c5a8cdcbd7342ff86898))
* **pairing:** tolerate non-UTF-8 stdout when rendering pairing QR ([679944d](https://github.com/syrax90/claude_mobile/commit/679944d29771b2d1b9c5178c86b72462451429a8))

## [0.1.12](https://github.com/syrax90/claude_mobile/compare/agent-v0.1.11...agent-v0.1.12) (2026-05-14)


### Bug Fixes

* **agent:** gate mobile "Paired" on agent.online=true; harden silent failure paths ([a863117](https://github.com/syrax90/claude_mobile/commit/a86311778e090c15da3bdbfe3c17bcb8f5c6a634))
* **pairing:** gate mobile "Paired" on agent.online=true; harden silent failure paths ([b45d31a](https://github.com/syrax90/claude_mobile/commit/b45d31aff702acca8fd3d06701af08911930604b))

## [0.1.11](https://github.com/syrax90/claude_mobile/compare/agent-v0.1.10...agent-v0.1.11) (2026-05-14)


### Bug Fixes

* **agent:** restore agent log observability and close QR-before-WS race ([2d94149](https://github.com/syrax90/claude_mobile/commit/2d9414997294e747d1e30c232830d97d09513108))
* **pairing:** harden agent relay_url comparator (scheme family, default-port normalisation, userinfo/query/fragment rejection) ([4b34cd5](https://github.com/syrax90/claude_mobile/commit/4b34cd5c2f8ec5f1506b314d2fbfab61485a1ca6))
* **pairing:** persist relay_url server-side so dispatch and recovery agree ([4b34cd5](https://github.com/syrax90/claude_mobile/commit/4b34cd5c2f8ec5f1506b314d2fbfab61485a1ca6))
* **pairing:** restore agent log observability and close QR-before-WS race ([4b34cd5](https://github.com/syrax90/claude_mobile/commit/4b34cd5c2f8ec5f1506b314d2fbfab61485a1ca6))

## [0.1.10](https://github.com/syrax90/claude_mobile/compare/agent-v0.1.9...agent-v0.1.10) (2026-05-13)


### Bug Fixes

* **agent:** force SelectorEventLoop on Windows to prevent Proactor WSARecv data-loss ([65da3c8](https://github.com/syrax90/claude_mobile/commit/65da3c87e71d58d0db93cece881a3bc09510fd7a))
* **pairing:** force SelectorEventLoop on Windows to prevent Proactor WSARecv data-loss ([c5b1dd1](https://github.com/syrax90/claude_mobile/commit/c5b1dd109f69dc4cebbb7d88401fd9aaa8166304))

## [0.1.9](https://github.com/syrax90/claude_mobile/compare/agent-v0.1.8...agent-v0.1.9) (2026-05-13)


### Bug Fixes

* **agent:** retain pending nonce on WS failure + retry write inline ([19f42ed](https://github.com/syrax90/claude_mobile/commit/19f42edef8bf46e6514522eec1deb927bcdd6167))
* **pairing:** retain pending nonce on WS failure + retry write inline ([f876146](https://github.com/syrax90/claude_mobile/commit/f876146bf640b21bbd8ca5aeae8245bb2b99df75))

## [0.1.8](https://github.com/syrax90/claude_mobile/compare/agent-v0.1.7...agent-v0.1.8) (2026-05-13)


### Bug Fixes

* **agent:** replace post-pair sys.exit(75) with in-process relay startup ([7abe076](https://github.com/syrax90/claude_mobile/commit/7abe076833cb012be2c0d85af5ef3e4d83f7a813))
* **pairing:** replace post-pair sys.exit(75) with in-process relay startup ([4adbcca](https://github.com/syrax90/claude_mobile/commit/4adbcca68d3dbefac2553c8b37f343751463e7a2))

## [0.1.7](https://github.com/syrax90/claude_mobile/compare/agent-v0.1.6...agent-v0.1.7) (2026-05-13)


### Bug Fixes

* **agent:** write relay credentials to .env after pairing and auto-restart service ([ba7594f](https://github.com/syrax90/claude_mobile/commit/ba7594f65da49162138b3df1a807f5dec349623a))
* **windows:** write relay credentials to .env after pairing and auto-restart service ([7b9c581](https://github.com/syrax90/claude_mobile/commit/7b9c581bf42ae6c99bf06baacee2d6097146bb78))

## [0.1.6](https://github.com/syrax90/claude_mobile/compare/agent-v0.1.5...agent-v0.1.6) (2026-05-13)


### Bug Fixes

* **agent:** preserve launcher PID on Windows by spawning agent as subprocess ([e9fd851](https://github.com/syrax90/claude_mobile/commit/e9fd85151cedfef4e2ac2169b95de9a41b8371cc))
* **launcher:** preserve launcher PID on Windows by spawning agent as subprocess ([46a0a33](https://github.com/syrax90/claude_mobile/commit/46a0a33e32d75bfcf891eabf60ffacada7ac0c2b))

## [0.1.5](https://github.com/syrax90/claude_mobile/compare/agent-v0.1.4...agent-v0.1.5) (2026-05-13)


### Features

* **agent:** render pairing QR only after agent service is confirmed healthy ([cb8bb5b](https://github.com/syrax90/claude_mobile/commit/cb8bb5b7d28b464e4445d02e149eaf19fdfb8d59))
* **install:** render pairing QR only after agent service is confirmed healthy ([8407e59](https://github.com/syrax90/claude_mobile/commit/8407e5908452cf08035d7c829e535fc88e2a6a49))
* **linear:** make Auto Scan Mode filter configurable per project (CLA-52) ([4882d3a](https://github.com/syrax90/claude_mobile/commit/4882d3a09f97c37c3bc9485b86a09cb7e454ff1b))
* **linear:** make Auto Scan Mode filter configurable per project (CLA-52) ([fa18866](https://github.com/syrax90/claude_mobile/commit/fa18866e414a4b6da9abc189f10090675f63e5e2))

## [0.1.4](https://github.com/syrax90/claude_mobile/compare/agent-v0.1.3...agent-v0.1.4) (2026-05-08)


### Bug Fixes

* **pairing:** resolve offline-after-pairing via agent-derived WS URL and sys.exit restart ([cae3ae2](https://github.com/syrax90/claude_mobile/commit/cae3ae2ad559cb8cbe09b7f2da0305f6fd28e2ff))
* **pairing:** resolve offline-after-pairing via agent-derived WS URL and sys.exit restart ([3b88eb2](https://github.com/syrax90/claude_mobile/commit/3b88eb2464ace3a780c1f195d9b19601ba8fcf6a))

## [Unreleased]

### Fixed
- **Offline-after-pairing: pairing WebSocket failed with `"isn't a valid
  URI: scheme isn't ws or wss"`.** The agent now derives the pairing WS
  URL locally from `RELAY_SERVER_URL` + the response `nonce` rather than
  trusting the server's `ws_url` field. Eliminates a deployment-skew
  failure mode where a stale `central_server` returned `https://...` and
  the Python `websockets` client hard-rejected the scheme. New helper:
  `mobaide.agent.pairing_monitor._pairing_ws_url`. Cleartext (`ws://` /
  `http://`) is refused unless `MOBAIDE_AGENT_ALLOW_INSECURE_TRUST_KEY_FETCH=1`,
  mirroring the trust-key fetch's loopback escape hatch.
- **Offline-after-pairing: agent stayed in `pairing_pending` even after
  successful pairing.** The post-pairing `_on_paired` callback used
  `sudo -n systemctl restart mobaide-agent`, which silently no-op'd when
  the install user lacked NOPASSWD (the typical case after
  `curl|sudo bash`); the running process kept its frozen
  empty-credentials config in memory and never started the relay client.
  Replaced with `sys.exit(EXIT_RESTART_REQUESTED)` (exit code 75),
  recognized by the systemd unit's `RestartForceExitStatus=75`, macOS
  launchd's `KeepAlive`, and Windows SC's failure-restart actions —
  same pattern the auto-updater already uses.
- **Auto-update "Install now" always returned `signature_failed`.**
  `central_server` sent ``manifest_url = "/api/v1/agent/manifest.json"``
  as a relative path; `StagedBinaryUpdater._download_manifest` treated
  any ``/``-prefixed value as a local filesystem path
  (``Path(...).read_text()``), which raised `FileNotFoundError`, was
  caught by the broad `except Exception` handler, and returned
  ``"signature_failed"`` — causing every user-triggered update to fail
  silently. Fixed by bumping wire protocol 1→2 (``PROTOCOL_VERSION = 2``):
  the server now delivers the full signed manifest body inline in
  ``auth_ok`` and ``agent_manifest_update`` frames (field ``manifest_body``
  replaces ``manifest_url``). `_download_manifest`, `_http_download`, and
  the `downloader` constructor knob are removed;
  `consider_manifest`/`force_update_now` accept a body dict directly.

## [0.1.3](https://github.com/syrax90/claude_mobile/compare/agent-v0.1.2...agent-v0.1.3) (2026-05-08)


### Bug Fixes

* **agent:** inject User-Agent header to bypass Cloudflare Bot Fight Mode (HTTP 403 / error 1010) ([8094125](https://github.com/syrax90/claude_mobile/commit/80941253c9b648e06ef7dc1c2e73278723e26ded))
* **agent:** inject User-Agent header to bypass Cloudflare Bot Fight Mode (HTTP 403 / error 1010) ([987988a](https://github.com/syrax90/claude_mobile/commit/987988a2db442be91e58b51b6d0d0182eefba0a6))

## [0.1.2] - 2026-05-08

### Added

- **QR pairing flow (CLA-50).** Replaces the legacy
  `mobaide-agent pair --id ... --token ...` invocation with a scan-to-pair
  experience: the agent renders a QR code in its terminal, the operator
  scans it from the Mobaide mobile app, and the agent receives its
  credentials over a server-mediated WebSocket. New CLI verbs:
  - `mobaide-agent pair-qr [--print | --wait] [--prefix] [--no-restart]` —
    initiates a pairing nonce, renders the QR; with `--wait`, blocks on
    `WS /ws/pairing/{nonce}` until the mobile claim arrives, then
    atomically writes credentials and restarts the supervisor.
  - `mobaide-agent show-qr [--prefix]` — re-renders the most-recent
    pending QR; falls back to a fresh initiate if expired.
  - `mobaide-agent re-pair [--prefix] [--no-restart]` — blanks
    `RELAY_AGENT_ID` / `RELAY_AGENT_TOKEN` in `.env` and restarts so the
    boot autorun renders a fresh QR.
  Boot-time autorun: when an agent in `pairing_pending` state starts, the
  app's lifespan schedules a pairing monitor that initiates + renders +
  awaits the claim, then atomic-writes `.env` and restarts the
  supervisor. Replaces the previous "log a warning and idle" behavior.
- New dependency: `segno >= 1.6` for terminal QR rendering.
- `mobaide-agent fetch-trust-key [--from-url URL] [--no-restart] [--prefix PATH]`
  CLI verb. Repair path for installs that were set up before the install
  scripts learned to fetch the manifest-signing public key automatically.
  Default URL is derived from `RELAY_SERVER_URL` in the agent `.env`
  (`wss://host[/path]` → `https://host/api/v1/agent/trust_key`). User-info
  in the URL is rejected (would otherwise silently DNS-resolve to an
  attacker host); cleartext `ws://`/`http://` schemes are refused by
  default (set `MOBAIDE_AGENT_ALLOW_INSECURE_TRUST_KEY_FETCH=1` for
  self-host loopback). The fetched value is validated as a 32-byte
  base64 Ed25519 key before being written to
  `<prefix>/trust/central_server.pub`. Best-effort restarts the
  supervisor so the running agent picks up the new updater wiring.
- Install scripts (`deploy/install.sh`, `deploy/install.ps1`)
  now write a prepared `<prefix>/mobaide_agent/.env` after staging the
  release and render the pairing QR before service start so SSH-attached
  operators can scan it immediately. `JWT_SECRET_KEY` is auto-generated
  and `RELAY_SERVER_URL` is prefilled with `wss://mobaide.com`.
- `python -m mobaide.agent.config init-env [--prefix] [--force]` and
  `print-template` CLI verbs. Idempotent: re-runs without `--force` are
  no-ops; with `--force`, regenerates the file while preserving
  `RELAY_AGENT_ID`, `RELAY_AGENT_TOKEN`, and the existing
  `JWT_SECRET_KEY` (rotating the secret would invalidate live mobile
  JWTs).
- Pairing-pending boot mode: when `RELAY_SERVER_URL` is set without a
  matching id/token pair, the agent boots without crashing, schedules
  the QR pairing monitor, and exposes `pairing_pending: true` on
  `/api/health`. Mirrors Tailscale's idle-until-paired daemon model.

### Removed

- `mobaide-agent pair --id ... --token ...` verb. Replaced by the QR
  pairing flow described above. The mobile app no longer surfaces
  raw `agent_id` / `agent_token` for manual entry.
- `mobaide/agent/env.template` packaged inside the wheel as canonical
  source of truth for the agent `.env` shape. Repo-root
  `mobaide_agent/.env.example` is now a generated mirror; regenerate
  with `python -m mobaide.agent.config print-template > mobaide_agent/.env.example`.
- ``__version__`` exposed via ``importlib.metadata.version("mobaide-agent")``
  and used as the canonical source of truth for ``FastAPI(version=...)`` and
  the wire-handshake ``agent_version`` field.
- ``[project.scripts]`` entries: ``mobaide-agent`` and
  ``mobaide-agent-launcher``. ``mobaide-agent --version``, ``mobaide-agent run``
  (default), and ``mobaide-agent update`` are the new CLI surface.
- ``GET /api/version`` endpoint returning the documented shape with
  ``name="mobaide_agent"`` and ``latest_agent_version=null`` (the central
  server hop is the only one that populates that field).
- ``server_info`` text frame on ``/ws/session/<id>`` carries
  ``agent_version`` + ``protocol_version`` so the phone can render an
  "agent outdated" banner without a separate round trip.
- Auth handshake against ``central_server`` now sends ``protocol_version``,
  ``agent_version``, and a reserved ``capabilities: {}`` placeholder. The
  ``auth_ok`` reply persists ``latest_agent_version`` / ``manifest_url`` /
  ``manifest_signature`` so the staged-binary updater has a rollout pointer.
- New control message ``agent_manifest_update`` lets the central server push
  manifest pointers mid-connection.
- Forward-compat ``unknown_control_type`` counter + WARN logging on the
  fall-through branch of ``RelayClient._handle_control``.
- Staged-binary auto-updater (``mobaide.agent.updater``) — Ed25519
  signature verification, sha256 verification, idle-deferral via
  ``SessionManager``, atomic ``current_version`` swap, exit code 75 to ask
  the supervisor to restart into the new release.
- Launcher shim (``mobaide.launcher.__main__``) that reads
  ``current_version`` and ``os.execv``-es into the matching release venv.
- ``cryptography>=43.0`` runtime dependency for Ed25519 manifest signature
  verification.

### Changed

- **BREAKING:** install scripts (`deploy/install.sh`, `deploy/install.ps1`)
  no longer silently skip manifest signature verification when
  `MOBAIDE_AGENT_TRUST_KEY_B64` is unset. They now derive
  `https://<host>/api/v1/agent/trust_key` from
  `MOBAIDE_AGENT_MANIFEST_URL` and bootstrap the public key from there
  automatically. If neither the env var nor the fetch yields a key, the
  install fails-closed with an actionable message. The previous
  silent-skip branch shipped agents whose auto-updater could never
  apply rollouts (manifests received but never verified) — closing
  that gap. Operators who rely on env-var pinning don't need to change
  anything; the env var still takes priority over the fetch.
- Configuration loading migrated from `python-dotenv` + `os.getenv` to
  `pydantic-settings`. `AgentConfig`, `RelayConfig`, and `AutoScanConfig`
  are now `BaseSettings` subclasses; the agent loads its `.env` from an
  explicit absolute path (`<prefix>/mobaide_agent/.env`) resolved via
  `install_paths.default_prefix()`, fixing the Windows CWD-dependent
  silent miss of the previous `load_dotenv()` call.
- ``deploy/mobaide-agent.service`` ExecStart now points at the launcher
  shim; ``RestartForceExitStatus=75`` is added so a clean staged-binary swap
  triggers a supervisor restart into the new release.

### Removed

- `python-dotenv` runtime dependency (replaced by `pydantic-settings>=2.5`).
- `config/profiles/_template/mobaide_agent.env` (was a hand-mirrored
  duplicate of `.env.example`; the agent `.env` is now generated by
  `init-env` rather than bundled in profiles).
