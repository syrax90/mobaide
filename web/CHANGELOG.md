# Changelog

## [0.1.17](https://github.com/syrax90/claude_mobile/compare/web-v0.1.16...web-v0.1.17) (2026-11-05)


### Bug Fixes

* **server:** throttle password sign-in and require 8+ char passwords ([c40f660](https://github.com/syrax90/claude_mobile/commit/c40f6604595d8063a1b13da2a9410ceac3941c59))
* **server:** throttle password sign-in and require 8+ char passwords ([03773e0](https://github.com/syrax90/claude_mobile/commit/03773e0b42f4ad7044fdcc278efec169682f4268))
* **server:** validate register email and rate limit verification mail ([4f79a5c](https://github.com/syrax90/claude_mobile/commit/4f79a5c5f152c3eea19c63ce11d207ea303c938b))
* **server:** validate register email and rate limit verification mail ([b9eee0b](https://github.com/syrax90/claude_mobile/commit/b9eee0b41298a8359e7f07978ba21eb5ef02af4d))

## [0.1.16](https://github.com/syrax90/claude_mobile/compare/web-v0.1.15...web-v0.1.16) (2026-11-04)


### Features

* drag-to-reorder projects and agents in the new-session wizard ([5ef2a0f](https://github.com/syrax90/claude_mobile/commit/5ef2a0ffa5d135fe81819bf53c04186aaab6118f))
* drag-to-reorder projects and agents in the new-session wizard ([2182e09](https://github.com/syrax90/claude_mobile/commit/2182e0917636f9d520a33fe3d84b2c41fc8f0b03))


### Bug Fixes

* keep reorder saves on their own agent and account, in drop order ([3ac1b31](https://github.com/syrax90/claude_mobile/commit/3ac1b316dfb1d9c17a664c189a087086ebe71cf5))

## [0.1.15](https://github.com/syrax90/claude_mobile/compare/web-v0.1.14...web-v0.1.15) (2026-10-30)


### Bug Fixes

* say a device revoke signs the device out right away ([f384175](https://github.com/syrax90/claude_mobile/commit/f384175a8902544fc8b959dd23939193c34c2ddd))
* **server,android,ios,web:** let abandoned unverified signups register again ([a1f3f94](https://github.com/syrax90/claude_mobile/commit/a1f3f9425134f4e474fb5aa7bc9aa8a7da2762c7))
* **server,android,ios,web:** let abandoned unverified signups register again ([223ba7f](https://github.com/syrax90/claude_mobile/commit/223ba7f11d276469fa4d88ea45d65d2a51df4432))
* **server:** give mobile and dashboard tokens finite default lifetimes ([5d871e6](https://github.com/syrax90/claude_mobile/commit/5d871e6e898c4bcadaa33eda63a89f3da68883b9))
* **server:** give mobile and dashboard tokens finite default lifetimes ([ed2a141](https://github.com/syrax90/claude_mobile/commit/ed2a1415c068a76261a5e5685a542b022674e220))
* **server:** make device revoke stop already-issued tokens ([18fa46e](https://github.com/syrax90/claude_mobile/commit/18fa46e108204db7903cea9eecfa3943169acdf1))
* **server:** make device revoke stop already-issued tokens ([746321a](https://github.com/syrax90/claude_mobile/commit/746321a712439ced89dde62682c61ceff721b1cb))
* **voice:** keep the screen awake during dictation ([f428bdc](https://github.com/syrax90/claude_mobile/commit/f428bdc8b533c5969f464b46d3fb12d98e8cc34d))
* **voice:** keep the screen awake during dictation ([e63c548](https://github.com/syrax90/claude_mobile/commit/e63c548d1ed18927ebb77486a65995d1b313b6e5))
* **web:** keep the device id bound to the restored session ([c3e7c8e](https://github.com/syrax90/claude_mobile/commit/c3e7c8edec104166ab489c0d1d5f3641ec2b241a))
* **web:** keep the terminal at viewport height with a pinned sessions panel ([c8deecf](https://github.com/syrax90/claude_mobile/commit/c8deecfbdbbaeec047b7f489f90126b1cb944d63))
* **web:** keep the terminal at viewport height with a pinned sessions panel ([68a7126](https://github.com/syrax90/claude_mobile/commit/68a712673d8582bb5ad1631214e0193fc98e2406))

## [0.1.14](https://github.com/syrax90/claude_mobile/compare/web-v0.1.13...web-v0.1.14) (2026-10-26)


### Features

* **android,ios,web:** animate the hand-off to the nearest session after stopping the current one ([66942ef](https://github.com/syrax90/claude_mobile/commit/66942ef836df00c70591008a5fc0dc347ab1f822))
* **android,ios,web:** animate the hand-off to the nearest session after stopping the current one ([d785cd5](https://github.com/syrax90/claude_mobile/commit/d785cd51be37b71604a1c24f0325e2f7c8e79394))
* **android,ios,web:** move to the nearest open session after stopping the current one ([19f707e](https://github.com/syrax90/claude_mobile/commit/19f707ed52e4b6f79b38084baf1476b6f43d72ca))
* **android,ios,web:** move to the nearest open session after stopping the current one ([c56f442](https://github.com/syrax90/claude_mobile/commit/c56f442ba532f5a6d778d1ee1c8f0f5cc8fdc39d))

## [0.1.13](https://github.com/syrax90/claude_mobile/compare/web-v0.1.12...web-v0.1.13) (2026-10-23)


### Features

* opt-in on-PC voice input in the agent and Android, iOS and web clients ([#736](https://github.com/syrax90/claude_mobile/issues/736), [#737](https://github.com/syrax90/claude_mobile/issues/737)) ([7fb5482](https://github.com/syrax90/claude_mobile/commit/7fb5482a39eb2a7d4fd33e84c05e3237f23ec175))
* **sessions:** stop sessions from the terminal sessions panel ([943d12d](https://github.com/syrax90/claude_mobile/commit/943d12de76ac16bef8bbe3ef5b1166110ae9f1c0))
* **sessions:** stop sessions from the terminal sessions panel ([789fe2c](https://github.com/syrax90/claude_mobile/commit/789fe2c0e0b651399329c84e5519089ce7b533fb))
* **web:** dictate more inserts at the review's cursor ([#737](https://github.com/syrax90/claude_mobile/issues/737)) ([7ca15d7](https://github.com/syrax90/claude_mobile/commit/7ca15d793371ca6a4ebf487f53ce5c8a41d6971f))
* **web:** mic icon, dictate more and a review opt-out; ~700 MB voice memory ([#737](https://github.com/syrax90/claude_mobile/issues/737)) ([a5b49e8](https://github.com/syrax90/claude_mobile/commit/a5b49e8c0a0c0d56a4a9ec2a84c8d570c4b55403))
* **web:** mic icon, dictate more and an opt-out of the dictation review ([#737](https://github.com/syrax90/claude_mobile/issues/737)) ([63a4d36](https://github.com/syrax90/claude_mobile/commit/63a4d36642ca46f2b131b7b2a741cd6fa8b58669))
* **web:** pin and unpin icons on the sessions panel toggle ([7de57c9](https://github.com/syrax90/claude_mobile/commit/7de57c99aaa1b038906e8496a63f2ac0d330144e))
* **web:** pin and unpin icons on the sessions panel toggle ([ba916a2](https://github.com/syrax90/claude_mobile/commit/ba916a239b9b350b2f332b234c7c2a69eb450f71))


### Bug Fixes

* **sessions:** harden stop-from-panel against races and already-gone sessions ([e581d20](https://github.com/syrax90/claude_mobile/commit/e581d2009f49121890ce257bb5f421fd7e07125e))
* **web:** put the sessions panel stop button inside the row, like Android ([29d6dd4](https://github.com/syrax90/claude_mobile/commit/29d6dd4bc3d53fa097fb35ecc9ec80eaee1e555c))
* **web:** put the sessions panel stop button inside the row, like Android ([777e32b](https://github.com/syrax90/claude_mobile/commit/777e32b0b7d641a0a0dcad32762ad330ea43520e))

## [0.1.12](https://github.com/syrax90/claude_mobile/compare/web-v0.1.11...web-v0.1.12) (2026-10-19)


### Features

* add Focus view toggle and collapsible Advanced group to session launch ([eff1cec](https://github.com/syrax90/claude_mobile/commit/eff1cecbc3b109044c58ba5b54a328de4a611e30))
* add Focus view toggle and collapsible Advanced group to session launch ([166acfa](https://github.com/syrax90/claude_mobile/commit/166acfa071e61ba1a4a35bddea8ac8ba0ffaf8b3))
* sessions panel for switching sessions from the terminal (Android, iOS, web) ([81d847d](https://github.com/syrax90/claude_mobile/commit/81d847d6f67a83c9ea09a8bb125156b0ee684e44))
* sessions panel for switching sessions from the terminal (Android, iOS, web) ([87e572f](https://github.com/syrax90/claude_mobile/commit/87e572fa192a64c7a466d1447596d874cf5f0ee6))


### Bug Fixes

* **web:** add spacing between Claude Code Advanced options ([712d8e4](https://github.com/syrax90/claude_mobile/commit/712d8e4ac1251605b5e8286dec8cc471a67ae722))
* **web:** add spacing between Claude Code Advanced options ([f59745c](https://github.com/syrax90/claude_mobile/commit/f59745cbea65a858b6bed3e8558d91754fdcef5e))

## [0.1.11](https://github.com/syrax90/claude_mobile/compare/web-v0.1.10...web-v0.1.11) (2026-10-17)


### Features

* suggest existing folders while typing project and custom paths ([4f13cfc](https://github.com/syrax90/claude_mobile/commit/4f13cfc1e729eb026f982d3f991a3b8976153e9f))
* suggest existing folders while typing project and custom paths ([6f2bb9e](https://github.com/syrax90/claude_mobile/commit/6f2bb9e1e5db9b14e066e3986031ae4d2ce1f7ea))


### Bug Fixes

* close folder suggestions on Done and word paths for Windows agents ([78740a9](https://github.com/syrax90/claude_mobile/commit/78740a9ffb2271259eaf64c26d9388fb23a6496b))
* close folder suggestions on Done and word paths for Windows agents ([f1ccacc](https://github.com/syrax90/claude_mobile/commit/f1ccacce058bf59f4aa869926e5bb0de8b65abf3))

## [0.1.10](https://github.com/syrax90/claude_mobile/compare/web-v0.1.9...web-v0.1.10) (2026-10-15)


### Features

* let removing an agent also uninstall it from its host ([f2addc4](https://github.com/syrax90/claude_mobile/commit/f2addc424dd34644e34000a360705497431b03d8))
* let removing an agent also uninstall it from its host ([51d77ac](https://github.com/syrax90/claude_mobile/commit/51d77ac05d19d51a39754bf8f7e94a57984ae25f))


### Bug Fixes

* stop a retained hidden client from resizing the PTY when another client attaches ([cb21cfb](https://github.com/syrax90/claude_mobile/commit/cb21cfbf09742c4874446d8c1555a54a855f80c4))
* stop a retained hidden client from resizing the PTY when another client attaches ([e134336](https://github.com/syrax90/claude_mobile/commit/e134336abbab00bfabfc9cecdac56d79fe9eef10))

## [0.1.9](https://github.com/syrax90/claude_mobile/compare/web-v0.1.8...web-v0.1.9) (2026-09-30)


### Bug Fixes

* **web:** declare app.mobaide.com/ as the SPA's canonical URL ([c22bf08](https://github.com/syrax90/claude_mobile/commit/c22bf0824a25177c8f3a7fc007a20691957050f0))
* **web:** declare app.mobaide.com/ as the SPA's canonical URL ([a499bbc](https://github.com/syrax90/claude_mobile/commit/a499bbc858ed77ba114bc13501b66b5d16a6c905))

## [0.1.8](https://github.com/syrax90/claude_mobile/compare/web-v0.1.7...web-v0.1.8) (2026-09-22)


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
* **android,ios,web:** offer only branch-backed rows as a new branch's start point ([739c131](https://github.com/syrax90/claude_mobile/commit/739c131ed3af95ecb3e8908baa881985306c9fbf))

## [0.1.7](https://github.com/syrax90/claude_mobile/compare/web-v0.1.6...web-v0.1.7) (2026-09-14)


### Features

* **agent,android,ios,web:** raise the diff ceiling and add a Full file switch to Git Changes ([2e9ac53](https://github.com/syrax90/claude_mobile/commit/2e9ac53fcea55d382e6daafffbe984a5adaa9034))
* **agent,android,ios,web:** raise the diff ceiling and add a Full file switch to Git Changes ([41758ac](https://github.com/syrax90/claude_mobile/commit/41758ac473dec3772ab8c3c32182e2c1d8b33ced))
* **agent:** add session-scoped read-only git status/diff API ([0f87c33](https://github.com/syrax90/claude_mobile/commit/0f87c338516de5f455c042e4061a717ac1229869))
* **web:** add the in-session Git Changes viewer behind a terminal ⋯ menu ([0947270](https://github.com/syrax90/claude_mobile/commit/0947270606ac977b950535a00ac2fdc693c135cd))
* **web:** create a project from the new-session wizard ([b35034d](https://github.com/syrax90/claude_mobile/commit/b35034d60bdc9c8e72bf6110234e97b09bc6f5bf))
* **web:** create a project from the new-session wizard ([e163a4d](https://github.com/syrax90/claude_mobile/commit/e163a4d1fad243a538a4acb4fdd4334668e4cccb))


### Bug Fixes

* **web,android,ios:** hide the diff Context control when it can't change the diff ([ae0633b](https://github.com/syrax90/claude_mobile/commit/ae0633b123a7981fdac0e4ec100b40bc9f487c4e))

## [0.1.6](https://github.com/syrax90/claude_mobile/compare/web-v0.1.5...web-v0.1.6) (2026-09-04)


### Features

* in-app "Agent paired" conversion via Firebase Analytics (Stage 5) ([50a74de](https://github.com/syrax90/claude_mobile/commit/50a74dea63f69efbc447dee2252ef22b2b30604e))
* **server:** upload an "Agent paired" Google Ads conversion on first pairing ([c4514bb](https://github.com/syrax90/claude_mobile/commit/c4514bbadf249cddaf325641981c7e048b55ddb9))
* **server:** upload an "Agent paired" Google Ads conversion on first pairing ([ba949b0](https://github.com/syrax90/claude_mobile/commit/ba949b0d59d977e53a6f0efc2d76734a566dead9))
* **web:** create a prompt template from inside a session ([c39f066](https://github.com/syrax90/claude_mobile/commit/c39f0662f6d4eef7583411d8fcd01538b21c3876))
* **web:** create a prompt template from inside a session ([a98d33e](https://github.com/syrax90/claude_mobile/commit/a98d33e8f7a512e4f8cf40ad812e7728f12d4092))
* **web:** mirror the first-pairing event and GET /api/auth/me ([6592ba5](https://github.com/syrax90/claude_mobile/commit/6592ba5f526b7a7e6c65f8917def2131938d715e))


### Bug Fixes

* **server:** bind ad consent to the visitor's network, revalidate before retries, guard late consent responses ([615d99e](https://github.com/syrax90/claude_mobile/commit/615d99e9983f3c0e573af0c1c8e5d0e197a7c097))
* **server:** issue the ad-attribution cookie server-side, signed and same-origin only ([22f8818](https://github.com/syrax90/claude_mobile/commit/22f88187875b622d3c54d3079cbc322630056b7e))
* **server:** propagate consent withdrawal, trust Cloudflare edges, revalidate under the send lock, match the click window ([7c7d30b](https://github.com/syrax90/claude_mobile/commit/7c7d30b54dceaa8fbda04cbeb035207cad800b11))
* **web:** derive the pairing origin, and keep a paste when storage is denied ([07f2511](https://github.com/syrax90/claude_mobile/commit/07f2511cd3d4b87f2314d87c2f8f2acd7572fb9c))
* **web:** make the sample payload's relay URL a placeholder too ([b26cac8](https://github.com/syrax90/claude_mobile/commit/b26cac8f8ac4e9ec5acf99143c6d3845874d8f1c))
* **web:** show the "No agents yet" state as an ordered setup guide ([7e74284](https://github.com/syrax90/claude_mobile/commit/7e742844a732c682b96f87c414adf668411962bf))
* **web:** show the "No agents yet" state as an ordered setup guide ([7a442e8](https://github.com/syrax90/claude_mobile/commit/7a442e8f9542c44615f8efb42ce0ddc91987b09a))

## [0.1.5](https://github.com/syrax90/claude_mobile/compare/web-v0.1.4...web-v0.1.5) (2026-08-30)


### Bug Fixes

* **android,ios,web:** never report a paired, online agent as a failed pairing ([efabb00](https://github.com/syrax90/claude_mobile/commit/efabb00ef2ab51219253b1ee17b71e0d80bbfef2))
* **android,ios,web:** never report a paired, online agent as a failed pairing ([8e83bfd](https://github.com/syrax90/claude_mobile/commit/8e83bfd82c03fd1cf4594e997afb50c68bc48389))

## [0.1.4](https://github.com/syrax90/claude_mobile/compare/web-v0.1.3...web-v0.1.4) (2026-08-27)


### Features

* advertise models_pending while the agent's model warmup outlasts the version wait ([e6170d7](https://github.com/syrax90/claude_mobile/commit/e6170d7e83641876c8ef900523e56d63801d8b94))
* **clients:** hide non-actionable account/version details from users ([0986c7b](https://github.com/syrax90/claude_mobile/commit/0986c7b433e2b58f2558930b88fe05b003965d81))
* **clients:** warn that externally started sessions won't appear ([f2a41f8](https://github.com/syrax90/claude_mobile/commit/f2a41f8e3d0a6b62f5dbb55fdb0557e6c116d790))
* **web,central_server:** retire portal auth, point Sign in at the web app ([c8183c5](https://github.com/syrax90/claude_mobile/commit/c8183c59154ea089d78f59655db5f79e049d67da))
* **web:** advance the create wizard as soon as a project is picked ([fab7cf3](https://github.com/syrax90/claude_mobile/commit/fab7cf31c0f021877c03529b4db1df9ce78f74c0))
* **web:** remove the terminal selection toolbar ([cf7df24](https://github.com/syrax90/claude_mobile/commit/cf7df24c00e933f12c29be3787826756f2165a9a))


### Bug Fixes

* **android,ios,web:** reconcile persisted catalog efforts against detail-less agents ([df2393f](https://github.com/syrax90/claude_mobile/commit/df2393f6056a75207e48a5987b9512abaf513f41))
* **android,ios,web:** warn that externally started sessions won't appear ([034f677](https://github.com/syrax90/claude_mobile/commit/034f677d281bfda47e3175794e23b760bf61889d))
* **clients:** hide non-actionable account/version details from users ([bd35511](https://github.com/syrax90/claude_mobile/commit/bd35511ea381ad5b7739eda42f44f33c99d3b890))
* Codex model picker missed current models (gpt-5.6-*) — enumerate without --json, advertise per-model efforts ([7dad428](https://github.com/syrax90/claude_mobile/commit/7dad4285374b5b567fbb56cb7830805d3b41e529))
* harden the codex effort reconcile against races, stale catalogs, and cold caches ([8fd8452](https://github.com/syrax90/claude_mobile/commit/8fd8452cc1f83878d7ad93760592da193aeb210a))
* **mobaide_agent,android,ios,web:** enumerate Codex models without --json, advertise per-model efforts ([bde0c30](https://github.com/syrax90/claude_mobile/commit/bde0c3032fec462ece892c1b94bc4f458499cca1))
* name the session by its label in stop and busy dialogs ([5edb148](https://github.com/syrax90/claude_mobile/commit/5edb1486a35165267e959806fe00b7d8d5253ff4))
* name the session by its label in stop and busy dialogs ([65684b3](https://github.com/syrax90/claude_mobile/commit/65684b37debd5878d5b6cf888f9d0b163a0a2d71))
* **web,central_server:** retire portal auth, point Sign in at the web app ([3608e64](https://github.com/syrax90/claude_mobile/commit/3608e649833ae1e793449e4f0fa9f6ea7a9e71c6))
* **web,ios:** scope the capability cache to the visit, keep a restored codex model renderable ([6c05cd2](https://github.com/syrax90/claude_mobile/commit/6c05cd2db4d40974cb55bf63e889e0e0161bec71))
* **web:** advance the create wizard as soon as a project is picked ([be8759d](https://github.com/syrax90/claude_mobile/commit/be8759d184934606bf1d09e6282fa36d82f0df87))
* **web:** clip the opaque frame around the Google sign-in button ([e275630](https://github.com/syrax90/claude_mobile/commit/e275630a8395f313c1a9239c2a00a55187935195))
* **web:** clip the opaque frame around the Google sign-in button ([b863154](https://github.com/syrax90/claude_mobile/commit/b863154038e6b42796743d59e605bb4361bd195d))
* **web:** copy picked template to system clipboard ([ef8aa28](https://github.com/syrax90/claude_mobile/commit/ef8aa281affa15d81f3041afe4d940bf04edd32b))
* **web:** guard the browser's own copy on an unsettled screen ([4413a31](https://github.com/syrax90/claude_mobile/commit/4413a31ece3db0fc517579b3fdf579c12301ca4b))
* **web:** hide the branch name field until a worktree is requested ([4f3cfff](https://github.com/syrax90/claude_mobile/commit/4f3cfffa314e0c67cc69b91414ec77f022489ab4))
* **web:** hide the branch name field until a worktree is requested ([2d2979b](https://github.com/syrax90/claude_mobile/commit/2d2979b1f7d81da43001768c87ddb4f721d9dca6))
* **web:** keep the browser signed in after the last tab closes ([faabf10](https://github.com/syrax90/claude_mobile/commit/faabf103df5ba2b7241e221e430d9a08edf9a77c))
* **web:** match app icon and header mark to the marketing site ([d58b71a](https://github.com/syrax90/claude_mobile/commit/d58b71a100e90e5162d691b3ef46ceb2a35e2105))
* **web:** re-arm the deletion notice after the login screen reads it ([f26fd01](https://github.com/syrax90/claude_mobile/commit/f26fd0149edc39ce5109a0c0645dea2291a190a7))
* **web:** relay the deletion notice to peer tabs ([fcc0dcb](https://github.com/syrax90/claude_mobile/commit/fcc0dcb9931b25eb84d1c4ec46fd913e1bbd9592))
* **web:** remove the terminal selection toolbar ([eb6d530](https://github.com/syrax90/claude_mobile/commit/eb6d5301d16c5305bd41fe8f78b2100d7c76ca46))
* **web:** replace the theme toggle with a direct three-way picker ([6ddc901](https://github.com/syrax90/claude_mobile/commit/6ddc90148829813260a8f0d1641d47c523ada5e3))
* **web:** replace the theme toggle with a direct three-way picker ([73e3ada](https://github.com/syrax90/claude_mobile/commit/73e3ada46a3cbac80412ca1babf12c9894b6116f))
* **web:** revoke the session cookie on rejected admin login and rescue a transient boot ([a4a41f0](https://github.com/syrax90/claude_mobile/commit/a4a41f078d99888abf43ddf9f69653af78cdb03d))
* **web:** scope the deletion correction to the account it belongs to ([1697050](https://github.com/syrax90/claude_mobile/commit/1697050179b18b57e0b2f2243880cf536807fc86))

## [0.1.3](https://github.com/syrax90/claude_mobile/compare/web-v0.1.2...web-v0.1.3) (2026-08-13)


### Features

* **central_server,mobaide_agent,web,android,ios:** reversed device-code pairing + account trust-key pin (CLA-199) ([6e56f2f](https://github.com/syrax90/claude_mobile/commit/6e56f2f00a3ee8e22825ed18b7b13dc3e9d95cd1))
* make Claude Code --debug flag opt-in via toggle ([36af32b](https://github.com/syrax90/claude_mobile/commit/36af32b1122b2dfc6e57dcce88841c14c97e8d7e))
* purge per-agent recent custom paths on sign-out/account deletion (CLA-258) ([8d7b5a3](https://github.com/syrax90/claude_mobile/commit/8d7b5a3a5f1188c9b431a9ae51c4837552030f4e))
* **web,android,ios:** suspend session PTY sockets when unwatched ([9e385c3](https://github.com/syrax90/claude_mobile/commit/9e385c34509e8f725ff265a66c33e56f4abbeb6b))
* **web:** adopt the portal "modern pixel" design system ([37f1155](https://github.com/syrax90/claude_mobile/commit/37f1155ab43514b7228bdf1fa7abbec9d4d4bca9))
* **web:** draggable scrollback slider in the terminal (WEB-05) ([b7d10bc](https://github.com/syrax90/claude_mobile/commit/b7d10bc8f540b514c2280bf97bb4af3f9dcfd06b))
* **web:** persist per-mode create-session option prefill (CLA-232) ([ee239bb](https://github.com/syrax90/claude_mobile/commit/ee239bbeb6b439120b6f2f7b1d2e6108939623fb))
* **web:** remove shortcuts, command palette and composer; re-home Templates in-session ([cac8c6e](https://github.com/syrax90/claude_mobile/commit/cac8c6efacbbd1d558b6f7b2bc576ce41de46045))
* **web:** remove shortcuts, command palette and composer; re-home Templates in-session ([aeb37a8](https://github.com/syrax90/claude_mobile/commit/aeb37a8cec087f91072aa8741bf7dea1984ad3b0))
* **web:** show recent custom paths in create-session wizard (CLA-256) ([48f59d3](https://github.com/syrax90/claude_mobile/commit/48f59d3a014e025c78d0b34cd035fee2e35a13c0))


### Bug Fixes

* **android,ios,web:** stop replayed OSC 52 from hijacking the clipboard on session open ([309fc93](https://github.com/syrax90/claude_mobile/commit/309fc93d93a95cd1f7ed43010b537c356a4a6f0c))
* **android,ios,web:** stop replayed OSC 52 from hijacking the clipboard on session open ([56a58be](https://github.com/syrax90/claude_mobile/commit/56a58be2ce5d3159e1f110e945c864f77e2e1098))
* **central_server,mobaide_agent,web:** reserve before pairing, keep the nonce, expire locally ([153fee4](https://github.com/syrax90/claude_mobile/commit/153fee4180b381e090b250273e3ff7eb44fce617))
* **central_server,mobaide_agent:** gate the confirm on terminal approval, drop the rotated tunnel ([956630c](https://github.com/syrax90/claude_mobile/commit/956630c37eeee1da487321535364056b72f9d8a0))
* make Claude Code --debug flag opt-in via toggle ([1fa0e8d](https://github.com/syrax90/claude_mobile/commit/1fa0e8df0713abe5c2852f9dcaf33ba22c96837d))
* **web,android,ios:** fix unwatched-suspend regressions from Codex review ([15f37c7](https://github.com/syrax90/claude_mobile/commit/15f37c7860f1962d3306d32d48af58f342481858))
* **web,android,ios:** re-assert PTY size on client-switch edges ([454c420](https://github.com/syrax90/claude_mobile/commit/454c4208bdcfbd4782ace47ba89338e19156cfd5))
* **web,android,ios:** re-assert PTY size on client-switch edges ([7a5fcf0](https://github.com/syrax90/claude_mobile/commit/7a5fcf0db0b920bbc9c4d8d06645276d9bb4dd82))
* **web,ios:** fix inert templates picker via top-layer dialog ([53daf80](https://github.com/syrax90/claude_mobile/commit/53daf80922b328427c933c0a67aa6a09267b8d2f))
* **web,ios:** fix inert templates picker via top-layer dialog ([74b0a6a](https://github.com/syrax90/claude_mobile/commit/74b0a6ae679f07ec049eb926c1e24f7c74b8b61c))
* **web:** address Codex review on the scrollback slider ([5848637](https://github.com/syrax90/claude_mobile/commit/5848637ee88a9a28f275e98f45bf486c30a78aaf))
* **web:** address Codex review round 1 — modal focus return, forced-colors controls, narrow toggle label ([d9826c6](https://github.com/syrax90/claude_mobile/commit/d9826c67b13debe171a9dbd96d6f705ef348f76a))
* **web:** address Codex review round 2 — dialog centering, forced-colors boundaries, backdrop fallback, safe initial focus ([a87d485](https://github.com/syrax90/claude_mobile/commit/a87d48507fd43bc93683c6c4e3d11c5c561b56a0))
* **web:** draggable scrollback slider in the terminal (WEB-05) ([a2c34ec](https://github.com/syrax90/claude_mobile/commit/a2c34ec36c9f98f5a695faad34cb77bb636cde64))
* **web:** keep an in-flight Google sign-in armed across a theme flip ([07bc872](https://github.com/syrax90/claude_mobile/commit/07bc87260e3b5eb5d80247d7b4069db77ff239b7))
* **web:** key drag end on its pointer, drop the idle track out of the hit path ([3ccdd22](https://github.com/syrax90/claude_mobile/commit/3ccdd2221c0218158a904d9ee98cb01c6f041cc5))
* **web:** line the agent status and actions up into columns ([ffa2100](https://github.com/syrax90/claude_mobile/commit/ffa21006c1f98c4c5ff2db7b478e08b8db34009b))
* **web:** line the agent status and actions up into columns ([3e676ea](https://github.com/syrax90/claude_mobile/commit/3e676ea80eb90958804de58bd897ef94e2a75e41))
* **web:** make template/selection inserts refusal-safe; jump to tail on insert ([d0b3582](https://github.com/syrax90/claude_mobile/commit/d0b35822ca95e75dd85145978ae982033ac251fa))
* **web:** show a working scroll control in full-screen sessions ([23cb5f0](https://github.com/syrax90/claude_mobile/commit/23cb5f04fa2684e868402f4e7843b270c0e282a1))
* **web:** show a working scroll control in full-screen sessions ([6dc1823](https://github.com/syrax90/claude_mobile/commit/6dc1823982d0723dd9e1ba6a35df4cc20206170a))
* **web:** show recent custom paths in create-session wizard (CLA-256) ([2d6cbb8](https://github.com/syrax90/claude_mobile/commit/2d6cbb8977ee372d11a329d35ff4e0e86347ccea))
* **web:** undamped pixel wheel and focus-safe track paging ([5ec3af2](https://github.com/syrax90/claude_mobile/commit/5ec3af24a529d7f576b0659f49f5b22edaa76d19))

## [0.1.2](https://github.com/syrax90/claude_mobile/compare/web-v0.1.1...web-v0.1.2) (2026-08-09)


### Features

* **web,android,ios:** desktop keyboard & shortcuts, shared chord fixture, CLA-158 (CLA-197) ([1157916](https://github.com/syrax90/claude_mobile/commit/115791676121559b70ad5310b3a928bcaa994a42))
* **web,central_server:** Sign in with Apple on the SPA and portal (CLA-198) ([edd1c59](https://github.com/syrax90/claude_mobile/commit/edd1c59fcc5ed9be14f93bdff5b5c87ef152c0af))
* **web,mobaide_agent:** auto scan monitor, attention notifications, and settings (CLA-196) ([d6d257f](https://github.com/syrax90/claude_mobile/commit/d6d257f79ea222db612f4e735486ae45ff75d11f))
* **web:** agents & pairing phase 1 — /pair and /agents routes (CLA-194) ([5dfa073](https://github.com/syrax90/claude_mobile/commit/5dfa073e3badde4cccdbe5a4668613d6cfaec7a9))
* **web:** projects, git items & prompt templates — CRUD, accessible reorder, tri-state updates (CLA-195) ([a2fd525](https://github.com/syrax90/claude_mobile/commit/a2fd525999439290564a4fb63a187585c540e193))
* **web:** realtime protocol core — WS manager, binary terminal codec, golden fixtures in CI (CLA-191) ([6807e5d](https://github.com/syrax90/claude_mobile/commit/6807e5d9b1b02999c3fdfd3a7d2ddd4205bb6caf))
* **web:** sessions list, auto-resume and the six-branch create wizard (CLA-193) ([9c1bac4](https://github.com/syrax90/claude_mobile/commit/9c1bac4b9a828b1ec9f53ccf9dce9f1e929d44ab))
* **web:** terminal screen on xterm.js 6 — replay, follow mode, unread badge, copy/paste (CLA-192) ([69495ad](https://github.com/syrax90/claude_mobile/commit/69495ada1d8120d52e937c14cfbb1596705ae3be))


### Bug Fixes

* **agent:** stop auto-creating a session for an unknown relay session_id ([a6a6001](https://github.com/syrax90/claude_mobile/commit/a6a60019f88621d51605376bf7784a406131bb8c))
* **agent:** stop auto-creating a session for an unknown relay session_id ([35a433b](https://github.com/syrax90/claude_mobile/commit/35a433bbe65184b8ed93114888bf530407c316fe))
* **android,web:** resolve the header label on cold start, drop it on route change ([01e696e](https://github.com/syrax90/claude_mobile/commit/01e696ebcad1e717e3bd3bcccc7d98339cc1c355))
* **ios,android,web:** name the terminal header by session title, not id ([0ea96ff](https://github.com/syrax90/claude_mobile/commit/0ea96ff697d7c9fdb3a9aadcd3c62b030fbe3a0b))
* **ios,android,web:** name the terminal header by session title, not id ([b32a097](https://github.com/syrax90/claude_mobile/commit/b32a097bfd7cfb26472d95e7d12eb67c8d26fa30))
* **web:** address Codex review round 1 on CLA-191 — token refresh outcome, agent token rotation, reachability probe-gate, version re-probe ([5f57582](https://github.com/syrax90/claude_mobile/commit/5f57582af7a8c9364000b97de06d387d369fc329))
* **web:** address Codex review round 1 on CLA-192 — IME Enter, LRU touch, reattach size, replay gates ([5b7b7ba](https://github.com/syrax90/claude_mobile/commit/5b7b7ba03b2a098f508dbe3d9bf38ade773319b0))
* **web:** address Codex review round 1 on CLA-193 - agent-token minting, snapshot race, add_dirs gate ([bba9ebb](https://github.com/syrax90/claude_mobile/commit/bba9ebb79e702ec90797bf78e328ecdc49e7adcc))
* **web:** address Codex review round 2 on CLA-191 — reachability probe races, unbounded agent retry, cross-tab token resurrection ([e063ee3](https://github.com/syrax90/claude_mobile/commit/e063ee30f2a4de973bb61350978642d87bd2323e))
* **web:** address Codex review round 2 on CLA-192 — floating selection bar, stdin gate mirrored into xterm ([6e8340d](https://github.com/syrax90/claude_mobile/commit/6e8340d1e65cbb15f65e63a12c92d543582b43f3))
* **web:** address Codex review round 2 on CLA-193 — draft/URL reconciliation, E2E token stub ([5a8b2f5](https://github.com/syrax90/claude_mobile/commit/5a8b2f5f1d4aa3cf2046ae481ed2091700e38a21))
* **web:** address Codex review round 3 on CLA-191 — onlineManager ownership, agent token storage-fallback merge, fractional seq truncation ([70212e4](https://github.com/syrax90/claude_mobile/commit/70212e4e5fc4c591674075418f109635d275e019))
* **web:** address Codex review round 3 on CLA-192 — restore terminal focus after selection actions, guard the favourite toggle ([83be3d3](https://github.com/syrax90/claude_mobile/commit/83be3d30f3a153dc818ebd5bbf89ec828b148735))
* **web:** address Codex review round 3 on CLA-193 — effort-ladder parity, offline guards, model options ([a3af34a](https://github.com/syrax90/claude_mobile/commit/a3af34a62d4fc56947904151cb808716d1b96393))
* **web:** address Codex review round 4 on CLA-192 — fixed dark terminal palette, replay-drain guard for all selection actions, composer focus ([3383a2f](https://github.com/syrax90/claude_mobile/commit/3383a2f27d4e729879c38ec1b01fd7ade60c5d17))
* **web:** address Codex review round 4 on CLA-193 — cache races and in-flight guards ([8211388](https://github.com/syrax90/claude_mobile/commit/82113887589bc5a1978916a9161f2a9c7e9446f3))
* **web:** address Codex review round 5 on CLA-192 — per-row favourite guard, composer focus on template insert ([fc1abb7](https://github.com/syrax90/claude_mobile/commit/fc1abb702d24d099c64669b7b6e6d412bb2d4a40))
* **web:** address Codex review round 5 on CLA-193 — close the Codex dialog on a non-auth failure ([d20797d](https://github.com/syrax90/claude_mobile/commit/d20797d49d7681bca156084899065dba33c18276))
* **web:** use the destructured binding in clearSnapshot so eslint passes ([458c578](https://github.com/syrax90/claude_mobile/commit/458c578496f3be9901f6f17d0958e176aad82fb6))

## [0.1.1](https://github.com/syrax90/claude_mobile/compare/web-v0.1.0...web-v0.1.1) (2026-08-03)


### Features

* **web:** auth & account — email/password + Google Sign-In, dual-axis tokens, devices, account deletion (CLA-190) ([7ab4df6](https://github.com/syrax90/claude_mobile/commit/7ab4df654dae97cf326c1679329c8864b53b8606))
* **web:** bootstrap web/ workspace (WEB-01) ([31cc00e](https://github.com/syrax90/claude_mobile/commit/31cc00ead90e8cfba269271c729ce91a2ae44170))
* **web:** bootstrap web/ workspace (WEB-01) ([c52b6ce](https://github.com/syrax90/claude_mobile/commit/c52b6ced2be7a968de05b9586f6d57068c4effb5))


### Bug Fixes

* **server,web:** address Codex review on CLA-190 — schema migration, refresh races, GIS recovery ([a25c44f](https://github.com/syrax90/claude_mobile/commit/a25c44ff1d0803b4ca79352e742cd0b92cd75a84))
* **server,web:** address Codex review round 2 on CLA-190 — cross-account session, grace copy, resend cooldown ([e9e7bb6](https://github.com/syrax90/claude_mobile/commit/e9e7bb6eb0b53a9d068cd70c836be5826ab8d138))
* **server,web:** address Codex review round 4 on CLA-190 — alembic mirror, iOS UA detection, delete copy ([622b5e7](https://github.com/syrax90/claude_mobile/commit/622b5e7fb4e4806030e5875f608d561e45f7b8e8))
* **web:** address Codex review round 3 on CLA-190 — cross-account query cache, agent-token memory fallback ([0749efd](https://github.com/syrax90/claude_mobile/commit/0749efdd3dba4926b97d5ed59f33f3ab2d3d7f90))
* **web:** address Codex review round 5 on CLA-190 — boot/logout race, late GIS credential, agent-token write guard ([59cd0b2](https://github.com/syrax90/claude_mobile/commit/59cd0b281a7c377b931038f25f402771c57a510a))
* **web:** sanitize persisted theme preference on hydration ([59bc9bf](https://github.com/syrax90/claude_mobile/commit/59bc9bff7f85d456ddaad518fae6ab0d2e5ad278))
* **web:** split API auth into user/agent token seams; narrow engines to lockfile intersection ([9af400f](https://github.com/syrax90/claude_mobile/commit/9af400f0d1a1b7a1a35ea3250d6fd2c41cddab39))
