/**
 * The agent's configured projects.
 *
 * The wire shape is preserved rather than mapped to camelCase, for the reason
 * `sessionRepository.ts` documents: REST and the `projects.changed` push write
 * the *same* query cache, so the two must agree field for field. Decoding both
 * through `projectInfoSchema` is what guarantees that.
 *
 * Mutations never patch the cache. The agent republishes the whole list after
 * every successful create/update/delete/reorder, and that echo is the truth —
 * the rule both mobile repositories state and this one inherits.
 */
import type { components } from './generated/agent';
import { createAgentClient } from './clients';
import { toApiError } from './errors';
import { projectInfoSchema, type ProjectInfo } from '../protocol/dto/projects';

export type { ProjectInfo } from '../protocol/dto/projects';

/**
 * The subset the session wizard reads.
 *
 * Structural, so a full `ProjectInfo` satisfies it — it exists to keep the
 * wizard from depending on the flag map and Auto Scan filter it never touches,
 * and to keep its fixtures to the fields under test.
 */
export type ProjectSummary = Pick<
  ProjectInfo,
  'name' | 'path' | 'order' | 'has_linear_key' | 'is_git' | 'path_exists' | 'auto_scan_prompt'
>;

export type ProjectWriteBody = components['schemas']['ProjectWriteModel'];
export type ProjectUpdateBody = components['schemas']['ProjectUpdateModel'];
export type LinearLabel = components['schemas']['LinearLabelModel'];
export type LinearState = components['schemas']['LinearStateModel'];

/**
 * Server order, with a name tiebreak.
 *
 * The tiebreak is web-only: neither mobile client has one, and `order` is
 * unique in practice, but a stable comparator keeps a reorder animation from
 * depending on `Array.prototype.sort` stability across two equal keys.
 */
function byOrder(a: ProjectInfo, b: ProjectInfo): number {
  return a.order - b.order || a.name.localeCompare(b.name);
}

export function sortProjects(projects: readonly ProjectInfo[]): ProjectInfo[] {
  return [...projects].sort(byOrder);
}

export async function listProjects(agentId: string): Promise<ProjectInfo[]> {
  const { data, error, response } = await createAgentClient(agentId).GET('/api/projects');
  if (error !== undefined || data === undefined) {
    throw toApiError(response, error);
  }
  return sortProjects(data.map((project) => projectInfoSchema.parse(project)));
}

export async function createProject(
  agentId: string,
  body: ProjectWriteBody,
): Promise<ProjectInfo> {
  const { data, error, response } = await createAgentClient(agentId).POST('/api/projects', {
    body,
  });
  if (error !== undefined || data === undefined) {
    throw toApiError(response, error);
  }
  return projectInfoSchema.parse(data);
}

/**
 * `PUT /api/projects/{name}` with a three-state patch — see `buildProjectUpdate`
 * for which keys may be absent, `""` or valued. `name` in the body is a rename.
 */
export async function updateProject(
  agentId: string,
  currentName: string,
  body: ProjectUpdateBody,
): Promise<ProjectInfo> {
  const { data, error, response } = await createAgentClient(agentId).PUT(
    '/api/projects/{name}',
    { params: { path: { name: currentName } }, body },
  );
  if (error !== undefined || data === undefined) {
    throw toApiError(response, error);
  }
  return projectInfoSchema.parse(data);
}

export async function deleteProject(agentId: string, name: string): Promise<void> {
  const { error, response } = await createAgentClient(agentId).DELETE(
    '/api/projects/{name}',
    { params: { path: { name } } },
  );
  if (error !== undefined) throw toApiError(response, error);
}

/**
 * Replace the ordering atomically. `names` must be the **complete** set — the
 * server answers 400 on any duplicate, missing or unknown name.
 *
 * The 200 body is the re-sorted list, so the caller can write it straight into
 * the cache instead of waiting for the `projects.changed` echo that follows.
 */
export async function reorderProjects(
  agentId: string,
  names: readonly string[],
): Promise<ProjectInfo[]> {
  const { data, error, response } = await createAgentClient(agentId).PATCH(
    '/api/projects/order',
    { body: { names: [...names] } },
  );
  if (error !== undefined || data === undefined) {
    throw toApiError(response, error);
  }
  return sortProjects(data.map((project) => projectInfoSchema.parse(project)));
}

/**
 * Labels visible to the project's Linear key, for the Auto Scan filter chips.
 * 400 when the project has no key, 502 when Linear itself fails — both surface
 * as an inline "couldn't load" state, never a blocked form.
 */
export async function listLinearLabels(
  agentId: string,
  name: string,
): Promise<LinearLabel[]> {
  const { data, error, response } = await createAgentClient(agentId).GET(
    '/api/projects/{name}/linear/labels',
    { params: { path: { name } } },
  );
  if (error !== undefined || data === undefined) {
    throw toApiError(response, error);
  }
  return data;
}

export async function listLinearStates(
  agentId: string,
  name: string,
): Promise<LinearState[]> {
  const { data, error, response } = await createAgentClient(agentId).GET(
    '/api/projects/{name}/linear/states',
    { params: { path: { name } } },
  );
  if (error !== undefined || data === undefined) {
    throw toApiError(response, error);
  }
  return data;
}
