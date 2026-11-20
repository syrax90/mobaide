import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it } from 'vitest';
import type { AutoScanTemplate } from '../../../../api/autoscanRepository';
import type { AutoScanTemplatesView } from '../useAutoScanTemplates';
import { INITIAL_WIZARD_STATE } from '../wizardState';
import { AutoScanTemplatesPanel } from './AutoScanTemplatesPanel';

const SMALL_BUGS: AutoScanTemplate = {
  id: 't1',
  name: 'Small bugs',
  updated_at: '2026-11-19T10:00:00+00:00',
  settings: { source: 'github', cli: 'codex' },
};

function renderPanel(
  status: AutoScanTemplatesView['status'],
  templates: AutoScanTemplate[] = [],
): void {
  const view: AutoScanTemplatesView = {
    status,
    templates,
    loadError: status === 'error' ? 'HTTP 502' : null,
    retryLoad: () => undefined,
    save: () => Promise.reject(new Error('save is not under test')),
    saving: false,
    remove: () => Promise.reject(new Error('delete is not under test')),
    deleting: false,
  };
  render(
    <AutoScanTemplatesPanel templates={view} state={INITIAL_WIZARD_STATE} dispatch={() => undefined} />,
  );
}

const saveButton = () => screen.getByRole('button', { name: 'Save as template…' });

afterEach(() => {
  cleanup();
});

describe('AutoScanTemplatesPanel — Save as template', () => {
  // Without the list nothing can warn that the name replaces a saved template.
  it.each(['loading', 'error'] as const)('is not offered while the list is %s', (status) => {
    renderPanel(status);

    expect(saveButton()).toBeDisabled();
  });

  it('warns that a saved name is replaced once the list is in hand', () => {
    renderPanel('ready', [SMALL_BUGS]);

    fireEvent.click(saveButton());
    fireEvent.change(screen.getByRole('textbox', { name: 'Name' }), {
      target: { value: ' SMALL  bugs ' },
    });

    expect(screen.getByRole('button', { name: 'Replace' })).toBeEnabled();
  });
});
