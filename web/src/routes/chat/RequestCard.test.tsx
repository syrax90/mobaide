import { act, cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it } from 'vitest';
import type { ChatResult, ChatSessionModel } from '../../chat/chatModel';
import type { SignInRequest } from '../../chat/chatWire';
import { RequestDock } from './RequestCard';

type SignInCall =
  | { action: 'start'; method: string }
  | { action: 'submit'; value: string }
  | { action: 'cancel' };

/** The three sign-in commands; each is answered with `result`. */
class FakeSignInModel {
  readonly sent: SignInCall[] = [];
  result: ChatResult = { ok: true, data: {} };

  startSignIn(method: string): Promise<ChatResult> {
    return this.record({ action: 'start', method });
  }

  submitSignIn(value: string): Promise<ChatResult> {
    return this.record({ action: 'submit', value });
  }

  cancelSignIn(): Promise<ChatResult> {
    return this.record({ action: 'cancel' });
  }

  private record(call: SignInCall): Promise<ChatResult> {
    this.sent.push(call);
    return Promise.resolve(this.result);
  }
}

const SUBSCRIPTION = {
  id: 'claude_ai',
  name: 'Claude subscription',
  description: 'Pro, Max, Team or Enterprise plan',
};
const CLAUDE_METHODS: SignInRequest['methods'] = [
  SUBSCRIPTION,
  { id: 'console', name: 'Anthropic Console', description: 'API usage billing' },
];

function signIn(fields: Partial<SignInRequest> = {}): SignInRequest {
  return {
    id: 'sign-in',
    kind: 'sign_in',
    tool_call_id: null,
    title: 'Sign in to Claude Code',
    text: 'Claude Code is not signed in on your PC. Sign in here to continue.',
    stage: 'choose',
    methods: CLAUDE_METHODS,
    url: null,
    user_code: null,
    input: null,
    busy: false,
    error: null,
    ...fields,
  };
}

function renderCard(request: SignInRequest, live = true): FakeSignInModel {
  const model = new FakeSignInModel();
  render(
    <RequestDock model={model as unknown as ChatSessionModel} requests={[request]} live={live} />,
  );
  return model;
}

async function click(name: string): Promise<void> {
  await act(async () => {
    fireEvent.click(screen.getByRole('button', { name }));
    await Promise.resolve();
  });
}

afterEach(() => {
  cleanup();
});

describe('RequestDock — sign-in card, choose', () => {
  it('offers a button per method and starts the one picked', async () => {
    const model = renderCard(signIn());

    expect(screen.getByText('Sign in to Claude Code')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Not now' })).toBeInTheDocument();
    await click('Anthropic Console');

    expect(model.sent).toEqual([{ action: 'start', method: 'console' }]);
  });

  it('names the only method "Sign in"', async () => {
    const model = renderCard(signIn({ methods: [SUBSCRIPTION] }));

    await click('Sign in');

    expect(model.sent).toEqual([{ action: 'start', method: 'claude_ai' }]);
  });

  it('shows why the last try failed', () => {
    renderCard(signIn({ error: 'The sign-in did not finish.' }));

    expect(screen.getByRole('alert')).toHaveTextContent('The sign-in did not finish.');
  });

  it('shows a refused command', async () => {
    const model = renderCard(signIn());
    model.result = { ok: false, error: 'Unknown sign-in method', lost: false };

    await click('Claude subscription');

    expect(screen.getByRole('alert')).toHaveTextContent('Unknown sign-in method');
  });

  it('cannot start while the session is offline', () => {
    renderCard(signIn(), false);

    expect(screen.getByRole('button', { name: 'Claude subscription' })).toBeDisabled();
    expect(screen.getByRole('button', { name: 'Not now' })).toBeDisabled();
  });

  it('Not now cancels the sign-in', async () => {
    const model = renderCard(signIn());

    await click('Not now');

    expect(model.sent).toEqual([{ action: 'cancel' }]);
  });
});

describe('RequestDock — sign-in card, waiting', () => {
  const pasteCode = signIn({
    stage: 'waiting',
    text: 'Open the sign-in page and sign in. Then copy the code that the page shows and paste it here.',
    url: 'https://claude.ai/oauth/authorize?code=true',
    input: { label: 'Code from the sign-in page', secret: false },
  });

  it('links the sign-in page in a new tab', () => {
    renderCard(pasteCode);

    const link = screen.getByRole('link', { name: 'Open sign-in page' });
    expect(link).toHaveAttribute('href', 'https://claude.ai/oauth/authorize?code=true');
    expect(link).toHaveAttribute('target', '_blank');
    expect(link).toHaveAttribute('rel', 'noopener noreferrer');
    expect(screen.getByRole('button', { name: 'Cancel' })).toBeInTheDocument();
  });

  it.each(['http://example.com/sign-in', 'javascript:alert(1)'])('never links %s', (url) => {
    renderCard(signIn({ ...pasteCode, url }));

    expect(screen.queryByRole('link', { name: 'Open sign-in page' })).not.toBeInTheDocument();
  });

  it('submits the trimmed code and clears the field', async () => {
    const model = renderCard(pasteCode);
    const field = screen.getByLabelText('Code from the sign-in page');
    expect(screen.getByRole('button', { name: 'Submit' })).toBeDisabled();

    fireEvent.change(field, { target: { value: '  ab12-cd34  ' } });
    await click('Submit');

    expect(model.sent).toEqual([{ action: 'submit', value: 'ab12-cd34' }]);
    expect(field).toHaveValue('');
  });

  it('keeps the code when the submit is refused', async () => {
    const model = renderCard(pasteCode);
    model.result = { ok: false, error: 'The sign-in is not running', lost: false };
    const field = screen.getByLabelText('Code from the sign-in page');

    fireEvent.change(field, { target: { value: 'ab12' } });
    await click('Submit');

    expect(field).toHaveValue('ab12');
    expect(screen.getByRole('alert')).toHaveTextContent('The sign-in is not running');
  });

  it('locks the field while the PC checks the code', () => {
    renderCard({ ...pasteCode, busy: true, error: null });

    expect(screen.getByLabelText('Code from the sign-in page')).toBeDisabled();
    expect(screen.getByRole('button', { name: 'Submit' })).toBeDisabled();
  });

  const apiKey = signIn({
    stage: 'waiting',
    text: 'Paste an API key.',
    input: { label: 'OpenAI API key', secret: true },
  });

  it('hides a key as it is typed', () => {
    renderCard(apiKey);

    expect(screen.getByLabelText('OpenAI API key')).toHaveAttribute('type', 'password');
  });

  it('shows the key on request and hides it again', async () => {
    renderCard(apiKey);
    const field = screen.getByLabelText('OpenAI API key');

    await click('Show key');
    expect(field).toHaveAttribute('type', 'text');

    await click('Hide key');
    expect(field).toHaveAttribute('type', 'password');
  });

  it('submits the trimmed key', async () => {
    const model = renderCard(apiKey);

    fireEvent.change(screen.getByLabelText('OpenAI API key'), { target: { value: ' sk-test ' } });
    await click('Submit');

    expect(model.sent).toEqual([{ action: 'submit', value: 'sk-test' }]);
  });

  it('locks the key field while the PC checks the key', () => {
    renderCard({ ...apiKey, busy: true });

    expect(screen.getByLabelText('OpenAI API key')).toBeDisabled();
    expect(screen.getByRole('button', { name: 'Submit' })).toBeDisabled();
  });

  it('locks the key field while the session is offline', () => {
    renderCard(apiKey, false);

    expect(screen.getByLabelText('OpenAI API key')).toBeDisabled();
  });

  it('shows the one-time code and waits for the page without a field', () => {
    renderCard(
      signIn({
        stage: 'waiting',
        url: 'https://auth.openai.com/codex/device',
        user_code: 'CIQN-Y66ME',
        busy: true,
      }),
    );

    expect(screen.getByText('CIQN-Y66ME')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Copy' })).toBeInTheDocument();
    expect(screen.getByRole('status')).toHaveTextContent(
      'Waiting for you to finish on the sign-in page…',
    );
    expect(screen.queryByRole('textbox')).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Submit' })).not.toBeInTheDocument();
  });

  it('says the sign-in is starting before the page is known', () => {
    // The PC sends no text while it starts: the card shows its own status line.
    renderCard(signIn({ stage: 'waiting', text: '', busy: true }));

    expect(screen.getByRole('status')).toHaveTextContent('Starting the sign-in…');
    expect(screen.getAllByText('Starting the sign-in…')).toHaveLength(1);
    expect(screen.queryByRole('link')).not.toBeInTheDocument();
  });
});
