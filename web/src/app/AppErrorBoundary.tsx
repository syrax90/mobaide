import { Component, type ErrorInfo, type ReactNode } from 'react';

interface AppErrorBoundaryProps {
  children: ReactNode;
}

interface AppErrorBoundaryState {
  hasError: boolean;
}

/**
 * Last-resort boundary around the providers and router. Route-level render
 * errors are handled by the router's errorElement (RouteError); this catches
 * failures outside the route tree. Styled with raw CSS classes only so it
 * cannot cascade.
 */
export class AppErrorBoundary extends Component<
  AppErrorBoundaryProps,
  AppErrorBoundaryState
> {
  override state: AppErrorBoundaryState = { hasError: false };

  static getDerivedStateFromError(): AppErrorBoundaryState {
    return { hasError: true };
  }

  override componentDidCatch(error: Error, info: ErrorInfo): void {
    console.error('Uncaught render error', error, info);
  }

  override render(): ReactNode {
    if (this.state.hasError) {
      return (
        <main className="error-screen">
          <h1>Something went wrong</h1>
          <p>An unexpected error occurred. Reload the page to continue.</p>
        </main>
      );
    }
    return this.props.children;
  }
}
