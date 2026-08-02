import { useToastStore } from '../stores/toastStore';

export function Home() {
  const show = useToastStore((state) => state.show);

  return (
    <section className="home">
      <h1>Mobaide Web</h1>
      <p>
        Browser client for controlling Claude Code and other CLI agents through
        the Mobaide relay. Feature screens arrive in upcoming milestones; this
        shell carries the routing, theming, data, and API foundations they
        build on.
      </p>
      <button
        type="button"
        onClick={() => {
          show('Toasts are wired up.', 'success');
        }}
      >
        Show a toast
      </button>
    </section>
  );
}
