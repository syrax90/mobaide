import { createAgentClient } from './clients';
import { toApiError } from './errors';

export interface ProjectSummary {
  name: string;
  path: string;
  order: number;
  /** Gates the Auto Scan mode card — a scan without a Linear key has nothing to read. */
  hasLinearKey: boolean;
  /** Gates the worktree section on every mode. */
  isGit: boolean;
  pathExists: boolean;
  /** Seeds the Auto Scan prompt; persisted server-side by the previous scan start. */
  autoScanPrompt: string | null;
}

/**
 * The agent's configured projects, in the order the user arranged them.
 *
 * Read-only here on purpose: project CRUD and the git surface are WEB-09. The
 * wizard needs the list to pick a working directory and to know whether the
 * selection is a git repo.
 */
export async function listProjects(agentId: string): Promise<ProjectSummary[]> {
  const { data, error, response } = await createAgentClient(agentId).GET('/api/projects');
  if (error !== undefined || data === undefined) {
    throw toApiError(response, error);
  }
  return data
    .map((project) => ({
      name: project.name,
      path: project.path,
      order: project.order,
      hasLinearKey: project.has_linear_key,
      isGit: project.is_git,
      pathExists: project.path_exists,
      autoScanPrompt: project.auto_scan_prompt ?? null,
    }))
    .sort((a, b) => a.order - b.order || a.name.localeCompare(b.name));
}
