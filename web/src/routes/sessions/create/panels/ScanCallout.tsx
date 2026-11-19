import { useId, type ReactNode } from 'react';

interface ScanCalloutProps {
  /** `info` explains how something works; `important` is a rule the user must know. */
  tone: 'info' | 'important';
  title: string;
  children: ReactNode;
}

/** A titled note in the Auto Scan wizard, ringed in the tone's colour. */
export function ScanCallout({ tone, title, children }: ScanCalloutProps) {
  const titleId = useId();
  return (
    <aside className={`autoscan-callout autoscan-callout-${tone}`} aria-labelledby={titleId}>
      <span className="autoscan-callout-mark" aria-hidden="true">
        {tone === 'info' ? 'i' : '!'}
      </span>
      <div className="autoscan-callout-body">
        <p className="autoscan-callout-title" id={titleId}>
          {title}
        </p>
        {children}
      </div>
    </aside>
  );
}
