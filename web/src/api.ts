// hub REST 클라이언트 (dev 는 vite 프록시 경유).

export type Cluster = {
  id: string;
  name: string;
  cluster_mode: "OBSERVE" | "ACTIVE";
  remediation_frozen: boolean;
  connected: boolean;
  agent_version: string;
  metrics_available: boolean;
  last_seen: string | null;
  namespaces: number;
  pods: number;
  services: number;
  unhealthy_services: number;
  restarting_pods: number;
  open_issues: number;
};

export type ResourceQ = { cpu: string; memory: string };
export type Container = {
  name: string;
  requests: ResourceQ;
  limits: ResourceQ;
  usage_p95: ResourceQ;
};
export type Workload = {
  namespace: string;
  kind: string;
  name: string;
  replicas_desired: number;
  replicas_ready: number;
  distinct_nodes: number;
  containers: Container[];
};

export type Service = {
  namespace: string;
  name: string;
  type: string;
  ready_endpoints: number;
  desired_endpoints: number;
  healthy: boolean;
};

export type StorageLink = {
  namespace: string;
  pvc: string;
  pv: string;
  storage_class: string;
  capacity: string;
  bound_workloads: string[];
  phase: string;
};

export type RouteEdge = {
  namespace: string;
  httproute: string;
  gateway: string;
  hostname: string;
  path: string;
  backend_service: string;
  backend_port: number;
  weight: number;
};

export type Pod = {
  namespace: string;
  name: string;
  node: string;
  phase: string;
  restart_count: number;
  waiting_reason: string;
  last_terminated_reason: string;
  owner_kind: string;
  owner_name: string;
};

export type SuggestedAction = {
  action_type: string;
  target_kind: string;
  target_name: string;
  namespace: string;
  patch: unknown;
  risk_tier: string;
  auto_apply: boolean;
};

export type Issue = {
  fingerprint: string;
  rule_id: string;
  cluster_id: string;
  namespace: string;
  severity: string;
  title: string;
  detail: string;
  evidence: Record<string, unknown>;
  suggested_action: SuggestedAction | null;
  disposition: string;
};

export type Command = {
  command_id: string;
  cluster_id: string;
  namespace: string;
  target: string;
  rule_id: string;
  action_type: string;
  risk_tier: string;
  dry_run: boolean;
  mode_at_issue: string;
  issued_by: string;
  phase: string;
  resource_version_before: string;
  resource_version_after: string;
  error: string;
  issued_at: string | null;
  resolved_at: string | null;
};

// BASE_URL 은 dev 에서 "/", 운영 빌드에서 "/operating/". API/WS 경로에 prefix 로 사용한다.
export const BASE = import.meta.env.BASE_URL; // 항상 "/" 로 끝남
const P = (path: string) => `${BASE}${path.replace(/^\//, "")}`;

// ── 세션 토큰 ────────────────────────────────────────────────────────────────
const TOKEN_KEY = "canggu_token";
let _token = localStorage.getItem(TOKEN_KEY) || "";
export const getToken = () => _token;
export function setToken(t: string) {
  _token = t;
  localStorage.setItem(TOKEN_KEY, t);
}
export function clearToken() {
  _token = "";
  localStorage.removeItem(TOKEN_KEY);
}

function authHeaders(): Record<string, string> {
  return _token ? { Authorization: `Bearer ${_token}` } : {};
}

async function j<T>(r: Response): Promise<T> {
  if (r.status === 401) {
    clearToken();
    window.dispatchEvent(new Event("canggu-unauth"));
    throw new Error("401 인증 필요");
  }
  if (!r.ok) throw new Error(`${r.status} ${await r.text()}`);
  return r.json();
}

const get = (path: string) => fetch(P(path), { headers: authHeaders() }).then(j);

const post = (path: string, body: unknown) =>
  fetch(P(path), {
    method: "POST",
    headers: { "content-type": "application/json", ...authHeaders() },
    body: JSON.stringify(body),
  }).then(j);

export type Me = { username: string; role: string; scope_type: string; scope_ref: string };

export const api = {
  login: (username: string, password: string) =>
    post("api/auth/login", { username, password }) as Promise<{ token: string; user: Me }>,
  me: () => get("api/auth/me") as Promise<Me>,
  rules: () => get("api/rules") as Promise<
    { id: string; title: string; risk: string; default_auto: boolean; enabled: boolean; auto_apply: string }[]
  >,
  patchRule: (id: string, body: { enabled?: boolean; auto_apply?: string }) =>
    fetch(P(`api/rules/${id}`), {
      method: "PATCH",
      headers: { "content-type": "application/json", ...authHeaders() },
      body: JSON.stringify(body),
    }).then(j),
  listUsers: () => get("api/auth/users") as Promise<Me[]>,
  createUser: (u: { username: string; password: string; role: string; scope_type: string; scope_ref: string }) =>
    post("api/auth/users", u),
  updateUser: (username: string, body: { password?: string; role?: string; scope_type?: string; scope_ref?: string }) =>
    fetch(P(`api/auth/users/${encodeURIComponent(username)}`), {
      method: "PATCH", headers: { "content-type": "application/json", ...authHeaders() },
      body: JSON.stringify(body),
    }).then(j),
  deleteUser: (username: string) =>
    fetch(P(`api/auth/users/${encodeURIComponent(username)}`), {
      method: "DELETE", headers: authHeaders(),
    }).then(j),
  clusters: () => get("api/clusters") as Promise<Cluster[]>,
  namespaces: (c: string) => get(`api/clusters/${c}/namespaces`) as Promise<string[]>,
  pods: (c: string, ns: string) =>
    get(`api/clusters/${c}/pods?namespace=${encodeURIComponent(ns)}`) as Promise<Pod[]>,
  workloads: (c: string, ns: string) =>
    get(`api/clusters/${c}/workloads?namespace=${encodeURIComponent(ns)}`) as Promise<Workload[]>,
  services: (c: string, ns: string) =>
    get(`api/clusters/${c}/services?namespace=${encodeURIComponent(ns)}`) as Promise<Service[]>,
  storage: (c: string, ns: string) =>
    get(`api/clusters/${c}/storage?namespace=${encodeURIComponent(ns)}`) as Promise<StorageLink[]>,
  routes: (c: string, ns: string) =>
    get(`api/clusters/${c}/routes?namespace=${encodeURIComponent(ns)}`) as Promise<RouteEdge[]>,
  issues: (c: string, ns: string) =>
    get(`api/clusters/${c}/issues?namespace=${encodeURIComponent(ns)}`) as Promise<Issue[]>,
  commands: (c: string) => get(`api/commands?cluster_id=${c}`) as Promise<Command[]>,
  namespaceModes: (c: string) =>
    get(`api/clusters/${c}/namespace-modes`) as Promise<Record<string, string>>,
  setClusterMode: (c: string, mode: string) => post(`api/clusters/${c}/mode`, { mode }),
  setNamespaceMode: (c: string, ns: string, mode: string) =>
    post(`api/clusters/${c}/namespaces/${encodeURIComponent(ns)}/mode`, { mode }),
  setFreeze: (c: string, frozen: boolean) => post(`api/clusters/${c}/freeze`, { frozen }),
  applyIssue: (c: string, fp: string, dry_run: boolean) =>
    post(`api/clusters/${c}/issues/${fp}/apply`, { dry_run }),
  patchWorkload: (
    c: string, ns: string, kind: string, name: string, patch: unknown, dry_run: boolean,
  ) => post(`api/clusters/${c}/workloads/${ns}/${kind}/${name}/patch`, { patch, dry_run }),
  issueKubeconfig: (c: string, namespace: string, role: string, sa_name: string, ttl_seconds: number) =>
    post(`api/clusters/${c}/kubeconfig`, { namespace, role, sa_name, ttl_seconds }) as Promise<{
      kubeconfig: string; namespace: string; role: string; sa_name: string; ttl_seconds: number;
    }>,
  kubeconfigGrants: (c: string) =>
    get(`api/clusters/${c}/kubeconfig-grants`) as Promise<
      { namespace: string; role: string; sa_name: string; ttl_seconds: number; issued_by: string; issued_at: string }[]
    >,
  velero: (c: string) => get(`api/clusters/${c}/velero`) as Promise<{
    installed: boolean;
    backups: { name: string; phase: string; included_namespaces: string[]; created: string; completed: string; errors: number; warnings: number }[];
    restores: { name: string; backup_name: string; phase: string; created: string; errors: number; warnings: number }[];
    schedules: { name: string; cron: string; included_namespaces: string[]; paused: boolean; last_backup: string }[];
  }>,
  veleroBackup: (c: string, namespace: string) =>
    post(`api/clusters/${c}/velero/backup`, { namespace }) as Promise<{ backup: string }>,
  veleroRestore: (c: string, backup_name: string) =>
    post(`api/clusters/${c}/velero/restore`, { backup_name }) as Promise<{ restore: string }>,
  veleroSchedule: (c: string, namespace: string, cron: string) =>
    post(`api/clusters/${c}/velero/schedule`, { namespace, cron }) as Promise<{ schedule: string }>,
  veleroScheduleDelete: (c: string, name: string) =>
    fetch(P(`api/clusters/${c}/velero/schedule/${encodeURIComponent(name)}`), {
      method: "DELETE", headers: authHeaders(),
    }).then(j),
};
