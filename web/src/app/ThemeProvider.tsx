import { useEffect, type ReactNode } from 'react';
import { useResolvedTheme } from './useResolvedTheme';

export function ThemeProvider({ children }: { children: ReactNode }) {
  const theme = useResolvedTheme();

  useEffect(() => {
    document.documentElement.dataset.theme = theme;
  }, [theme]);

  return children;
}
