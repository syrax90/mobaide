import { createBrowserRouter } from 'react-router';
import { Home } from '../routes/Home';
import { Account } from '../routes/account/Account';
import { Devices } from '../routes/account/Devices';
import { ForgotPassword } from '../routes/auth/ForgotPassword';
import { Login } from '../routes/auth/Login';
import { Register } from '../routes/auth/Register';
import { ResetPassword } from '../routes/auth/ResetPassword';
import { VerifyEmail } from '../routes/auth/VerifyEmail';
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
        middleware: [requireAuthMiddleware],
        element: <RequireAuth />,
        children: [
          {
            element: <RootLayout />,
            children: [
              { index: true, element: <Home /> },
              { path: 'account', element: <Account /> },
              { path: 'devices', element: <Devices /> },
            ],
          },
        ],
      },
    ],
  },
]);
