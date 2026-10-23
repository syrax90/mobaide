import type { ReactNode } from 'react';

/**
 * The mic's glyphs, drawn on the same 16-unit pixel grid as the rest of the
 * chrome and matching the Android `VoiceMicButton` set (Mic, MicNone,
 * Downloading, ErrorOutline). `aria-hidden`: the button's `aria-label`
 * already names the state.
 */
function PixelIcon({ children }: { children: ReactNode }) {
  return (
    <svg className="voice-icon" viewBox="0 0 16 16" aria-hidden="true" focusable="false">
      {children}
    </svg>
  );
}

function MicStand() {
  return (
    <>
      <rect x="3" y="6" width="1" height="3" />
      <rect x="12" y="6" width="1" height="3" />
      <rect x="4" y="9" width="1" height="1" />
      <rect x="11" y="9" width="1" height="1" />
      <rect x="5" y="10" width="6" height="1" />
      <rect x="7" y="11" width="2" height="3" />
      <rect x="5" y="14" width="6" height="1" />
    </>
  );
}

export function MicIcon() {
  return (
    <PixelIcon>
      <rect x="7" y="1" width="2" height="1" />
      <rect x="6" y="2" width="4" height="7" />
      <rect x="7" y="9" width="2" height="1" />
      <MicStand />
    </PixelIcon>
  );
}

/** The hollow mic: voice input is off. */
export function MicOffIcon() {
  return (
    <PixelIcon>
      <rect x="7" y="1" width="2" height="1" />
      <rect x="6" y="2" width="1" height="7" />
      <rect x="9" y="2" width="1" height="7" />
      <rect x="7" y="9" width="2" height="1" />
      <MicStand />
    </PixelIcon>
  );
}

export function DownloadIcon() {
  return (
    <PixelIcon>
      <rect x="7" y="1" width="2" height="6" />
      <rect x="4" y="7" width="8" height="1" />
      <rect x="5" y="8" width="6" height="1" />
      <rect x="6" y="9" width="4" height="1" />
      <rect x="7" y="10" width="2" height="1" />
      <rect x="2" y="12" width="1" height="2" />
      <rect x="13" y="12" width="1" height="2" />
      <rect x="2" y="14" width="12" height="1" />
    </PixelIcon>
  );
}

export function AlertIcon() {
  return (
    <PixelIcon>
      <rect x="7" y="2" width="2" height="8" />
      <rect x="7" y="12" width="2" height="2" />
    </PixelIcon>
  );
}
