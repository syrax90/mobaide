# Changelog

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
