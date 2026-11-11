import { describe, expect, it } from 'vitest';
import {
  autoScanFilterSchema,
  projectInfoSchema,
  type AutoScanFilter,
  type ProjectInfo,
} from '../../protocol/dto/projects';
import { flagEntriesFrom, newFlagEntry } from './flagEntry';
import {
  buildProjectUpdate,
  buildProjectWrite,
  canSave,
  filterError,
  nameError,
  orderedUnion,
  pathError,
  showsFilterSection,
  type ProjectFormState,
} from './projectForm';

const project = (over: Record<string, unknown> = {}): ProjectInfo =>
  projectInfoSchema.parse({ name: 'proj', path: '/home/user/proj', ...over });

const filter = (over: Record<string, unknown> = {}): AutoScanFilter =>
  autoScanFilterSchema.parse(over);

/** The untouched form `ProjectFormDialog` seeds from a live snapshot. */
const formFor = (
  snapshot: ProjectInfo,
  over: Partial<ProjectFormState> = {},
): ProjectFormState => ({
  name: snapshot.name,
  path: snapshot.path,
  condaEnv: snapshot.conda_env ?? '',
  linearApiKey: '',
  clearLinearKey: false,
  flags: flagEntriesFrom(snapshot.default_flags),
  filter: snapshot.auto_scan_filter,
  ...over,
});

describe('buildProjectUpdate', () => {
  it('sends an empty body when the form still equals the snapshot', () => {
    const snapshot = project({
      conda_env: 'ml',
      default_flags: { plan_mode: true, effort_level: 3 },
    });

    expect(Object.keys(buildProjectUpdate(snapshot, formFor(snapshot)))).toEqual([]);
  });

  it('omits name and path whose trimmed value is unchanged, and trims the ones that changed', () => {
    const snapshot = project();

    const unchanged = buildProjectUpdate(
      snapshot,
      formFor(snapshot, { name: '  proj  ', path: ' /home/user/proj ' }),
    );
    expect(Object.hasOwn(unchanged, 'name')).toBe(false);
    expect(Object.hasOwn(unchanged, 'path')).toBe(false);

    const renamed = buildProjectUpdate(
      snapshot,
      formFor(snapshot, { name: '  renamed  ', path: '  /srv/renamed  ' }),
    );
    expect(renamed.name).toBe('renamed');
    expect(renamed.path).toBe('/srv/renamed');
  });

  it('clears the linear key with an explicit empty string', () => {
    const snapshot = project({ has_linear_key: true });

    const body = buildProjectUpdate(snapshot, formFor(snapshot, { clearLinearKey: true }));

    expect(Object.hasOwn(body, 'linear_api_key')).toBe(true);
    expect(body.linear_api_key).toBe('');
  });

  it('sends a typed linear key untrimmed, because an api key is opaque', () => {
    const snapshot = project();

    const body = buildProjectUpdate(snapshot, formFor(snapshot, { linearApiKey: ' lin_api_01 ' }));

    expect(body.linear_api_key).toBe(' lin_api_01 ');
  });

  it('clears conda_env with an empty string once the project had one', () => {
    const snapshot = project({ conda_env: 'ml' });

    const body = buildProjectUpdate(snapshot, formFor(snapshot, { condaEnv: '   ' }));

    expect(Object.hasOwn(body, 'conda_env')).toBe(true);
    expect(body.conda_env).toBe('');
  });

  it('omits default_flags while the editor mirrors the wire map, and clears it with {}', () => {
    const snapshot = project({ default_flags: { model: 'opus', plan_mode: true } });

    expect(Object.hasOwn(buildProjectUpdate(snapshot, formFor(snapshot)), 'default_flags')).toBe(
      false,
    );

    const cleared = buildProjectUpdate(snapshot, formFor(snapshot, { flags: [] }));
    expect(Object.hasOwn(cleared, 'default_flags')).toBe(true);
    expect(cleared.default_flags).toEqual({});
  });

  it('sends the whole flag map when a single row changed', () => {
    const snapshot = project({ default_flags: { model: 'opus' } });
    const flags = formFor(snapshot).flags;

    const body = buildProjectUpdate(snapshot, {
      ...formFor(snapshot),
      flags: [...flags, { ...newFlagEntry(), key: 'effort_level', kind: 'number', value: '3' }],
    });

    expect(body.default_flags).toEqual({ model: 'opus', effort_level: 3 });
  });

  it('compares the filter structurally, so an equal copy is not resent', () => {
    const snapshot = project();
    const copy = filter(
      JSON.parse(JSON.stringify(snapshot.auto_scan_filter)) as Record<string, unknown>,
    );

    const body = buildProjectUpdate(snapshot, formFor(snapshot, { filter: copy }));

    expect(Object.hasOwn(body, 'auto_scan_filter')).toBe(false);
  });

  it('replaces the filter wholesale when any field differs', () => {
    const snapshot = project();
    const edited = filter({ ...snapshot.auto_scan_filter, label_names: ['AutoPipeline', 'Bug'] });

    const body = buildProjectUpdate(snapshot, formFor(snapshot, { filter: edited }));

    expect(body.auto_scan_filter).toEqual(edited);
  });

  it('sends exactly the keys a full edit touched', () => {
    const snapshot = project({
      conda_env: 'ml',
      has_linear_key: true,
      default_flags: { plan_mode: true },
    });

    const body = buildProjectUpdate(
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

describe('buildProjectWrite', () => {
  it('always carries name, path, flags and filter, with name and path trimmed', () => {
    const state = formFor(project(), { name: '  new  ', path: '  /srv/new  ', flags: [] });

    const body = buildProjectWrite(state);

    expect(body).toEqual({
      name: 'new',
      path: '/srv/new',
      default_flags: {},
      auto_scan_filter: state.filter,
    });
  });

  it('omits blank optionals, trims conda_env and leaves a typed key untrimmed', () => {
    const snapshot = project();

    expect(Object.hasOwn(buildProjectWrite(formFor(snapshot, { condaEnv: '  ' })), 'conda_env')).toBe(
      false,
    );

    const body = buildProjectWrite(
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
    ['My Project_1.2-3', null],
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
    const snapshot = project();

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
  it('follows the key the project will have after the save', () => {
    const keyed = project({ has_linear_key: true });

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
