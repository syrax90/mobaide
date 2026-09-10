import type { ReactNode } from 'react';

interface SessionGitStateProps {
  title: string;
  body: ReactNode;
  /** A monospaced detail line, such as the session's folder. */
  detail?: string;
  action?: { label: string; onClick: () => void };
}

/** Full-pane message for the viewer's non-list states, shared by both panes. */
export function SessionGitState({ title, body, detail, action }: SessionGitStateProps) {
  return (
    <div className="git-state" role="status">
      <h3>{title}</h3>
      <p>{body}</p>
      {detail !== undefined && <p className="git-state-detail">{detail}</p>}
      {action !== undefined && (
        <div className="button-row">
          <button type="button" className="button" onClick={action.onClick}>
            {action.label}
          </button>
        </div>
      )}
    </div>
  );
}
