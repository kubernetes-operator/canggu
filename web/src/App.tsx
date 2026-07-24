import { useCallback, useEffect, useRef, useState } from "react";
import {
  api, BASE, clearToken, Cluster, Command, getToken, Issue, Me, Pod, RouteEdge, Service, setToken,
  StorageLink, Workload,
} from "./api";

type LiveEvent = Record<string, unknown> & { kind: string; ts?: string };
type EditState = {
  ns: string; kind: string; name: string; container: string;
  reqCpu: string; reqMem: string; limCpu: string; limMem: string;
};

export default function App() {
  const [me, setMe] = useState<Me | null>(null);
  const [authReady, setAuthReady] = useState(false);
  const [clusters, setClusters] = useState<Cluster[]>([]);
  const [selected, setSelected] = useState<string>("");
  const [namespaces, setNamespaces] = useState<string[]>([]);
  const [nsModes, setNsModes] = useState<Record<string, string>>({});
  const [ns, setNs] = useState<string>("all");
  const [pods, setPods] = useState<Pod[]>([]);
  const [services, setServices] = useState<Service[]>([]);
  const [workloads, setWorkloads] = useState<Workload[]>([]);
  const [storage, setStorage] = useState<StorageLink[]>([]);
  const [routes, setRoutes] = useState<RouteEdge[]>([]);
  const [issues, setIssues] = useState<Issue[]>([]);
  const [commands, setCommands] = useState<Command[]>([]);
  const [events, setEvents] = useState<LiveEvent[]>([]);
  const [err, setErr] = useState<string>("");
  const [edit, setEdit] = useState<EditState | null>(null);

  const cluster = clusters.find((c) => c.id === selected);
  const isAdmin = me?.role === "admin";

  // 인증 부트스트랩: 저장된 토큰 검증 + 401 시 로그아웃.
  useEffect(() => {
    const onUnauth = () => setMe(null);
    window.addEventListener("canggu-unauth", onUnauth);
    if (getToken()) {
      api.me().then(setMe).catch(() => clearToken()).finally(() => setAuthReady(true));
    } else {
      setAuthReady(true);
    }
    return () => window.removeEventListener("canggu-unauth", onUnauth);
  }, []);

  const refreshClusters = useCallback(async () => {
    if (!me) return;
    try {
      const cs = await api.clusters();
      setClusters(cs);
      if (!selected && cs.length) setSelected(cs[0].id);
    } catch (e) {
      setErr(String(e));
    }
  }, [selected, me]);

  const refreshData = useCallback(async () => {
    if (!selected || !me) return;
    try {
      const [nsList, nsm, p, svc, wl, st, rt, i, cmds] = await Promise.all([
        api.namespaces(selected).catch(() => []),
        api.namespaceModes(selected).catch(() => ({}) as Record<string, string>),
        api.pods(selected, ns).catch(() => []),
        api.services(selected, ns).catch(() => []),
        api.workloads(selected, ns).catch(() => []),
        api.storage(selected, ns).catch(() => []),
        api.routes(selected, ns).catch(() => []),
        api.issues(selected, ns).catch(() => []),
        api.commands(selected).catch(() => []),
      ]);
      setNamespaces(nsList);
      setNsModes(nsm);
      setPods(p);
      setServices(svc);
      setWorkloads(wl);
      setStorage(st);
      setRoutes(rt);
      setIssues(i);
      setCommands(cmds);
    } catch (e) {
      setErr(String(e));
    }
  }, [selected, ns, me]);

  useEffect(() => {
    refreshClusters();
    const t = setInterval(refreshClusters, 5000);
    return () => clearInterval(t);
  }, [refreshClusters]);

  useEffect(() => {
    refreshData();
    const t = setInterval(refreshData, 4000);
    return () => clearInterval(t);
  }, [refreshData]);

  // 라이브 피드 WebSocket
  const wsRef = useRef<WebSocket | null>(null);
  useEffect(() => {
    if (!me) return;
    const proto = location.protocol === "https:" ? "wss" : "ws";
    const ws = new WebSocket(
      `${proto}://${location.host}${BASE}live/ws?token=${encodeURIComponent(getToken())}`,
    );
    wsRef.current = ws;
    ws.onmessage = (m) => {
      const ev = JSON.parse(m.data) as LiveEvent;
      setEvents((prev) => [ev, ...prev].slice(0, 60));
      if (["command_result", "mode_changed", "freeze_changed"].includes(ev.kind)) {
        refreshData();
        refreshClusters();
      }
    };
    ws.onclose = () => (wsRef.current = null);
    return () => ws.close();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [me]);

  async function toggleMode() {
    if (!cluster) return;
    await api.setClusterMode(cluster.id, cluster.cluster_mode === "ACTIVE" ? "OBSERVE" : "ACTIVE");
    refreshClusters();
  }
  async function toggleFreeze() {
    if (!cluster) return;
    await api.setFreeze(cluster.id, !cluster.remediation_frozen);
    refreshClusters();
  }
  async function toggleNsMode() {
    if (!selected || ns === "all") return;
    const cur = nsModes[ns] ?? cluster?.cluster_mode ?? "OBSERVE";
    await api.setNamespaceMode(selected, ns, cur === "ACTIVE" ? "OBSERVE" : "ACTIVE");
    refreshData();
  }
  async function apply(fp: string, dry: boolean) {
    if (!selected) return;
    try {
      await api.applyIssue(selected, fp, dry);
      refreshData();
    } catch (e) {
      alert("적용 실패: " + e);
    }
  }
  async function applyEdit(dry: boolean) {
    if (!selected || !edit) return;
    const res: Record<string, Record<string, string>> = {};
    if (edit.reqCpu || edit.reqMem)
      res.requests = { ...(edit.reqCpu && { cpu: edit.reqCpu }), ...(edit.reqMem && { memory: edit.reqMem }) };
    if (edit.limCpu || edit.limMem)
      res.limits = { ...(edit.limCpu && { cpu: edit.limCpu }), ...(edit.limMem && { memory: edit.limMem }) };
    if (!res.requests && !res.limits) {
      alert("변경할 값을 입력하세요");
      return;
    }
    const patch = { spec: { template: { spec: { containers: [{ name: edit.container, resources: res }] } } } };
    try {
      await api.patchWorkload(selected, edit.ns, edit.kind, edit.name, patch, dry);
      if (!dry) setEdit(null);
      refreshData();
    } catch (e) {
      alert("편집 적용 실패: " + e);
    }
  }

  if (authReady && !me) return <Login onLogin={setMe} />;
  if (!me) return <div className="app"><div className="empty">로딩 중…</div></div>;

  return (
    <div className="app">
      <header>
        <h1>canggu</h1>
        <span className="sub">Kubernetes 자동 운영</span>
        <div className="spacer" />
        <span className="stat">
          👤 {me.username} · {me.role}
          {me.role !== "admin" && " (읽기전용)"}
        </span>
        <button onClick={() => { clearToken(); setMe(null); }}>로그아웃</button>
        <select value={selected} onChange={(e) => setSelected(e.target.value)}>
          {clusters.length === 0 && <option value="">(클러스터 없음)</option>}
          {clusters.map((c) => (
            <option key={c.id} value={c.id}>
              {c.name} {c.connected ? "🟢" : "⚪"}
            </option>
          ))}
        </select>
      </header>

      {err && <div className="err">{err}</div>}

      {cluster && (
        <section className="controls">
          <div className={`mode ${cluster.cluster_mode}`}>
            모드: <b>{cluster.cluster_mode === "ACTIVE" ? "자동조정(ACTIVE)" : "관찰전용(OBSERVE)"}</b>
            {isAdmin && (
              <button onClick={toggleMode}>
                {cluster.cluster_mode === "ACTIVE" ? "관찰전용으로" : "자동조정으로"}
              </button>
            )}
          </div>
          <div className={`freeze ${cluster.remediation_frozen ? "on" : ""}`}>
            {cluster.remediation_frozen ? "🧊 Freeze 됨" : "freeze 해제"}
            {isAdmin && (
              <button onClick={toggleFreeze}>
                {cluster.remediation_frozen ? "해제" : "전역 freeze"}
              </button>
            )}
          </div>
          <div className="stat">agent {cluster.agent_version || "-"}</div>
          <div className="stat">metrics {cluster.metrics_available ? "✓" : "✗"}</div>
          <div className="stat">🔁 재기동 {cluster.restarting_pods}</div>
          <div className="stat">🔴 비정상 SVC {cluster.unhealthy_services}</div>
          <div className="spacer" />
          <label>
            Namespace{" "}
            <select value={ns} onChange={(e) => setNs(e.target.value)}>
              <option value="all">전체</option>
              {namespaces.map((n) => (
                <option key={n} value={n}>{n}</option>
              ))}
            </select>
          </label>
          {ns !== "all" && (() => {
            const nsMode = nsModes[ns] ?? cluster.cluster_mode;
            const effective =
              cluster.cluster_mode === "OBSERVE" || nsMode === "OBSERVE" ? "OBSERVE" : "ACTIVE";
            return (
              <div className={`mode ${effective}`}>
                NS <b>{ns}</b> 유효모드 <b>{effective}</b>
                <span className="stat">(override: {nsModes[ns] ?? "상속"})</span>
                {isAdmin && (
                  <button onClick={toggleNsMode}>
                    {(nsModes[ns] ?? cluster.cluster_mode) === "ACTIVE" ? "이 NS 관찰전용" : "이 NS 자동조정"}
                  </button>
                )}
              </div>
            );
          })()}
        </section>
      )}

      <div className="grid">
        <Panel title={`이슈 & 해결 제안 (${issues.length})`}>
          {issues.length === 0 && <div className="empty">이슈 없음</div>}
          {issues.map((i) => (
            <div key={i.fingerprint} className={`issue sev-${i.severity}`}>
              <div className="issue-title">
                <span className={`badge ${i.severity}`}>{i.rule_id}</span> {i.title}
              </div>
              <div className="issue-detail">{i.detail}</div>
              <div className="issue-detail mono">{JSON.stringify(i.evidence)}</div>
              <div className="issue-foot">
                <span className={`disp ${i.disposition}`}>{i.disposition}</span>
                {isAdmin && i.suggested_action && (
                  <>
                    <button onClick={() => apply(i.fingerprint, true)}>Dry-run</button>
                    <button className="primary" onClick={() => apply(i.fingerprint, false)}>
                      적용
                    </button>
                  </>
                )}
              </div>
            </div>
          ))}
        </Panel>

        <Panel title="자동조정 라이브 피드">
          {events.length === 0 && <div className="empty">대기 중…</div>}
          <ul className="feed">
            {events.map((e, idx) => (
              <li key={idx}>
                <span className={`ev ${e.kind}`}>{e.kind}</span>{" "}
                <span className="mono">{summarize(e)}</span>
              </li>
            ))}
          </ul>
        </Panel>

        <Panel title={`Services 헬스 (${services.filter((s) => !s.healthy).length} 비정상 / ${services.length})`}>
          {services.length === 0 && <div className="empty">서비스 없음</div>}
          <table>
            <thead>
              <tr><th>상태</th><th>Namespace</th><th>Service</th><th>Type</th><th>Endpoints</th></tr>
            </thead>
            <tbody>
              {[...services].sort((a, b) => Number(a.healthy) - Number(b.healthy)).map((s) => (
                <tr key={s.namespace + s.name} className={s.healthy ? "" : "warn-row"}>
                  <td>{s.healthy ? "🟢" : "🔴"}</td>
                  <td>{s.namespace}</td>
                  <td>{s.name}</td>
                  <td>{s.type}</td>
                  <td className="mono">{s.ready_endpoints}/{s.desired_endpoints}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </Panel>

        <Panel title={`Workloads · 사용량 vs requests (${workloads.length})`} wide>
          <table>
            <thead>
              <tr>
                <th>Namespace</th><th>Workload</th><th>Ready</th><th>Nodes</th>
                <th>Container</th><th>CPU (usage/req)</th><th>Mem (usage/req)</th><th></th>
              </tr>
            </thead>
            <tbody>
              {workloads.flatMap((w) =>
                w.containers.map((c) => (
                  <tr key={w.namespace + w.name + c.name}
                      className={w.distinct_nodes < 2 && w.replicas_desired > 1 ? "warn-row" : ""}>
                    <td>{w.namespace}</td>
                    <td>{w.kind}/{w.name}</td>
                    <td>{w.replicas_ready}/{w.replicas_desired}</td>
                    <td>{w.distinct_nodes < 2 && w.replicas_desired > 1 ? `⚠️ ${w.distinct_nodes}` : w.distinct_nodes}</td>
                    <td>{c.name}</td>
                    <td className="mono">{c.usage_p95.cpu || "-"} / {c.requests.cpu || "unset"}</td>
                    <td className="mono">{c.usage_p95.memory || "-"} / {c.requests.memory || "unset"}</td>
                    <td>
                      {isAdmin && (
                        <button onClick={() => setEdit({
                          ns: w.namespace, kind: w.kind, name: w.name, container: c.name,
                          reqCpu: c.requests.cpu, reqMem: c.requests.memory,
                          limCpu: c.limits.cpu, limMem: c.limits.memory,
                        })}>편집</button>
                      )}
                    </td>
                  </tr>
                )),
              )}
            </tbody>
          </table>
        </Panel>

        <Panel title={`Pods (${pods.length})`} wide>
          <table>
            <thead>
              <tr>
                <th>Namespace</th><th>Pod</th><th>Node</th><th>Phase</th>
                <th>Restarts</th><th>Waiting</th>
              </tr>
            </thead>
            <tbody>
              {pods.map((p) => (
                <tr key={p.namespace + p.name} className={p.restart_count > 0 ? "warn-row" : ""}>
                  <td>{p.namespace}</td>
                  <td>{p.name}</td>
                  <td>{p.node}</td>
                  <td>{p.phase}</td>
                  <td>{p.restart_count > 0 ? `🔁 ${p.restart_count}` : p.restart_count}</td>
                  <td className="mono">{p.waiting_reason || p.last_terminated_reason}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </Panel>

        <Panel title={`HTTPRoute 트래픽 흐름 (${routes.length})`} wide>
          {routes.length === 0 && <div className="empty">HTTPRoute 없음</div>}
          <div className="flows">
            {routes.map((r, i) => (
              <div className="flow" key={i}>
                <span className="node host">🌐 {r.hostname || "*"}{r.path}</span>
                <span className="arrow">→</span>
                <span className="node gw">🚪 {r.gateway || "gateway"}</span>
                <span className="arrow">→</span>
                <span className="node route">🧭 {r.namespace}/{r.httproute}</span>
                <span className="arrow">→</span>
                <span className="node svc">
                  ⚙️ {r.backend_service}:{r.backend_port}
                  {r.weight !== 1 && <em> w={r.weight}</em>}
                </span>
              </div>
            ))}
          </div>
        </Panel>

        <Panel title={`스토리지 토폴로지 SC↔PV↔PVC↔워크로드 (${storage.length})`} wide>
          {storage.length === 0 && <div className="empty">PVC 없음</div>}
          <table>
            <thead>
              <tr>
                <th>상태</th><th>Namespace</th><th>StorageClass</th><th>PV</th>
                <th>PVC</th><th>용량</th><th>사용 워크로드</th>
              </tr>
            </thead>
            <tbody>
              {[...storage].sort((a, b) => (a.phase === "Bound" ? 1 : 0) - (b.phase === "Bound" ? 1 : 0))
                .map((s, i) => (
                <tr key={i} className={s.phase !== "Bound" ? "warn-row" : ""}>
                  <td>{s.phase === "Bound" ? "🟢" : "🟡"} {s.phase}</td>
                  <td>{s.namespace}</td>
                  <td>{s.storage_class || "-"}</td>
                  <td className="mono">{s.pv || "(미바인딩)"}</td>
                  <td>{s.pvc}</td>
                  <td className="mono">{s.capacity || "-"}</td>
                  <td>{s.bound_workloads.join(", ") || "-"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </Panel>

        <Panel title="명령 감사 원장" wide>
          <table>
            <thead>
              <tr>
                <th>시각</th><th>NS</th><th>대상</th><th>규칙</th><th>모드</th>
                <th>발행자</th><th>상태</th><th>rv</th>
              </tr>
            </thead>
            <tbody>
              {commands.map((c) => (
                <tr key={c.command_id}>
                  <td className="mono">{c.issued_at?.slice(11, 19)}</td>
                  <td>{c.namespace}</td>
                  <td>{c.target}</td>
                  <td>{c.rule_id}</td>
                  <td>{c.mode_at_issue}</td>
                  <td>{c.issued_by}</td>
                  <td className={`phase ${c.phase}`}>{c.phase}{c.dry_run ? " (dry)" : ""}</td>
                  <td className="mono">{c.resource_version_before}→{c.resource_version_after}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </Panel>

        <AccessPanel cluster={selected} namespaces={namespaces} isAdmin={!!isAdmin} />
        <VeleroPanel cluster={selected} namespaces={namespaces} isAdmin={!!isAdmin} />
        <RulesPanel isAdmin={!!isAdmin} />
        <UsersPanel isAdmin={!!isAdmin} clusterId={selected} />
      </div>

      {edit && (
        <div className="modal-backdrop" onClick={() => setEdit(null)}>
          <div className="modal" onClick={(e) => e.stopPropagation()}>
            <h3>수동 리소스 편집</h3>
            <div className="mono edit-target">
              {edit.kind}/{edit.name} · ns={edit.ns} · container={edit.container}
            </div>
            <div className="edit-grid">
              <label>requests.cpu
                <input value={edit.reqCpu} placeholder="예: 250m"
                  onChange={(e) => setEdit({ ...edit, reqCpu: e.target.value })} /></label>
              <label>requests.memory
                <input value={edit.reqMem} placeholder="예: 256Mi"
                  onChange={(e) => setEdit({ ...edit, reqMem: e.target.value })} /></label>
              <label>limits.cpu
                <input value={edit.limCpu} placeholder="예: 500m"
                  onChange={(e) => setEdit({ ...edit, limCpu: e.target.value })} /></label>
              <label>limits.memory
                <input value={edit.limMem} placeholder="예: 512Mi"
                  onChange={(e) => setEdit({ ...edit, limMem: e.target.value })} /></label>
            </div>
            <div className="edit-actions">
              <button onClick={() => setEdit(null)}>취소</button>
              <button onClick={() => applyEdit(true)}>Dry-run</button>
              <button className="primary" onClick={() => applyEdit(false)}>적용</button>
            </div>
            <div className="stat">※ 적용 시 hub→agent 로 strategic-merge patch 가 전송되고 감사 원장에 기록됩니다.</div>
          </div>
        </div>
      )}
    </div>
  );
}

function Login({ onLogin }: { onLogin: (me: Me) => void }) {
  const [u, setU] = useState("");
  const [p, setP] = useState("");
  const [err, setErr] = useState("");
  const [busy, setBusy] = useState(false);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setErr("");
    try {
      const r = await api.login(u, p);
      setToken(r.token);
      onLogin(r.user);
    } catch {
      setErr("로그인 실패 — 아이디/비밀번호를 확인하세요");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="app login-wrap">
      <form className="login" onSubmit={submit}>
        <h1>canggu</h1>
        <div className="sub">Kubernetes 자동 운영 · 로그인</div>
        <input placeholder="아이디" value={u} onChange={(e) => setU(e.target.value)} autoFocus />
        <input placeholder="비밀번호" type="password" value={p}
          onChange={(e) => setP(e.target.value)} />
        {err && <div className="err">{err}</div>}
        <button className="primary" disabled={busy} type="submit">
          {busy ? "확인 중…" : "로그인"}
        </button>
      </form>
    </div>
  );
}

function AccessPanel(
  { cluster, namespaces, isAdmin }: { cluster: string; namespaces: string[]; isAdmin: boolean },
) {
  const [ns, setNs] = useState("");
  const [role, setRole] = useState("view");
  const [sa, setSa] = useState("");
  const [ttl, setTtl] = useState(3600);
  const [busy, setBusy] = useState(false);
  const [grants, setGrants] = useState<Awaited<ReturnType<typeof api.kubeconfigGrants>>>([]);

  const loadGrants = useCallback(() => {
    api.kubeconfigGrants(cluster).then(setGrants).catch(() => {});
  }, [cluster]);
  useEffect(() => { loadGrants(); }, [loadGrants]);
  useEffect(() => { if (!ns && namespaces.length) setNs(namespaces[0]); }, [namespaces, ns]);

  async function issue() {
    if (!ns) return;
    setBusy(true);
    try {
      const r = await api.issueKubeconfig(cluster, ns, role, sa, ttl);
      const blob = new Blob([r.kubeconfig], { type: "application/yaml" });
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = `canggu-${r.namespace}-${r.role}.kubeconfig`;
      a.click();
      URL.revokeObjectURL(url);
      loadGrants();
    } catch (e) {
      alert("발급 실패: " + e);
    } finally {
      setBusy(false);
    }
  }

  return (
    <Panel title="네임스페이스 접근 발급 (kubeconfig)" wide>
      {isAdmin ? (
        <div className="access-form">
          <label>Namespace
            <select value={ns} onChange={(e) => setNs(e.target.value)}>
              {namespaces.map((n) => <option key={n} value={n}>{n}</option>)}
            </select>
          </label>
          <label>Role
            <select value={role} onChange={(e) => setRole(e.target.value)}>
              <option value="view">view (읽기)</option>
              <option value="edit">edit</option>
              <option value="admin">admin (NS 관리자)</option>
            </select>
          </label>
          <label>SA 이름
            <input value={sa} placeholder={`canggu-${role}`} onChange={(e) => setSa(e.target.value)} />
          </label>
          <label>TTL(초)
            <input type="number" value={ttl} min={600} max={86400}
              onChange={(e) => setTtl(Number(e.target.value))} />
          </label>
          <button className="primary" disabled={busy} onClick={issue}>
            {busy ? "발급 중…" : "발급 & 다운로드"}
          </button>
        </div>
      ) : (
        <div className="stat">발급은 admin 권한이 필요합니다. 아래는 발급 이력입니다.</div>
      )}
      <table>
        <thead>
          <tr><th>시각</th><th>NS</th><th>Role</th><th>SA</th><th>TTL</th><th>발급자</th></tr>
        </thead>
        <tbody>
          {grants.map((g, i) => (
            <tr key={i}>
              <td className="mono">{g.issued_at?.slice(0, 19).replace("T", " ")}</td>
              <td>{g.namespace}</td><td>{g.role}</td><td>{g.sa_name}</td>
              <td className="mono">{g.ttl_seconds}s</td><td>{g.issued_by}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </Panel>
  );
}

function RulesPanel({ isAdmin }: { isAdmin: boolean }) {
  const [rules, setRules] = useState<Awaited<ReturnType<typeof api.rules>>>([]);
  const load = useCallback(() => { api.rules().then(setRules).catch(() => {}); }, []);
  useEffect(() => { load(); }, [load]);

  async function patch(id: string, body: { enabled?: boolean; auto_apply?: string }) {
    try { await api.patchRule(id, body); load(); } catch (e) { alert("변경 실패: " + e); }
  }

  return (
    <Panel title="자동조정 규칙 설정" wide>
      <table>
        <thead>
          <tr><th>규칙</th><th>Risk</th><th>활성</th><th>자동 적용(auto-apply)</th></tr>
        </thead>
        <tbody>
          {rules.map((r) => (
            <tr key={r.id} className={!r.enabled ? "dim-row" : ""}>
              <td>{r.title} <span className="mono" style={{ color: "var(--muted)" }}>({r.id})</span></td>
              <td>{r.risk}</td>
              <td>
                <input type="checkbox" checked={r.enabled} disabled={!isAdmin}
                  onChange={(e) => patch(r.id, { enabled: e.target.checked })} />
              </td>
              <td>
                <select value={r.auto_apply} disabled={!isAdmin}
                  onChange={(e) => patch(r.id, { auto_apply: e.target.value })}>
                  <option value="default">기본({r.default_auto ? "자동" : "수동"})</option>
                  <option value="on">항상 자동</option>
                  <option value="off">항상 수동</option>
                </select>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      <div className="stat">※ 활성 규칙만 이슈를 생성하고, auto-apply + ACTIVE 모드일 때만 실제 변경됩니다.</div>
    </Panel>
  );
}

function UsersPanel({ isAdmin, clusterId }: { isAdmin: boolean; clusterId: string }) {
  const [users, setUsers] = useState<Me[]>([]);
  const [u, setU] = useState("");
  const [pw, setPw] = useState("");
  const [role, setRole] = useState("viewer");
  const [scopeType, setScopeType] = useState("cluster");
  const [ns, setNs] = useState("");
  const [busy, setBusy] = useState(false);
  const load = useCallback(() => { api.listUsers().then(setUsers).catch(() => {}); }, []);
  useEffect(() => { if (isAdmin) load(); }, [isAdmin, load]);
  if (!isAdmin) return null;

  async function create() {
    if (!u || !pw) { alert("아이디/비밀번호 입력"); return; }
    const scope_ref = scopeType === "namespace" ? `${clusterId}/${ns}` : clusterId;
    setBusy(true);
    try {
      await api.createUser({ username: u, password: pw, role, scope_type: scopeType, scope_ref });
      setU(""); setPw(""); setNs(""); load();
    } catch (e) { alert("생성 실패: " + e); } finally { setBusy(false); }
  }

  return (
    <Panel title={`웹 사용자 관리 (${users.length})`} wide>
      <div className="access-form">
        <label>아이디<input value={u} onChange={(e) => setU(e.target.value)} /></label>
        <label>비밀번호<input type="password" value={pw} onChange={(e) => setPw(e.target.value)} /></label>
        <label>Role
          <select value={role} onChange={(e) => setRole(e.target.value)}>
            <option value="viewer">viewer</option><option value="admin">admin</option>
          </select>
        </label>
        <label>Scope
          <select value={scopeType} onChange={(e) => setScopeType(e.target.value)}>
            <option value="cluster">이 클러스터 전체</option>
            <option value="namespace">특정 네임스페이스</option>
          </select>
        </label>
        {scopeType === "namespace" && (
          <label>Namespace<input value={ns} placeholder="예: team-a" onChange={(e) => setNs(e.target.value)} /></label>
        )}
        <button className="primary" disabled={busy} onClick={create}>사용자 생성</button>
      </div>
      <table>
        <thead><tr><th>아이디</th><th>Role</th><th>Scope</th><th></th></tr></thead>
        <tbody>
          {users.map((x) => (
            <tr key={x.username}>
              <td>{x.username}</td><td>{x.role}</td>
              <td className="mono">{x.scope_type}{x.scope_ref ? `:${x.scope_ref}` : " (전체)"}</td>
              <td>
                <button onClick={async () => {
                  const npw = prompt(`'${x.username}' 새 비밀번호`);
                  if (!npw) return;
                  try { await api.updateUser(x.username, { password: npw }); alert("변경됨"); }
                  catch (e) { alert("실패: " + e); }
                }}>비번재설정</button>
                <button onClick={async () => {
                  if (!confirm(`'${x.username}' 삭제?`)) return;
                  try { await api.deleteUser(x.username); load(); }
                  catch (e) { alert("삭제 실패: " + e); }
                }}>삭제</button>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </Panel>
  );
}

function VeleroPanel(
  { cluster, namespaces, isAdmin }: { cluster: string; namespaces: string[]; isAdmin: boolean },
) {
  const [state, setState] = useState<Awaited<ReturnType<typeof api.velero>> | null>(null);
  const [ns, setNs] = useState("");
  const [busy, setBusy] = useState(false);

  const load = useCallback(() => { api.velero(cluster).then(setState).catch(() => setState(null)); }, [cluster]);
  useEffect(() => { load(); const t = setInterval(load, 8000); return () => clearInterval(t); }, [load]);
  useEffect(() => { if (!ns && namespaces.length) setNs(namespaces[0]); }, [namespaces, ns]);

  if (!state) return null;
  if (!state.installed) {
    return <Panel title="백업/복구 (Velero)" wide><div className="empty">이 클러스터에 Velero 가 설치되어 있지 않습니다.</div></Panel>;
  }

  async function backup() {
    setBusy(true);
    try { await api.veleroBackup(cluster, ns); load(); }
    catch (e) { alert("백업 실패: " + e); } finally { setBusy(false); }
  }
  async function restore(name: string) {
    if (!window.confirm(`백업 '${name}' 에서 복구하시겠습니까? 리소스가 덮어써질 수 있습니다.`)) return;
    setBusy(true);
    try { await api.veleroRestore(cluster, name); load(); }
    catch (e) { alert("복구 실패: " + e); } finally { setBusy(false); }
  }

  const phaseIcon = (p: string) =>
    p === "Completed" ? "🟢" : p === "Failed" || p === "PartiallyFailed" ? "🔴" : "🟡";

  return (
    <Panel title={`백업/복구 (Velero) · 백업 ${state.backups.length} / 복구 ${state.restores.length}`} wide>
      {isAdmin && (
        <div className="access-form">
          <label>Namespace
            <select value={ns} onChange={(e) => setNs(e.target.value)}>
              {namespaces.map((n) => <option key={n} value={n}>{n}</option>)}
            </select>
          </label>
          <button className="primary" disabled={busy} onClick={backup}>이 NS 백업 생성</button>
        </div>
      )}
      <table>
        <thead>
          <tr><th>상태</th><th>백업</th><th>대상 NS</th><th>생성</th><th>오류/경고</th>{isAdmin && <th></th>}</tr>
        </thead>
        <tbody>
          {state.backups.map((b) => (
            <tr key={b.name}>
              <td>{phaseIcon(b.phase)} {b.phase}</td>
              <td className="mono">{b.name}</td>
              <td>{b.included_namespaces.join(", ") || "(전체)"}</td>
              <td className="mono">{b.created?.slice(0, 19).replace("T", " ")}</td>
              <td>{b.errors > 0 ? `❌${b.errors}` : "0"}/{b.warnings}</td>
              {isAdmin && <td><button disabled={busy || b.phase !== "Completed"}
                onClick={() => restore(b.name)}>복구</button></td>}
            </tr>
          ))}
        </tbody>
      </table>
      {state.restores.length > 0 && (
        <>
          <h3 style={{ fontSize: 13, color: "var(--muted)", margin: "12px 0 4px" }}>복구 이력</h3>
          <table>
            <thead><tr><th>상태</th><th>복구</th><th>원본 백업</th><th>생성</th></tr></thead>
            <tbody>
              {state.restores.map((r) => (
                <tr key={r.name}>
                  <td>{phaseIcon(r.phase)} {r.phase}</td>
                  <td className="mono">{r.name}</td><td className="mono">{r.backup_name}</td>
                  <td className="mono">{r.created?.slice(0, 19).replace("T", " ")}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </>
      )}
    </Panel>
  );
}

function Panel(props: { title: string; wide?: boolean; children: React.ReactNode }) {
  return (
    <section className={`panel ${props.wide ? "wide" : ""}`}>
      <h2>{props.title}</h2>
      <div className="panel-body">{props.children}</div>
    </section>
  );
}

function summarize(e: LiveEvent): string {
  const keys = ["cluster_id", "namespace", "target", "action", "phase", "mode", "frozen", "issues", "pods", "error"];
  return keys
    .filter((k) => e[k] !== undefined && e[k] !== "")
    .map((k) => `${k}=${e[k]}`)
    .join(" ");
}
