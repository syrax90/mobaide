// @vitest-environment node
import { describe, expect, it } from 'vitest';
import { herdrPanePath, herdrPaneTag } from './herdrPath';

describe('herdrPanePath', () => {
  it.each([
    ['tui:default', 'w1:p1'],
    ['core:default', 'box/t3'],
    ['tui:my work', 'a&pane=b?c#d%2F'],
  ])('carries the ids %s and %s in the query, whole', (serverId, paneId) => {
    // A pane id contains `:` and can contain `/`: in the path it would name
    // another route.
    const url = new URL(herdrPanePath('agent/1', serverId, paneId), 'https://app.test');

    expect(url.pathname).toBe('/agents/agent%2F1/herdr/pane');
    expect(url.searchParams.get('server')).toBe(serverId);
    expect(url.searchParams.get('pane')).toBe(paneId);
  });
});

describe('herdrPaneTag', () => {
  it('is one per agent, server and pane', () => {
    const tag = herdrPaneTag('agent-1', 'tui:default', 'w1:p1');

    expect(herdrPaneTag('agent-1', 'tui:default', 'w1:p1')).toBe(tag);
    expect(herdrPaneTag('agent-2', 'tui:default', 'w1:p1')).not.toBe(tag);
    expect(herdrPaneTag('agent-1', 'tui:work', 'w1:p1')).not.toBe(tag);
    expect(herdrPaneTag('agent-1', 'tui:default', 'w1:p2')).not.toBe(tag);
  });
});
