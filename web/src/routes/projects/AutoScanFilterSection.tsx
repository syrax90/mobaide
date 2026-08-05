import { SelectField, type SelectOption } from '../../components/form/SelectField';
import type { LinearLabel, LinearState } from '../../api/projectRepository';
import type { AutoScanFilter } from '../../protocol/dto/projects';
import { orderedUnion, stateTypeGlyph } from './projectForm';

interface AutoScanFilterSectionProps {
  filter: AutoScanFilter;
  labels: readonly LinearLabel[];
  states: readonly LinearState[];
  loading: boolean;
  error: string | null;
  onChange: (filter: AutoScanFilter) => void;
  onRefresh: () => void;
}

function toggle(values: readonly string[], value: string, ordered: readonly string[]) {
  const next = new Set(values);
  if (next.has(value)) next.delete(value);
  else next.add(value);
  // Re-emitted in option-list order rather than insertion order, so saving twice
  // with no edits produces identical bytes. iOS does the same; Android emits set
  // order and therefore does not.
  return ordered.filter((option) => next.has(option));
}

/**
 * The per-project Auto Scan filter — which Linear issues a scan picks up, and
 * which states and labels it writes back.
 *
 * Option lists are the ordered union of what Linear returned and what the
 * project already selects, so the form stays usable before discovery resolves,
 * when it fails, and when a saved value no longer exists in Linear.
 */
export function AutoScanFilterSection({
  filter,
  labels,
  states,
  loading,
  error,
  onChange,
  onRefresh,
}: AutoScanFilterSectionProps) {
  const labelOptions = orderedUnion(
    labels.map((label) => label.name),
    filter.label_names,
    [filter.removal_label],
  );

  const stateNames = orderedUnion(
    states.map((state) => state.name),
    filter.state_names,
    [filter.active_state, filter.done_state],
  );
  const stateType = new Map(states.map((state) => [state.name, state.type]));

  const stateSelectOptions: SelectOption[] = stateNames.map((name) => ({
    value: name,
    label: `${stateTypeGlyph(stateType.get(name) ?? '')} ${name}`,
  }));
  const labelSelectOptions: SelectOption[] = labelOptions.map((name) => ({
    value: name,
    label: name,
  }));

  return (
    <section className="option-group">
      <div className="option-group-head">
        <h3>Auto Scan filters</h3>
        <button type="button" className="button" disabled={loading} onClick={onRefresh}>
          {loading ? 'Refreshing…' : 'Refresh from Linear'}
        </button>
      </div>

      <p className="field-hint">
        Each matching issue runs in its own git worktree (branched per Linear issue), so
        its changes stay isolated from your working copy. Projects that aren&rsquo;t git
        repositories run in the project directory directly.
      </p>

      {error !== null && <p className="field-error">{error}</p>}

      <fieldset className="chip-group">
        <legend>Trigger labels</legend>
        <p className="field-hint">Issues with any of these labels will be picked up.</p>
        {labelOptions.length === 0 ? (
          <p className="field-hint">
            No labels found — verify your Linear API key and refresh.
          </p>
        ) : (
          <div className="chip-row">
            {labelOptions.map((name) => (
              <label key={name} className="chip">
                <input
                  type="checkbox"
                  checked={filter.label_names.includes(name)}
                  onChange={() => {
                    onChange({
                      ...filter,
                      label_names: toggle(filter.label_names, name, labelOptions),
                    });
                  }}
                />
                {name}
              </label>
            ))}
          </div>
        )}
        {filter.label_names.length === 0 && (
          <p className="field-error">Select at least one trigger label</p>
        )}
      </fieldset>

      <fieldset className="chip-group">
        <legend>Trigger states</legend>
        <p className="field-hint">
          Issues in any of these workflow states will be picked up.
        </p>
        {stateNames.length === 0 ? (
          <p className="field-hint">
            No workflow states found — verify your Linear API key and refresh.
          </p>
        ) : (
          <div className="chip-row">
            {stateNames.map((name) => (
              <label key={name} className="chip">
                <input
                  type="checkbox"
                  checked={filter.state_names.includes(name)}
                  onChange={() => {
                    onChange({
                      ...filter,
                      state_names: toggle(filter.state_names, name, stateNames),
                    });
                  }}
                />
                {stateTypeGlyph(stateType.get(name) ?? '')} {name}
              </label>
            ))}
          </div>
        )}
        {filter.state_names.length === 0 && (
          <p className="field-error">Select at least one trigger state</p>
        )}
      </fieldset>

      <SelectField
        label="Active state (set on claim)"
        value={filter.active_state}
        options={stateSelectOptions}
        onChange={(value) => {
          onChange({ ...filter, active_state: value ?? '' });
        }}
      />
      <SelectField
        label="Done state (cleanup trigger)"
        value={filter.done_state}
        options={stateSelectOptions}
        onChange={(value) => {
          onChange({ ...filter, done_state: value ?? '' });
        }}
      />
      <SelectField
        label="Removal label (stripped on claim)"
        value={filter.removal_label}
        options={labelSelectOptions}
        onChange={(value) => {
          onChange({ ...filter, removal_label: value ?? '' });
        }}
      />
    </section>
  );
}
