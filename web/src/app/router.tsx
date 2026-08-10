import { createBrowserRouter } from 'react-router';
import { Account } from '../routes/account/Account';
import { Devices } from '../routes/account/Devices';
import { ForgotPassword } from '../routes/auth/ForgotPassword';
import { Login } from '../routes/auth/Login';
import { Register } from '../routes/auth/Register';
import { ResetPassword } from '../routes/auth/ResetPassword';
import { VerifyEmail } from '../routes/auth/VerifyEmail';
import { Agents } from '../routes/agents/Agents';
import { AutoScanDetailScreen } from '../routes/autoscan/AutoScanDetailScreen';
import { Pair } from '../routes/pair/Pair';
import { PairHost } from '../routes/pair/PairHost';
import { SettingsScreen } from '../routes/settings/SettingsScreen';
import { ProjectsScreen } from '../routes/projects/ProjectsScreen';
import { GitScreen } from '../routes/projects/git/GitScreen';
import { TemplatesScreen } from '../routes/templates/TemplatesScreen';
import { SessionsScreen } from '../routes/sessions/SessionsScreen';
import { NewSession } from '../routes/sessions/create/NewSession';
import { AuthLayout } from './AuthLayout';
import { RootLayout } from './RootLayout';
import { RouteError } from './RouteError';
import { RequireAnonymous, RequireAuth, requireAuthMiddleware } from './guards';

export const router = createBrowserRouter([
  {
    path: '/',
    errorElement: <RouteError />,
    children: [
      {
        // Auth screens get their own chrome — the signed-in nav would link a
        // signed-out visitor to screens the guard immediately bounces them off.
        element: <RequireAnonymous />,
        children: [
          {
            element: <AuthLayout />,
            children: [
              { path: 'login', element: <Login /> },
              { path: 'register', element: <Register /> },
              { path: 'verify-email', element: <VerifyEmail /> },
              { path: 'forgot-password', element: <ForgotPassword /> },
              { path: 'reset-password', element: <ResetPassword /> },
            ],
          },
        ],
      },
      {
        // Neither guarded nor anonymous-only. A pairing link must reach this
        // component even when signed out, because the payload rides in the URL
        // fragment and `requireAuthMiddleware` would redirect — carrying only
        // `pathname + search` — before anything could read it. A signed-in user
        // must reach it too, which rules out `RequireAnonymous`. The route
        // handles the sign-in bounce itself, after stashing and scrubbing.
        element: <AuthLayout />,
        children: [{ path: 'pair', element: <Pair /> }],
      },
      {
        middleware: [requireAuthMiddleware],
        element: <RequireAuth />,
        children: [
          {
            element: <RootLayout />,
            children: [
              { index: true, element: <SessionsScreen /> },
              { path: 'account', element: <Account /> },
              { path: 'devices', element: <Devices /> },
              { path: 'settings', element: <SettingsScreen /> },
              { path: 'agents', element: <Agents /> },
              // Guarded, unlike `/pair`: this direction has no payload in the
              // URL to rescue before sign-in, and minting a code needs a
              // session anyway.
              { path: 'agents/pair', element: <PairHost /> },
              { path: 'projects', element: <ProjectsScreen /> },
              { path: 'templates', element: <TemplatesScreen /> },
              // Agent- and project-scoped, so both ids ride in the path: a git
              // list is only meaningful for one repo on one host, and the
              // selected-agent store would make a shared link resolve elsewhere.
              {
                path: 'agents/:agentId/projects/:projectName/git',
                element: <GitScreen />,
              },
              // Not `sessions/new`: keeping it off the terminal route's path
              // means correctness never rests on react-router ranking a static
              // segment above `:sessionId`.
              { path: 'agents/:agentId/new-session', element: <NewSession /> },
              // Agent- and project-scoped for the same reason the git route is:
              // a scan is one project on one host, and the selected-agent store
              // would make a shared link resolve elsewhere.
              {
                path: 'agents/:agentId/autoscan/:projectName',
                element: <AutoScanDetailScreen />,
              },
              {
                // The only lazy route. xterm.js and its WebGL addon are ~740 KB
                // raw, which is more than the rest of the SPA put together —
                // eager-loading them would put the whole emulator on the
                // critical path of the sign-in screen.
                path: 'agents/:agentId/sessions/:sessionId',
                lazy: async () => {
                  const { TerminalScreen } = await import(
                    '../routes/terminal/TerminalScreen'
                  );
                  return { Component: TerminalScreen };
                },
              },
              // The reserved-key sweep harness. Both the route and the module
              // disappear from a production build: `import.meta.env.DEV` is a
              // compile-time constant, so Rollup drops the branch and the
              // dynamic import with it.
              ...(import.meta.env.DEV
                ? [
                    {
                      path: 'dev/key-sweep',
                      lazy: async () => {
                        const { KeySweepScreen } = await import(
                          '../routes/dev/KeySweepScreen'
                        );
                        return { Component: KeySweepScreen };
                      },
                    },
                  ]
                : []),
            ],
          },
        ],
      },
    ],
  },
]);
