import { createBrowserRouter } from 'react-router';
import { Home } from '../routes/Home';
import { RootLayout } from './RootLayout';
import { RouteError } from './RouteError';

export const router = createBrowserRouter([
  {
    path: '/',
    element: <RootLayout />,
    errorElement: <RouteError />,
    children: [{ index: true, element: <Home /> }],
  },
]);
