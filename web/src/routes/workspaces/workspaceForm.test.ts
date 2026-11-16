import { describe, expect, it } from 'vitest';
import {
  autoScanFilterSchema,
  workspaceInfoSchema,
  type AutoScanFilter,
  type WorkspaceInfo,
} from '../../protocol/dto/workspaces';
import { flagEntriesFrom, newFlagEntry } from './flagEntry';
import {
  buildWorkspaceUpdate,
  buildWorkspaceWrite,
  canSave,
  filterError,
  nameError,
  orderedUnion,
  pathError,
  showsFilterSection,
  type WorkspaceFormState,
} from './workspaceForm';

const workspace = (over: Record<string, unknown> = {}): WorkspaceInfo =>
  workspaceInfoSchema.parse({ name: 'proj', path: '/home/user/proj', ...over });

const filter = (over: Record<string, unknown> = {}): AutoScanFilter =>
  autoScanFilterSchema.parse(over);

/** The untouched form `WorkspaceFormDialog` seeds from a live snapshot. */
const formFor = (
  snapshot: WorkspaceInfo,
  over: Partial<WorkspaceFormState> = {},
): WorkspaceFormState => ({
  name: snapshot.name,
  path: snapshot.path,
  condaEnv: snapshot.conda_env ?? '',
  linearApiKey: '',
  clearLinearKey: false,
  flags: flagEntriesFrom(snapshot.default_flags),
  filter: snapshot.auto_scan_filter,
  ...over,
});

describe('buildWorkspaceUpdate', () => {
  it('sends an empty body when the form still equals the snapshot', () => {
    const snapshot = workspace({
      conda_env: 'ml',
      default_flags: { plan_mode: true, effort_level: 3 },
    });

    expect(Object.keys(buildWorkspaceUpdate(snapshot, formFor(snapshot)))).toEqual([]);
  });

  it('omits name and path whose trimmed value is unchanged, and trims the ones that changed', () => {
    const snapshot = workspace();

    const unchanged = buildWorkspaceUpdate(
      snapshot,
      formFor(snapshot, { name: '  proj  ', path: ' /home/user/proj ' }),
    );
    expect(Object.hasOwn(unchanged, 'name')).toBe(false);
    expect(Object.hasOwn(unchanged, 'path')).toBe(false);

    const renamed = buildWorkspaceUpdate(
      snapshot,
      formFor(snapshot, { name: '  renamed  ', path: '  /srv/renamed  ' }),
    );
    expect(renamed.name).toBe('renamed');
    expect(renamed.path).toBe('/srv/renamed');
  });

  it('clears the linear key with an explicit empty string', () => {
    const snapshot = workspace({ has_linear_key: true });

    const body = buildWorkspaceUpdate(snapshot, formFor(snapshot, { clearLinearKey: true }));

    expect(Object.hasOwn(body, 'linear_api_key')).toBe(true);
    expect(body.linear_api_key).toBe('');
  });

  it('sends a typed linear key untrimmed, because an api key is opaque', () => {
    const snapshot = workspace();

    const body = buildWorkspaceUpdate(snapshot, formFor(snapshot, { linearApiKey: ' lin_api_01 ' }));

    expect(body.linear_api_key).toBe(' lin_api_01 ');
  });

  it('clears conda_env with an empty string once the workspace had one', () => {
    const snapshot = workspace({ conda_env: 'ml' });

    const body = buildWorkspaceUpdate(snapshot, formFor(snapshot, { condaEnv: '   ' }));

    expect(Object.hasOwn(body, 'conda_env')).toBe(true);
    expect(body.conda_env).toBe('');
  });

  it('omits default_flags while the editor mirrors the wire map, and clears it with {}', () => {
    const snapshot = workspace({ default_flags: { model: 'opus', plan_mode: true } });

    expect(Object.hasOwn(buildWorkspaceUpdate(snapshot, formFor(snapshot)), 'default_flags')).toBe(
      false,
    );

    const cleared = buildWorkspaceUpdate(snapshot, formFor(snapshot, { flags: [] }));
    expect(Object.hasOwn(cleared, 'default_flags')).toBe(true);
    expect(cleared.default_flags).toEqual({});
  });

  it('sends the whole flag map when a single row changed', () => {
    const snapshot = workspace({ default_flags: { model: 'opus' } });
    const flags = formFor(snapshot).flags;

    const body = buildWorkspaceUpdate(snapshot, {
      ...formFor(snapshot),
      flags: [...flags, { ...newFlagEntry(), key: 'effort_level', kind: 'number', value: '3' }],
    });

    expect(body.default_flags).toEqual({ model: 'opus', effort_level: 3 });
  });

  it('compares the filter structurally, so an equal copy is not resent', () => {
    const snapshot = workspace();
    const copy = filter(
      JSON.parse(JSON.stringify(snapshot.auto_scan_filter)) as Record<string, unknown>,
    );

    const body = buildWorkspaceUpdate(snapshot, formFor(snapshot, { filter: copy }));

    expect(Object.hasOwn(body, 'auto_scan_filter')).toBe(false);
  });

  it('replaces the filter wholesale when any field differs', () => {
    const snapshot = workspace();
    const edited = filter({ ...snapshot.auto_scan_filter, label_names: ['AutoPipeline', 'Bug'] });

    const body = buildWorkspaceUpdate(snapshot, formFor(snapshot, { filter: edited }));

    expect(body.auto_scan_filter).toEqual(edited);
  });

  it('sends exactly the keys a full edit touched', () => {
    const snapshot = workspace({
      conda_env: 'ml',
      has_linear_key: true,
      default_flags: { plan_mode: true },
    });

    const body = buildWorkspaceUpdate(
      snapshot,
      formFor(snapshot, {
        name: ' renamed ',
        condaEnv: '',
        clearLinearKey: true,
        flags: [],
        filter: filter({ ...snapshot.auto_scan_filter, done_state: 'Shipped' }),
      }),
    );

    expect(Object.keys(body).sort()).toEqual([
      'auto_scan_filter',
      'conda_env',
      'default_flags',
      'linear_api_key',
      'name',
    ]);
  });
});

describe('buildWorkspaceWrite', () => {
  it('always carries name, path, flags and filter, with name and path trimmed', () => {
    const state = formFor(workspace(), { name: '  new  ', path: '  /srv/new  ', flags: [] });

    const body = buildWorkspaceWrite(state);

    expect(body).toEqual({
      name: 'new',
      path: '/srv/new',
      default_flags: {},
      auto_scan_filter: state.filter,
    });
  });

  it('omits blank optionals, trims conda_env and leaves a typed key untrimmed', () => {
    const snapshot = workspace();

    expect(Object.hasOwn(buildWorkspaceWrite(formFor(snapshot, { condaEnv: '  ' })), 'conda_env')).toBe(
      false,
    );

    const body = buildWorkspaceWrite(
      formFor(snapshot, { condaEnv: '  ml  ', linearApiKey: ' lin_api_01 ' }),
    );
    expect(body.conda_env).toBe('ml');
    expect(body.linear_api_key).toBe(' lin_api_01 ');
  });
});

describe('validation', () => {
  it.each([
    ['   ', 'Name is required'],
    ['bad/name', 'Use letters, digits, spaces, _-.'],
    ['My Workspace_1.2-3', null],
  ])('nameError(%j)', (name, expected) => {
    expect(nameError(name)).toBe(expected);
  });

  it('requires a non-blank path', () => {
    expect(pathError('  ')).toBe('Path is required');
    expect(pathError(' /srv/p ')).toBeNull();
  });

  it.each([
    [{ label_names: [] }, 'Select at least one trigger label'],
    [{ done_state: '' }, 'Done state is required'],
    [{}, null],
  ])('filterError(%j)', (over, expected) => {
    expect(filterError(filter(over))).toBe(expected);
  });

  it('blocks save on an invalid field and allows it on the seeded defaults', () => {
    const snapshot = workspace();

    expect(canSave(formFor(snapshot))).toBe(true);
    expect(canSave(formFor(snapshot, { name: '' }))).toBe(false);
    expect(canSave(formFor(snapshot, { filter: filter({ label_names: [] }) }))).toBe(false);
    expect(
      canSave(
        formFor(snapshot, {
          flags: [
            { ...newFlagEntry(), key: 'dup', kind: 'string', value: 'a' },
            { ...newFlagEntry(), key: 'dup', kind: 'string', value: 'b' },
          ],
        }),
      ),
    ).toBe(false);
  });
});

describe('showsFilterSection', () => {
  it('follows the key the workspace will have after the save', () => {
    const keyed = workspace({ has_linear_key: true });

    expect(showsFilterSection({ linearApiKey: '', clearLinearKey: false }, keyed)).toBe(true);
    expect(showsFilterSection({ linearApiKey: '', clearLinearKey: true }, keyed)).toBe(false);
    expect(showsFilterSection({ linearApiKey: 'lin_1', clearLinearKey: false }, null)).toBe(true);
    expect(showsFilterSection({ linearApiKey: '  ', clearLinearKey: false }, null)).toBe(false);
  });
});

describe('orderedUnion', () => {
  it('lists discovered options first, then anything selected but undiscovered', () => {
    expect(orderedUnion(['Done', 'Backlog'], ['Backlog', 'Custom'])).toEqual([
      'Done',
      'Backlog',
      'Custom',
    ]);
  });

  it('drops blanks so an empty wire entry never becomes a chip', () => {
    expect(orderedUnion(['', 'Done'], [''])).toEqual(['Done']);
  });
});
