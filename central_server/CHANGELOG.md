# central_server Changelog

## [0.2.13](https://github.com/syrax90/claude_mobile/compare/server-v0.2.12...server-v0.2.13) (2026-08-27)


### Features

* **server:** add consent-gated Google Ads tag to the landing page ([dae3b8a](https://github.com/syrax90/claude_mobile/commit/dae3b8ae8a9815b458bbb0e0a3ab5e8d1ede6964))
* **server:** address Codex review on the consent-gated Ads tag ([9815e8b](https://github.com/syrax90/claude_mobile/commit/9815e8b4349bb67028de41d1ae964f3c811339f1))
* **server:** consent-gated Google Ads tag on the landing page ([9272387](https://github.com/syrax90/claude_mobile/commit/9272387fe983dadaa0471f719dce8a64fc3dfa6f))
* **server:** one-click consent withdrawal for the Ads tag ([d5ef0eb](https://github.com/syrax90/claude_mobile/commit/d5ef0eb0eebf7adf6c4a87eca0e145d7cb1b1ffd))
* **server:** paginate and sort the admin dashboard users table ([398c220](https://github.com/syrax90/claude_mobile/commit/398c2208ab65f99004df1fc1896ddaf3cd03ea01))
* **web,central_server:** retire portal auth, point Sign in at the web app ([c8183c5](https://github.com/syrax90/claude_mobile/commit/c8183c59154ea089d78f59655db5f79e049d67da))
* **website:** add copy buttons to the install one-liners ([2a26162](https://github.com/syrax90/claude_mobile/commit/2a26162adf441f5a9a37de27422205dc30fc0b9f))


### Bug Fixes

* **server:** close the dashboard search/sort race and fold the email search symmetrically ([340dd94](https://github.com/syrax90/claude_mobile/commit/340dd949125154006ea79bd89839f29b46e0be14))
* **server:** serialise the dashboard delete refresh behind in-flight panel requests ([6046cb0](https://github.com/syrax90/claude_mobile/commit/6046cb0fffbb2e0e78022c94c1685213ed21dac1))
* **web,central_server:** retire portal auth, point Sign in at the web app ([3608e64](https://github.com/syrax90/claude_mobile/commit/3608e649833ae1e793449e4f0fa9f6ea7a9e71c6))
* **web:** keep the browser signed in after the last tab closes ([faabf10](https://github.com/syrax90/claude_mobile/commit/faabf103df5ba2b7241e221e430d9a08edf9a77c))
* **website:** add copy buttons to the install one-liners ([db460f7](https://github.com/syrax90/claude_mobile/commit/db460f799267a92e95c064fb783d192aa1cd3ac3))
* **website:** drop the direct/LAN mode claims from the landing page ([9246e47](https://github.com/syrax90/claude_mobile/commit/9246e47e67edf76c7189977f153d454afb1979e3))
* **website:** drop the direct/LAN mode claims from the landing page ([323be3c](https://github.com/syrax90/claude_mobile/commit/323be3cf43672ab8f4be5a8d6e34bc19bf6bcf12))
* **website:** harden the copy button against a stale-write race ([591f056](https://github.com/syrax90/claude_mobile/commit/591f056c3d562bc039eb80806837039fc4c8812d))
* **website:** keep the "by default" qualifier on the agent bind address ([7a7c61d](https://github.com/syrax90/claude_mobile/commit/7a7c61da8c37fc567532a7c2ec752c61d813da32))


### Performance Improvements

* **server:** compute the dashboard last-activity sort key only for that sort ([54e3e47](https://github.com/syrax90/claude_mobile/commit/54e3e4713947a2305d0b0949a15eb1dbab023e78))

## [0.2.12](https://github.com/syrax90/claude_mobile/compare/server-v0.2.11...server-v0.2.12) (2026-08-13)


### Features

* **central_server,mobaide_agent,web,android,ios:** reversed device-code pairing + account trust-key pin (CLA-199) ([6e56f2f](https://github.com/syrax90/claude_mobile/commit/6e56f2f00a3ee8e22825ed18b7b13dc3e9d95cd1))
* purge per-agent recent custom paths on sign-out/account deletion (CLA-258) ([8d7b5a3](https://github.com/syrax90/claude_mobile/commit/8d7b5a3a5f1188c9b431a9ae51c4837552030f4e))


### Bug Fixes

* **central_server,mobaide_agent,web:** reserve before pairing, keep the nonce, expire locally ([153fee4](https://github.com/syrax90/claude_mobile/commit/153fee4180b381e090b250273e3ff7eb44fce617))
* **central_server,mobaide_agent:** gate the confirm on terminal approval, drop the rotated tunnel ([956630c](https://github.com/syrax90/claude_mobile/commit/956630c37eeee1da487321535364056b72f9d8a0))
* **central_server:** clear push tokens when scheduled account deletion starts (CLA-128) ([e65fa36](https://github.com/syrax90/claude_mobile/commit/e65fa36993f6600aac8f92760cb62795ccb06348))
* **central_server:** clear push tokens when scheduled account deletion starts (CLA-128) ([7b7b9e1](https://github.com/syrax90/claude_mobile/commit/7b7b9e13316d87f4e612bd47275c388fb9454904))


### Documentation

* **privacy:** fix factual claims added in this PR (CLA-258) ([a5e577b](https://github.com/syrax90/claude_mobile/commit/a5e577b256ceb9e35338e3cda28e743daf184488))

## [0.2.11](https://github.com/syrax90/claude_mobile/compare/server-v0.2.10...server-v0.2.11) (2026-08-09)


### Features

* **central_server:** add Cross-Origin-Opener-Policy and Permissions-Policy headers ([538db80](https://github.com/syrax90/claude_mobile/commit/538db8083a866db9e4130677d34aa9a5f7daffab))
* **web,central_server:** Sign in with Apple on the SPA and portal (CLA-198) ([edd1c59](https://github.com/syrax90/claude_mobile/commit/edd1c59fcc5ed9be14f93bdff5b5c87ef152c0af))


### Bug Fixes

* **ios,central_server:** stop stale attention alerts from re-firing on app open ([79d3dd1](https://github.com/syrax90/claude_mobile/commit/79d3dd191ed5f3f6d971839eee92513d560103d4))
* **ios,central_server:** stop stale attention alerts from re-firing on app open ([8d786c4](https://github.com/syrax90/claude_mobile/commit/8d786c4e0fbbf0970edb156b7244725427489ce4))

## [0.2.10](https://github.com/syrax90/claude_mobile/compare/server-v0.2.9...server-v0.2.10) (2026-08-03)


### Features

* **server:** app.mobaide.com origin + relay readiness (CLA-189 / WEB-02) ([83b303e](https://github.com/syrax90/claude_mobile/commit/83b303eaebee471db7dc8ac5b06c13a1501124ee))
* **web:** auth & account — email/password + Google Sign-In, dual-axis tokens, devices, account deletion (CLA-190) ([7ab4df6](https://github.com/syrax90/claude_mobile/commit/7ab4df654dae97cf326c1679329c8864b53b8606))


### Bug Fixes

* **server,web:** address Codex review on CLA-190 — schema migration, refresh races, GIS recovery ([a25c44f](https://github.com/syrax90/claude_mobile/commit/a25c44ff1d0803b4ca79352e742cd0b92cd75a84))
* **server,web:** address Codex review round 2 on CLA-190 — cross-account session, grace copy, resend cooldown ([e9e7bb6](https://github.com/syrax90/claude_mobile/commit/e9e7bb6eb0b53a9d068cd70c836be5826ab8d138))
* **server,web:** address Codex review round 4 on CLA-190 — alembic mirror, iOS UA detection, delete copy ([622b5e7](https://github.com/syrax90/claude_mobile/commit/622b5e7fb4e4806030e5875f608d561e45f7b8e8))
* **server:** address Codex review round 1 on CLA-189 relay readiness ([4b8935b](https://github.com/syrax90/claude_mobile/commit/4b8935b2e79b6b9be9a38761517b3c82a77da8aa))
* **server:** address Codex review round 2 on CLA-189 relay readiness ([e62fc46](https://github.com/syrax90/claude_mobile/commit/e62fc46454ed9a9dabef25d832440471a351a0f4))

## [0.2.9](https://github.com/syrax90/claude_mobile/compare/server-v0.2.8...server-v0.2.9) (2026-07-31)


### Bug Fixes

* **central_server:** widen verification code input field ([7d9eb4b](https://github.com/syrax90/claude_mobile/commit/7d9eb4be1741f85f0cb06c275d3f15e0ecaad312))
* **central_server:** widen verification code input field ([8d50bac](https://github.com/syrax90/claude_mobile/commit/8d50bace3a911fb09965196b8cddde713f1aeb8b))

## [0.2.8](https://github.com/syrax90/claude_mobile/compare/server-v0.2.7...server-v0.2.8) (2026-07-30)


### Features

* **central_server:** adopt site-wide security headers and CSP ([32a53a1](https://github.com/syrax90/claude_mobile/commit/32a53a1941df5783b5657db359a135e8f519cac9))
* **central_server:** adopt site-wide security headers and CSP ([50f70c3](https://github.com/syrax90/claude_mobile/commit/50f70c3b7143cbd2e1b56711da1044b82426bd0e))
* **marketing:** serve pixel-styled mobaide.com landing page from central_server ([17bfeb8](https://github.com/syrax90/claude_mobile/commit/17bfeb870506139777ea7eaa908b7a1a8a2bf217))
* **marketing:** serve pixel-styled mobaide.com landing page from central_server ([ac41546](https://github.com/syrax90/claude_mobile/commit/ac415469844761d70c3baa7449f8a5aaf4b3404d))
* **portal:** restyle the web portal to the marketing pixel design system ([f9c0913](https://github.com/syrax90/claude_mobile/commit/f9c0913deeced170c010cfd7ee5f85e418f8e29d))
* **portal:** restyle the web portal to the marketing pixel design system ([0fe82aa](https://github.com/syrax90/claude_mobile/commit/0fe82aa38cb7de1a82ee4614c3dea1e02e6cbe8a))


### Bug Fixes

* **legal:** add support@mobaide.com to the privacy policy ([951311b](https://github.com/syrax90/claude_mobile/commit/951311be4f19074680c3cebb7bdf158a310efc9f))
* **legal:** add support@mobaide.com to the privacy policy ([47412af](https://github.com/syrax90/claude_mobile/commit/47412af86955aae389b0cd57869bab20a9c9e50a))
* **marketing:** correct false landing-page claims and cache versioning from PR review ([fa8849c](https://github.com/syrax90/claude_mobile/commit/fa8849c21423a885771935518c758a447873f015))
* **marketing:** keep the deletion page crawlable, scope README's WSS claim ([8f21cf3](https://github.com/syrax90/claude_mobile/commit/8f21cf310f4503c9bbb1f6a1bb570dae878c5937))
* **marketing:** scope the TLS claim to relay mode, hash the whole asset tree ([479f21a](https://github.com/syrax90/claude_mobile/commit/479f21aa23237362b5574fdf4f45e155d9a2a86f))
* **security:** gate /api/health agent counts behind admin auth ([dbcb596](https://github.com/syrax90/claude_mobile/commit/dbcb596a802dd575bcbf3fd6ec3737aa5a91fc0e))
* **security:** gate /api/health agent counts behind admin auth ([d19d61e](https://github.com/syrax90/claude_mobile/commit/d19d61ec3a1c4d195b8b048f82e9a8c019b33146))

## [0.2.7](https://github.com/syrax90/claude_mobile/compare/server-v0.2.6...server-v0.2.7) (2026-07-29)


### Bug Fixes

* **agent:** gate OpenAPI docs and default-deny undocumented admin/internal routes ([4406c0e](https://github.com/syrax90/claude_mobile/commit/4406c0efc7e8026d73c2f52f45df86d5522f03bd))
* **security:** gate OpenAPI docs and default-deny undocumented admin/internal routes ([fee5d16](https://github.com/syrax90/claude_mobile/commit/fee5d16a6bd025873d5b1159904aa214c82afe47))

## [0.2.6](https://github.com/syrax90/claude_mobile/compare/server-v0.2.5...server-v0.2.6) (2026-07-28)


### Features

* **agent:** add account-scoped custom AI agent types registry (CLA-153) ([e3372d1](https://github.com/syrax90/claude_mobile/commit/e3372d10aaeebeae141160ab6dba694989c43d87))
* **backend:** add account-scoped custom AI agent types registry (CLA-153) ([dd061d1](https://github.com/syrax90/claude_mobile/commit/dd061d1a53b364abe73510c07c0e61151f97ab56))
* **ios:** add custom keyboard shortcut chord picker and synced saved shortcuts (CLA-136, CLA-138) ([a226562](https://github.com/syrax90/claude_mobile/commit/a22656231b91c38e322b35a3f8ade9667df10da0))
* **ios:** add custom keyboard shortcut chord picker and synced saved shortcuts (CLA-136, CLA-138) ([3ab4788](https://github.com/syrax90/claude_mobile/commit/3ab47884cfcb4f90b8b894c3e06e52b7379a4d4e))


### Bug Fixes

* **backend:** gate custom agent-type launch on live tunnel version, reject embedded NUL ([beab1ad](https://github.com/syrax90/claude_mobile/commit/beab1ad243b1d3903f4c3aae5bef41d6b19ce06d))
* **backend:** roll back aborted custom-agent-type/shortcut writes and fix 409 body shape ([fa1fee6](https://github.com/syrax90/claude_mobile/commit/fa1fee6fe589c7e8abeba6618b39187c67210137))

## [0.2.5](https://github.com/syrax90/claude_mobile/compare/server-v0.2.4...server-v0.2.5) (2026-07-24)


### Bug Fixes

* **central-server:** close template tombstone resurrection race in update/touch ([46a59e3](https://github.com/syrax90/claude_mobile/commit/46a59e3936e5c5e60e736066e9be6e8c988ee9b8))
* **central-server:** close template tombstone resurrection race in update/touch ([e243061](https://github.com/syrax90/claude_mobile/commit/e24306109b4394f820ae0956d48ade57559d4015))
* **central-server:** make mobile device registry last-login-wins ([42e9e37](https://github.com/syrax90/claude_mobile/commit/42e9e37f1fdce5652a09b9bab505285ac47188bd))
* **central-server:** make mobile device registry last-login-wins ([060e5b2](https://github.com/syrax90/claude_mobile/commit/060e5b2a0af97514f23275247df0c12d0f578287))

## [0.2.4](https://github.com/syrax90/claude_mobile/compare/server-v0.2.3...server-v0.2.4) (2026-07-22)


### Features

* add persistent named custom shortcut buttons synced via central server ([8ed089a](https://github.com/syrax90/claude_mobile/commit/8ed089abd66bcaa404559ee4d1840636aa659359))
* add persistent named custom shortcut buttons synced via central server ([04bde6f](https://github.com/syrax90/claude_mobile/commit/04bde6fb0738973d4eadab5ec7398b91823b3d9d))


### Bug Fixes

* address third Codex review round for saved shortcut buttons (CLA-137) ([740dd2f](https://github.com/syrax90/claude_mobile/commit/740dd2fc018315f0ee179edbfdb4f0ea913787b8))
* resolve review findings for custom shortcut persistence (CLA-137) ([4b90ef2](https://github.com/syrax90/claude_mobile/commit/4b90ef28ceef21993d9ff193d4e83e4356ff1e36))
* **server:** close TOCTOU window in update_user_shortcut_name readback ([45e950a](https://github.com/syrax90/claude_mobile/commit/45e950a27a585385aee8d3b3618353d69b057dd4))

## [0.2.3](https://github.com/syrax90/claude_mobile/compare/server-v0.2.2...server-v0.2.3) (2026-07-11)


### Features

* **ios,server:** onboarding UI (6 screens), Google Sign-In + Sign in with Apple, /api/auth/apple ([46656a9](https://github.com/syrax90/claude_mobile/commit/46656a93520db5d1ed5b013d9b490b67d6294dd8))
* **ios,server:** onboarding UI (6 screens), Google Sign-In + Sign in with Apple, /api/auth/apple ([4cad459](https://github.com/syrax90/claude_mobile/commit/4cad459db29c09e76d6495237c583323b6329a99))
* **push:** APNs push pipeline for session attention (CLA-106 / iOS-10) ([14178f4](https://github.com/syrax90/claude_mobile/commit/14178f4ae572f2259151b27c6c49b7092b6d5e1f))
* **push:** APNs push pipeline for session attention (CLA-106 / iOS-10) ([a1f2877](https://github.com/syrax90/claude_mobile/commit/a1f2877251ef9350c99fdade5504c6d13b0007da))


### Bug Fixes

* **central_server:** allow PATCH through relay mobile proxy ([65f45c6](https://github.com/syrax90/claude_mobile/commit/65f45c6c2d0dc00b0626a6d11b8e674c2a8ad894))
* **central_server:** allow PATCH through relay mobile proxy ([0a9a7a5](https://github.com/syrax90/claude_mobile/commit/0a9a7a5c3216069c4ef7e0abbd21bfb8ca2eb456))

## [0.2.2](https://github.com/syrax90/claude_mobile/compare/server-v0.2.1...server-v0.2.2) (2026-06-30)


### Features

* **central_server:** add MOBAIDE_ENV=test selector (CLA-76) ([3d25b11](https://github.com/syrax90/claude_mobile/commit/3d25b11af914993ce3093b2e12cb726e79f9006b))

## [0.2.1](https://github.com/syrax90/claude_mobile/compare/server-v0.2.0...server-v0.2.1) (2026-06-29)


### Documentation

* **privacy:** disclose OpenAI Codex as a third-party data recipient ([680957b](https://github.com/syrax90/claude_mobile/commit/680957b7a922150b7707cac3718faef9f2ca1bf0))
* **privacy:** disclose OpenAI Codex data handling in privacy policy ([c9ded5b](https://github.com/syrax90/claude_mobile/commit/c9ded5bb433aa148bba64d8369671fd7f2425a8f))
* **privacy:** drop "optional" framing for Codex ([144fac2](https://github.com/syrax90/claude_mobile/commit/144fac2f79cd0d92427b4bc00cccc841be64e7bc))

## [0.2.0](https://github.com/syrax90/claude_mobile/compare/server-v0.1.18...server-v0.2.0) (2026-06-26)


### ⚠ BREAKING CHANGES

* **sessions:** wire protocol bumped 5→6. The claude_options envelope field changed shape: dropped max_effort (bool); added model (str), effort (str|null), fast_mode (bool), ultracode (bool), append_system_prompt (str|null). Any relay or client built against proto 5 will fail the version handshake with a proto 6 agent. New sessions default to effort="high" instead of always "max".

### Features

* **sessions:** replace Claude max-effort toggle with model + effort controls (CLA-84) ([3b52a46](https://github.com/syrax90/claude_mobile/commit/3b52a46439d066fc15c95fbd8084d84b79bf195e))


### Bug Fixes

* **android:** Replace the boolean Max effort toggle with rich Claude session controls (CLA-84) ([3b052b1](https://github.com/syrax90/claude_mobile/commit/3b052b1cda8438c98b9375128ac378612c97f46c))

## [0.1.18](https://github.com/syrax90/claude_mobile/compare/server-v0.1.17...server-v0.1.18) (2026-06-24)


### Documentation

* **privacy:** disclose in-app Help & Support data flows (CLA-67) ([c22abac](https://github.com/syrax90/claude_mobile/commit/c22abac01e9e09e4fd123be18d62c79f1118a660))
* **privacy:** disclose in-app Help & Support data flows (CLA-67) ([6fa0e16](https://github.com/syrax90/claude_mobile/commit/6fa0e164d2c7027a8104914d5080559a2fb8e24e))

## [0.1.17](https://github.com/syrax90/claude_mobile/compare/server-v0.1.16...server-v0.1.17) (2026-06-23)


### Features

* **agent:** generalize CLI launch abstraction and add Codex CLI support (CLA-68) ([de719f2](https://github.com/syrax90/claude_mobile/commit/de719f27d013f277ede0bac900ca4ff8bd4fb59d))
* **agent:** generalize CLI launch abstraction and add Codex CLI support (CLA-68) ([f9c0d83](https://github.com/syrax90/claude_mobile/commit/f9c0d8375d5a5106cb78561881b9cb93b8c584b3))

## [0.1.16](https://github.com/syrax90/claude_mobile/compare/server-v0.1.15...server-v0.1.16) (2026-06-19)


### Bug Fixes

* **agent:** add `re-pair --fresh` to recover from missing-agent 404 ([8db9e00](https://github.com/syrax90/claude_mobile/commit/8db9e0058de2c46f7fb98dbd34caf35a011e08e4))
* **pairing:** add `re-pair --fresh` to recover from missing-agent 404 ([a6b261a](https://github.com/syrax90/claude_mobile/commit/a6b261a6fe6b02be6cc173c88222333a2fe1095c))

## [0.1.15](https://github.com/syrax90/claude_mobile/compare/server-v0.1.14...server-v0.1.15) (2026-06-13)


### Features

* **dashboard:** show email verification status on users page ([b56c971](https://github.com/syrax90/claude_mobile/commit/b56c9717bab4f701aa0ca0f838c9b9ab9d657768))
* **server:** show email verification status on users page ([22a0bb5](https://github.com/syrax90/claude_mobile/commit/22a0bb5b61848bd196be8005839622cccbf30025))


### Performance Improvements

* **central-server:** capacity hardening — WAL tuning, batched heartbeats, bounded argon2, backoff de-escalation, container limits (CLA-65) ([817f3c0](https://github.com/syrax90/claude_mobile/commit/817f3c06596fba2f8bb7ab98c8414db612c13c58))
* **central-server:** SQLite WAL tuning, batched heartbeats, bounded argon2, reconnect backoff de-escalation, container limits (CLA-65) ([814eb2c](https://github.com/syrax90/claude_mobile/commit/814eb2c19f6b84f3529d035f4897dc20d234e0e2))

## [0.1.14](https://github.com/syrax90/claude_mobile/compare/server-v0.1.13...server-v0.1.14) (2026-06-09)


### Features

* **agent:** pop-up notifications when a Claude session awaits input or ends(CLA-64) ([6410cdb](https://github.com/syrax90/claude_mobile/commit/6410cdb50d471709d1aa560a62b8040e516d1d40))
* pop-up notifications when a Claude session awaits input or ends (CLA-64) ([848af44](https://github.com/syrax90/claude_mobile/commit/848af446a8cb6db7c699c34bbfa0a8093ede7271))

## [0.1.13](https://github.com/syrax90/claude_mobile/compare/server-v0.1.12...server-v0.1.13) (2026-06-09)


### Bug Fixes

* **pairing:** release stale device lock and forbid cross-user agent takeover ([01b940c](https://github.com/syrax90/claude_mobile/commit/01b940cf0c9218eedfb63ab3706e72a68cb5672e))
* **pairing:** release stale device lock and forbid cross-user agent takeover ([40392b4](https://github.com/syrax90/claude_mobile/commit/40392b41d02ff780b512aa9de73ef1bbb0b0e6b8))

## [0.1.12](https://github.com/syrax90/claude_mobile/compare/server-v0.1.11...server-v0.1.12) (2026-05-29)


### Bug Fixes

* **android:** remove dead foreground service and its artifacts ([1475656](https://github.com/syrax90/claude_mobile/commit/1475656689a68bdc942fd8f2a3feabd3fd4f2052))
* **android:** remove dead foreground service and its artifacts ([d6d98b3](https://github.com/syrax90/claude_mobile/commit/d6d98b3677f74edfc6ad3fb27216c1e8aecedf85))

## [0.1.11](https://github.com/syrax90/claude_mobile/compare/server-v0.1.10...server-v0.1.11) (2026-05-23)


### Features

* **agent:** self-heal protocol-version mismatch via HTTPS-bootstrap update ([174cd93](https://github.com/syrax90/claude_mobile/commit/174cd936307c53228d8b457e66d910426e9893d7))
* **agent:** self-heal protocol-version mismatch via HTTPS-bootstrap update ([2243d31](https://github.com/syrax90/claude_mobile/commit/2243d31040091f57908092556fb03e36883c5a8d))

## [0.1.10](https://github.com/syrax90/claude_mobile/compare/server-v0.1.9...server-v0.1.10) (2026-05-23)


### Features

* **ui:** rename "Your devices" to "My devices" and add Google logo to sign-in buttons ([657fd92](https://github.com/syrax90/claude_mobile/commit/657fd924ddf2d6e5bc8d39a3b70775922d98a086))


### Bug Fixes

* **android:** rename "Your devices" to "My devices" and add Google logo to sign-in buttons ([bbc4eab](https://github.com/syrax90/claude_mobile/commit/bbc4eab6c4617c47b35e5729b093eab55a200905))
* **android:** replace 256 KB single-frame replay with 4 MB chunked ring buffer (proto v3) ([b1740e9](https://github.com/syrax90/claude_mobile/commit/b1740e92bbe3fcc77edcf620490ae9000985217b))
* **privacy:** replace narrow 420 px card with article + sticky TOC layout ([9728dc6](https://github.com/syrax90/claude_mobile/commit/9728dc6c61d59a65f88c94e7ddceea434b90d7cd))
* **privacy:** replace narrow 420 px card with article + sticky TOC layout ([a518040](https://github.com/syrax90/claude_mobile/commit/a5180402e6773789903639eeaf98c0ae57a85bf5))
* **replay:** replace 256 KB single-frame replay with 4 MB chunked ring buffer (proto v3) ([bd8bb63](https://github.com/syrax90/claude_mobile/commit/bd8bb633f83e7a6552b0db15f00291b71ec53504))

## [0.1.9](https://github.com/syrax90/claude_mobile/compare/server-v0.1.8...server-v0.1.9) (2026-05-22)


### Features

* self-host privacy policy at /privacy.html and surface links in Android UI ([9eb0b90](https://github.com/syrax90/claude_mobile/commit/9eb0b90c184b13617810b52d08faa9c25b6834dd))
* self-host privacy policy at /privacy.html and surface links in Android UI ([882251b](https://github.com/syrax90/claude_mobile/commit/882251b75e5f32777dfa356ae8906f7f48ed18ca))

## [0.1.8](https://github.com/syrax90/claude_mobile/compare/server-v0.1.7...server-v0.1.8) (2026-05-22)


### Features

* **account-deletion:** user self-service deletion + portal + 14-day grace (CLA-61) ([8fb14dd](https://github.com/syrax90/claude_mobile/commit/8fb14ddb3d18c83dd2c9cae4451ddabeda4bcee1))
* **android:** user self-service deletion + portal + 14-day grace (CLA-61) ([c8ea9cb](https://github.com/syrax90/claude_mobile/commit/c8ea9cb97c4dcf86dfc3967be84611230dfd4c90))
* **devices:** per-install device UUID + user-scoped devices API + "Your devices" screen (CLA-62) ([354fb68](https://github.com/syrax90/claude_mobile/commit/354fb6890ed16de899a1952db81f6fce8d630891))
* **devices:** per-install device UUID + user-scoped devices API + "Your devices" screen (CLA-62) ([60bc57a](https://github.com/syrax90/claude_mobile/commit/60bc57a38aa91f5be0aa14143c70a300205ec93e))
* **portal:** add confirm-password field to signup and reset flows ([e34495a](https://github.com/syrax90/claude_mobile/commit/e34495aceca8b52e53008d2a822ccc5d400eb434))
* **portal:** add confirm-password field to signup and reset flows ([a644626](https://github.com/syrax90/claude_mobile/commit/a644626913bdcc5c17bac272f2b60432b8a531f9))


### Bug Fixes

* **account-deletion:** resolve 5 reported issues end-to-end ([0775b9d](https://github.com/syrax90/claude_mobile/commit/0775b9d544ea1165d1d4c0c3523c841faf87ba7e))
* **android:** resolve 5 reported issues end-to-end ([1a43c9b](https://github.com/syrax90/claude_mobile/commit/1a43c9becff506141f30eef1fac930a512b0bba7))
* **portal:** keep password eye button on the same row as the input ([5a7f764](https://github.com/syrax90/claude_mobile/commit/5a7f76449873367fd067a62334ca2cfca3d90ca7))
* **portal:** keep password eye button on the same row as the input ([26a5b4a](https://github.com/syrax90/claude_mobile/commit/26a5b4a7b4a538805749d2800cff8ba11f1f3aa1))
* **portal:** restore delete-account Google button and replace password show/hide text with SVG icons ([265f361](https://github.com/syrax90/claude_mobile/commit/265f36117a2da90e8d962b29e886354cf012513b))
* **portal:** restore delete-account Google button and replace password show/hide text with SVG icons ([1b58968](https://github.com/syrax90/claude_mobile/commit/1b58968bb2a601599bcfc2e583d2ffc04213bc5c))
* **portal:** use Google renderButton instead of One Tap for sign-in ([a51a0dc](https://github.com/syrax90/claude_mobile/commit/a51a0dcafa2d3fd55516d166e0ec343f345c0032))
* **portal:** use Google renderButton instead of One Tap for sign-in ([14ba0bc](https://github.com/syrax90/claude_mobile/commit/14ba0bc8ea148f34e4ddecce70fb572996eb0cd4))

## [0.1.7](https://github.com/syrax90/claude_mobile/compare/server-v0.1.6...server-v0.1.7) (2026-05-18)


### Features

* **auth:** add password reset for email-registered users (CLA-55) ([04b5075](https://github.com/syrax90/claude_mobile/commit/04b507576171a178597d0cfc9a263d4a05fee65b))
* **auth:** add password reset for email-registered users (CLA-55) ([b99341c](https://github.com/syrax90/claude_mobile/commit/b99341c8b91220d756a032e434eae2b3bd5a0211))
* **auth:** log out mobile user after admin unregisters them ([8d2ce7b](https://github.com/syrax90/claude_mobile/commit/8d2ce7bbc371b0344c3dc2aee38e58db55854b3f))
* **auth:** log out mobile user after admin unregisters them ([27ae8fa](https://github.com/syrax90/claude_mobile/commit/27ae8fab28c1fab4d402926a92cf3396e14f43f9))
* **central-server:** users-centric admin dashboard refactor (CLA-56) ([9df563b](https://github.com/syrax90/claude_mobile/commit/9df563bd33cfe717b0548c3ae8a6ad7d7b1ca0e2))
* **central-server:** users-centric admin dashboard refactor (CLA-56) ([8b06345](https://github.com/syrax90/claude_mobile/commit/8b063456841226c5cfc6938de355857499a64df7))
* persist and surface agent version in admin dashboard (CLA) ([e7dcbb6](https://github.com/syrax90/claude_mobile/commit/e7dcbb63447429146c82664da19bd21bef808e23))
* persist and surface agent version in admin dashboard (CLA) ([12f909f](https://github.com/syrax90/claude_mobile/commit/12f909facf133cec77cad693f5f87bebf3bbe7f8))


### Bug Fixes

* **central-server:** close unregister-user modal via OOB swap ([7c0172c](https://github.com/syrax90/claude_mobile/commit/7c0172cf0dcb075ee2122f54559786c7209df9d8))
* **central-server:** close unregister-user modal via OOB swap ([e136fc8](https://github.com/syrax90/claude_mobile/commit/e136fc8b293b3a70aff40afd80b112f113db883a))

## [0.1.6](https://github.com/syrax90/claude_mobile/compare/server-v0.1.5...server-v0.1.6) (2026-05-15)


### Bug Fixes

* **agent:** deliver manifest body inline over relay (proto-v2) ([a7ebd08](https://github.com/syrax90/claude_mobile/commit/a7ebd08837774992fb60f28fbe89891ae1408d26))

## [0.1.5](https://github.com/syrax90/claude_mobile/compare/server-v0.1.4...server-v0.1.5) (2026-05-14)


### Bug Fixes

* **agent:** gate mobile "Paired" on agent.online=true; harden silent failure paths ([a863117](https://github.com/syrax90/claude_mobile/commit/a86311778e090c15da3bdbfe3c17bcb8f5c6a634))
* **pairing:** gate mobile "Paired" on agent.online=true; harden silent failure paths ([b45d31a](https://github.com/syrax90/claude_mobile/commit/b45d31aff702acca8fd3d06701af08911930604b))

## [0.1.4](https://github.com/syrax90/claude_mobile/compare/server-v0.1.3...server-v0.1.4) (2026-05-14)


### Bug Fixes

* **agent:** restore agent log observability and close QR-before-WS race ([2d94149](https://github.com/syrax90/claude_mobile/commit/2d9414997294e747d1e30c232830d97d09513108))
* **pairing:** harden agent relay_url comparator (scheme family, default-port normalisation, userinfo/query/fragment rejection) ([4b34cd5](https://github.com/syrax90/claude_mobile/commit/4b34cd5c2f8ec5f1506b314d2fbfab61485a1ca6))
* **pairing:** persist relay_url server-side so dispatch and recovery agree ([4b34cd5](https://github.com/syrax90/claude_mobile/commit/4b34cd5c2f8ec5f1506b314d2fbfab61485a1ca6))
* **pairing:** restore agent log observability and close QR-before-WS race ([4b34cd5](https://github.com/syrax90/claude_mobile/commit/4b34cd5c2f8ec5f1506b314d2fbfab61485a1ca6))

## [0.1.3](https://github.com/syrax90/claude_mobile/compare/server-v0.1.2...server-v0.1.3) (2026-05-13)


### Bug Fixes

* **database:** clear claimed_agent_id FK before deleting agent ([ac416fd](https://github.com/syrax90/claude_mobile/commit/ac416fd8d93b06bc581f65a773db706f3d61f04b))
* **database:** clear claimed_agent_id FK before deleting agent ([9f909ee](https://github.com/syrax90/claude_mobile/commit/9f909ee835bf87782de9c364fdc0f6e6671cd9b9))

## [0.1.2](https://github.com/syrax90/claude_mobile/compare/server-v0.1.1...server-v0.1.2) (2026-05-08)


### Bug Fixes

* **pairing:** resolve offline-after-pairing via agent-derived WS URL and sys.exit restart ([cae3ae2](https://github.com/syrax90/claude_mobile/commit/cae3ae2ad559cb8cbe09b7f2da0305f6fd28e2ff))
* **pairing:** resolve offline-after-pairing via agent-derived WS URL and sys.exit restart ([3b88eb2](https://github.com/syrax90/claude_mobile/commit/3b88eb2464ace3a780c1f195d9b19601ba8fcf6a))

## [0.1.1](https://github.com/syrax90/claude_mobile/compare/server-v0.1.0...server-v0.1.1) (2026-05-08)


### Features

* add non-expiring token support by omitting exp claim when TTL is 0 ([ada6181](https://github.com/syrax90/claude_mobile/commit/ada6181c3f3e8faea842312320e0834fa2ed953d))
* add non-expiring token support by omitting exp claim when TTL is 0 ([649fc89](https://github.com/syrax90/claude_mobile/commit/649fc8903a88bc0069483604936906cb35cd5b07))
* add real-time agent online/offline status via user-scoped WebSocket channel ([3fcd576](https://github.com/syrax90/claude_mobile/commit/3fcd57643b7082d243583161b11157e9bf3c7524))
* add real-time agent online/offline status via user-scoped WebSocket channel ([27b3431](https://github.com/syrax90/claude_mobile/commit/27b34319d403bce8cccee5059f092e5d11035bc4))
* **cla-30:** central server WebSocket relay with agent registration and web dashboard ([6d2762b](https://github.com/syrax90/claude_mobile/commit/6d2762b1d548ac918adac458abff387c43dcd4e4))
* **cla-32:** Android QR pairing and dual-mode connection relay ([066cd00](https://github.com/syrax90/claude_mobile/commit/066cd00b6cae954529e98fc562c1b6ff3b660e78))
* **cla-32:** Android QR pairing and dual-mode connection relay ([a93d4d7](https://github.com/syrax90/claude_mobile/commit/a93d4d7d2456030e811a735730f9f6e420615b75))
* **cla-33:** implement user authentication system with registration, login, and Google OAuth ([d5cbed1](https://github.com/syrax90/claude_mobile/commit/d5cbed193a7f4770cebc876f16d2a2a3ab2ec1d0))
* **cla-33:** implement user authentication system with registration, login, and Google OAuth ([1865ba5](https://github.com/syrax90/claude_mobile/commit/1865ba5d5c375f17a8d0addc5060fbf537635634))
* **cla-34:** implement user-owned agent management with credential isolation ([77135f5](https://github.com/syrax90/claude_mobile/commit/77135f59c652f95a1dcc6c8691db6cc3a4b47a25))
* **cla-34:** implement user-owned agent management with credential isolation ([e26746e](https://github.com/syrax90/claude_mobile/commit/e26746e60cd4ec751fe89ab0d379204020c9d70a))
* **CLA-38:** implement email verification for email/password registr… ([cb4e71b](https://github.com/syrax90/claude_mobile/commit/cb4e71bb0f4eddfdbb6ebc253fe7d148e26f9839))
* **CLA-38:** implement email verification for email/password registration via server OTP ([fa8c2d1](https://github.com/syrax90/claude_mobile/commit/fa8c2d100d02fd174fe5e790e7ce8905c225b538))
* **cla-42:** wire-contract versioning, auto-update, Play publishing, and CI/CD ([204350d](https://github.com/syrax90/claude_mobile/commit/204350dfb2a017a8fbe6b3169a5f225e5f408c76))
* **cla-44:** wire StagedBinaryUpdater, force-update endpoint, launcher rollback, and Android update UX ([4d27647](https://github.com/syrax90/claude_mobile/commit/4d27647f9d93c36ad77c0110094434c450bdc3bf))
* **cla-44:** wire StagedBinaryUpdater, force-update endpoint, launcher rollback, and Android update UX ([e5d4da1](https://github.com/syrax90/claude_mobile/commit/e5d4da1851e9e45f26472515dc166541f505a9ba))
* **CLA-50:** agent-initiated QR pairing across server, agent, and Android ([8d1f9b9](https://github.com/syrax90/claude_mobile/commit/8d1f9b956791d2944c4732af165327e7cd2f78ca))
* enforce admin-users-never-own-agents invariant and fix viewer security gaps ([6280127](https://github.com/syrax90/claude_mobile/commit/62801275d22805a871fa8e349206d27b9372cd71))
* enforce admin-users-never-own-agents invariant and fix viewer security gaps ([4367e5d](https://github.com/syrax90/claude_mobile/commit/4367e5dc2d1705bf9b5eae2887060b49023c945c))
* **mobaide_agent:** agent-initiated QR pairing across server, agent, and Android ([5fa3179](https://github.com/syrax90/claude_mobile/commit/5fa3179286bfc51948c1727cbc3412f4ef042a89))


### Bug Fixes

* **cla-30:** resolve validation warnings — secure cookie, unused var, re-registration race ([37a7045](https://github.com/syrax90/claude_mobile/commit/37a704520cec95026f9e397cc841e5e65725df44))
* fail-closed trust key pinning and fetch-trust-key CLI verb ([2dd5613](https://github.com/syrax90/claude_mobile/commit/2dd561377071f0c6c66f55eb473db58cb716e677))
* fail-closed trust key pinning and fetch-trust-key CLI verb ([4a8f281](https://github.com/syrax90/claude_mobile/commit/4a8f281b397807112dde6ab19b43fa6686d299ed))
* issue dashboard login cookie as session cookie when TTL=0 ([fc619a5](https://github.com/syrax90/claude_mobile/commit/fc619a5fea74c4c2d85ed1d15def6e332e340e26))
* issue dashboard login cookie as session cookie when TTL=0 ([203bcd7](https://github.com/syrax90/claude_mobile/commit/203bcd7f70738456d584ddabadd83ce96a29f7e5))
* recover from server-side JWT invalidation on mobile client ([32b4773](https://github.com/syrax90/claude_mobile/commit/32b4773425ea8ad230094c85f3683f52e71f4918))
* recover from server-side JWT invalidation on mobile client ([389c950](https://github.com/syrax90/claude_mobile/commit/389c95033d59378ff99cec36193a47803480fd3a))
* **relay:** fix session-not-showing and blank-terminal-on-reopen bugs ([e8894ab](https://github.com/syrax90/claude_mobile/commit/e8894ab364cdcb87fe74411bce0e9e4b91c30862))
* **relay:** unstick keyboard on session reopen via explicit mobile_session_attach ([b3c829e](https://github.com/syrax90/claude_mobile/commit/b3c829ee3deb538a7e476e9d101aeee406685b03))
* **relay:** unstick keyboard on session reopen via explicit mobile_session_attach ([45e5731](https://github.com/syrax90/claude_mobile/commit/45e5731766124129afc004075c8c1af140d4eb1f))


### Documentation

* new docs/email-verification-workflow.md; updated deploy/prod/bootstrap-mobaide.md with SMTP guidance. ([fa8c2d1](https://github.com/syrax90/claude_mobile/commit/fa8c2d100d02fd174fe5e790e7ce8905c225b538))

## [Unreleased]

### Breaking
- **`POST /api/pairing/initiate` no longer returns a `ws_url` field.** The
  pairing WS URL is now derived agent-side from `RELAY_SERVER_URL` + the
  response `nonce` (single source of truth). Removes a class of
  deployment-skew bugs where a stale server image returned an `https://`
  scheme that the Python `websockets` client hard-rejected with
  `"isn't a valid URI: scheme isn't ws or wss"`. The
  `derive_ws_url` helper in `central_server.pairing.qr_signer` is removed.

### Added
- **QR pairing endpoints (CLA-50):**
  * ``POST /api/pairing/initiate`` — anonymous; agent posts to receive a
    signed nonce + QR payload (``MOBA1|<nonce>|<ws_url>|<fp>``). Returns
    503 when ``MANIFEST_SIGNING_PRIVATE_KEY`` is unset.
  * ``POST /api/pairing/claim`` — authenticated (mobile user); claims a
    scanned nonce, creates the agent row, and pushes signed credentials
    (``agent_id``/``agent_token``) directly to the waiting agent over
    ``WS /ws/pairing/{nonce}``.
  * ``WS /ws/pairing/{nonce}`` — anonymous, single-flight WebSocket the
    agent holds open while the QR is displayed; closed (with the push
    payload) the instant mobile claims the nonce, or with code 4410
    (gone) if the nonce expires before a claim.
- Schema migration 0005 (``pairing_qr``): adds ``pairing_sessions`` table
  (``nonce``, ``agent_id``, ``owner_user_id``, ``expires_at``,
  ``claimed_at``) used by the QR flow. Pure additive; runs automatically
  on startup.
- ``GET /api/v1/agent/trust_key`` — public, anonymous endpoint that
  exposes ``MANIFEST_SIGNING_PUBLIC_KEY`` so the agent install scripts
  and ``mobaide-agent fetch-trust-key`` can bootstrap
  ``<prefix>/trust/central_server.pub`` without operator-side env-var
  plumbing on each install host. Returns 503 when the env var is
  unset (matches the existing manifest-publish 503 pattern).
- Prompt-templates CRUD: ``GET/POST /api/templates``,
  ``GET/PUT/DELETE /api/templates/{id}``, and
  ``POST /api/templates/{id}/touch`` (updates ``last_used_at`` and bumps the
  template to the top of the recents list).
- ``prompt_templates`` SQLite table (schema v6 migration — pure additive; runs
  automatically on startup alongside the existing v1-v5 migrations). Indexed
  on ``owner_user_id`` for per-user scoped queries.
- Three new push events broadcast over the existing ``/ws/user-events``
  channel: ``template.created``, ``template.updated``, and
  ``template.deleted`` — so all connected clients for a user stay in sync
  without polling.
- ``__version__`` exposed via ``importlib.metadata.version("central-server")``
  and used as the canonical source of truth for ``FastAPI(version=...)`` and
  the relay handshake ``server_version`` field.
- ``[project.scripts]`` entry ``claude-central``.
- ``GET /api/version`` endpoint returning name, server_version,
  protocol_versions, min_client_version, capabilities, and
  latest_agent_version. The manifest body/signature is NOT returned here;
  agents receive it inline over the relay tunnel (proto-v2).
- Manifest store and HTTP routes:
  * ``GET /api/v1/agent/manifest.json`` — public read (404 when no manifest
    has been published yet — install scripts treat 404 as a first-install
    bootstrap).
  * ``POST /api/internal/agent_manifest`` — token-auth via ``X-Release-Token``;
    validates an Ed25519 signature against the configured public key before
    persisting.
- Agent-tunnel handshake validates ``protocol_version`` against
  ``SUPPORTED_PROTOCOL_VERSIONS`` and closes with code ``4002`` when the
  agent is outside the supported range. ``auth_ok`` now carries the signed
  manifest body inline (``manifest_body`` + ``manifest_signature``) — proto-v2
  replaces the earlier URL-pointer design.
- Forward-compat ``unknown_control_type`` counter + WARN logging on
  ``_handle_control_json`` fall-through.
- ``cryptography>=43.0`` runtime dependency for Ed25519 verification.

### Configuration

New environment variables:

* ``RELEASE_AUTH_TOKEN`` — bearer token gating ``POST /api/internal/agent_manifest``.
* ``MANIFEST_SIGNING_PUBLIC_KEY`` — base64-encoded Ed25519 public key used to
  validate manifests posted by the release pipeline.
* ``MANIFEST_STORE_PATH`` — file system path for the persisted manifest
  (default ``data/agent_manifest.json``).
* ``MANIFEST_SIGNING_PRIVATE_KEY`` — base64-encoded Ed25519 private key
  paired with ``MANIFEST_SIGNING_PUBLIC_KEY``. Required for the QR pairing
  flow: the server signs the push payload delivered to the agent over
  ``WS /ws/pairing/{nonce}``. ``POST /api/pairing/initiate`` returns 503
  when unset.
* ``PUBLIC_BASE_URL`` — public HTTPS base URL of this server (e.g.
  ``https://mobaide.com``). Embedded in the QR payload so the agent can
  open the right WebSocket. Falls back to the ``Host`` header of the
  initiate request when unset.
* ``PAIRING_QR_TOKEN_TTL_SECONDS`` — lifetime of a pairing nonce in
  seconds (default ``120``).
