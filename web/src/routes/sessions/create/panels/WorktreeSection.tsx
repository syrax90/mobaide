import { CheckboxField } from '../../../../components/form/CheckboxField';
import { TextField } from '../../../../components/form/TextField';
import type { GitBucket, WizardAction } from '../wizardState';
import { BaseBranchPicker, type BaseBranchPickerProps } from './BaseBranchPicker';

interface WorktreeSectionProps {
  git: GitBucket;
  dispatch: (action: WizardAction) => void;
  /** `null` hides the base-branch picker: an agent without `capabilities.git.worktree_base`. */
  baseBranch: BaseBranchPickerProps | null;
}

/**
 * Only rendered for a stored git project — a one-off path has no repository to
 * branch from, and a non-git project has nothing to worktree.
 */
export function WorktreeSection({ git, dispatch, baseBranch }: WorktreeSectionProps) {
  return (
    <div className="option-group">
      <h3>Worktree</h3>
      <CheckboxField
        label="Create worktree"
        checked={git.worktree}
        hint="Runs the session in its own git worktree, isolating its changes from your working copy."
        onChange={(value) => {
          dispatch({ type: 'setWorktree', value });
        }}
      />
      {/* Hidden rather than disabled, matching both mobile clients: without a
          worktree the agent never reads the branch name at all. */}
      {git.worktree && (
        <TextField
          label="Branch name (optional)"
          value={git.branchName}
          placeholder="feature/my-change"
          onChange={(value) => {
            dispatch({ type: 'setBranchName', value });
          }}
        />
      )}
      {git.worktree && baseBranch !== null && <BaseBranchPicker {...baseBranch} />}
    </div>
  );
}
