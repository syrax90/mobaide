import { useEffect, useRef, useState } from 'react';

/**
 * Countdown backing the "Resend code (30s)" buttons.
 *
 * Seeded from the server's `cooldown_seconds_remaining` / `Retry-After` rather
 * than assumed, because the limiters are in-memory per process and a restart
 * can return a shorter window than the nominal 60s.
 */
export function useCooldown(): [number, (seconds: number) => void] {
  const [remaining, setRemaining] = useState(0);
  const deadlineRef = useRef(0);

  useEffect(() => {
    if (remaining <= 0) return;
    // Tick off a wall-clock deadline instead of decrementing: a backgrounded
    // tab throttles timers, and a naive counter would drift long.
    const id = setInterval(() => {
      const left = Math.ceil((deadlineRef.current - Date.now()) / 1000);
      setRemaining(left > 0 ? left : 0);
    }, 500);
    return () => {
      clearInterval(id);
    };
  }, [remaining]);

  const start = (seconds: number) => {
    deadlineRef.current = Date.now() + seconds * 1000;
    setRemaining(seconds);
  };

  return [remaining, start];
}
