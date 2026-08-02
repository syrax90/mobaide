import { useEffect, type ReactNode } from 'react';
import { resolveTheme, useThemeStore } from '../stores/themeStore';

export function ThemeProvider({ children }: { children: ReactNode }) {
  const preference = useThemeStore((state) => state.preference);

  useEffect(() => {
    const media = window.matchMedia('(prefers-color-scheme: dark)');
    const apply = () => {
      document.documentElement.dataset.theme = resolveTheme(preference, media.matches);
    };
    apply();
    if (preference !== 'system') {
      return;
    }
    media.addEventListener('change', apply);
    return () => {
      media.removeEventListener('change', apply);
    };
  }, [preference]);

  return children;
}
