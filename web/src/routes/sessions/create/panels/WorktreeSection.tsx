import { CheckboxField } from '../../../../components/form/CheckboxField';
import { TextField } from '../../../../components/form/TextField';
import type { GitBucket, WizardAction } from '../wizardState';

interface WorktreeSectionProps {
  git: GitBucket;
  dispatch: (action: WizardAction) => void;
}

/**
 * Only rendered for a stored git project — a one-off path has no repository to
 * branch from, and a non-git project has nothing to worktree.
 */
export function WorktreeSection({ git, dispatch }: WorktreeSectionProps) {
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
      <TextField
        label="Branch name (optional)"
        value={git.branchName}
        placeholder="feature/my-change"
        onChange={(value) => {
          dispatch({ type: 'setBranchName', value });
        }}
      />
    </div>
  );
}
