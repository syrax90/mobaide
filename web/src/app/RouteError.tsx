import { isRouteErrorResponse, useRouteError } from 'react-router';

export function RouteError() {
  const error = useRouteError();
  const notFound = isRouteErrorResponse(error) && error.status === 404;

  return (
    <main className="error-screen">
      <h1>{notFound ? 'Page not found' : 'Something went wrong'}</h1>
      <p>
        {notFound
          ? 'The address you followed does not exist.'
          : 'An unexpected error occurred. Reload the page to continue.'}
      </p>
      <a href="/">Back to home</a>
    </main>
  );
}
