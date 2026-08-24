import { useEffect, useMemo, useRef, useState } from "react";
import {
  Check,
  ChevronRight,
  CircleAlert,
  Download,
  Eye,
  FilePlus2,
  Gauge,
  MousePointer2,
  Play,
  Plus,
  Redo2,
  RotateCcw,
  Save,
  Settings2,
  Trash2,
  Undo2,
  X,
} from "lucide-react";
import {
  ApiResult,
  closeWorkspace,
  command,
  commit,
  getBootstrap,
  getSignals,
  openWorkspace,
  publishSchema,
  recognize,
  registerTrial,
  setSessionToken,
  updateSchema,
} from "./api";

type SignalData = ApiResult;
type Workspace = ApiResult;

const tracks = [
  { value: "activity", label: "活动" },
  { value: "terrain", label: "地形" },
];

function App() {
  const [bootstrap, setBootstrap] = useState<ApiResult | null>(null);
  const [workspace, setWorkspace] = useState<Workspace | null>(null);
  const [signals, setSignals] = useState<SignalData | null>(null);
  const [selectedEventId, setSelectedEventId] = useState<string | null>(null);
  const [selectedSample, setSelectedSample] = useState(0);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [schemaDraft, setSchemaDraft] = useState<ApiResult | null>(null);
  const [schemaHash, setSchemaHash] = useState("");
  const [registerForm, setRegisterForm] = useState({
    source_path: "",
    subject_id: "",
    session_id: "",
    trial_id: "",
  });
  const [newLabel, setNewLabel] = useState({ track: "activity", code: "", display_name: "" });
  const [newState, setNewState] = useState({ activity: "", terrain: "" });
  const [annotatorId, setAnnotatorId] = useState("annotator");
  const [qaReason, setQaReason] = useState("");

  useEffect(() => {
    getBootstrap()
      .then((value) => {
        setBootstrap(value);
        setSessionToken(value.session_token);
        setSchemaDraft(value.schema_draft);
        setSchemaHash(value.schema_draft.content_hash);
      })
      .catch((reason) => setError(reason.message));
  }, []);

  const run = async (action: () => Promise<ApiResult>, onSuccess?: (value: ApiResult) => void) => {
    setBusy(true);
    setError("");
    try {
      const value = await action();
      onSuccess?.(value);
    } catch (reason: any) {
      setError(reason.message || "请求失败");
    } finally {
      setBusy(false);
    }
  };

  const loadSignals = (id: string) =>
    getSignals(id).then(setSignals).catch((reason) => setError(reason.message));

  const openTrial = (recordId: string) => {
    run(
      () => openWorkspace(recordId),
      (value) => {
        setWorkspace(value);
        setSelectedEventId(value.document.events[0]?.id || null);
        setSelectedSample(0);
        loadSignals(value.workspace_id);
      },
    );
  };

  const runCommand = (action: string, payload: ApiResult = {}) => {
    if (!workspace) return;
    run(() => command(workspace.workspace_id, action, payload), setWorkspace);
  };

  const selectedEvent = useMemo(
    () => workspace?.document.events.find((item: ApiResult) => item.id === selectedEventId) || null,
    [workspace, selectedEventId],
  );

  const selectedBoundary = useMemo(() => {
    if (!workspace || !selectedEvent) return null;
    return workspace.document.boundaries.find((item: ApiResult) =>
      selectedEvent.component_ids.includes(item.id),
    );
  }, [workspace, selectedEvent]);

  const activeLabels = (track: string) =>
    (workspace?.schema || schemaDraft)?.[track + "_labels"]?.filter((item: ApiResult) => item.active) || [];

  const pickSample = (index: number) => {
    setSelectedSample(index);
    if (workspace && signals) {
      const seconds = signals.seconds[signals.indices.indexOf(index)] ?? workspace.trial.duration;
      const nearest = workspace.document.events.reduce((best: ApiResult, event: ApiResult) =>
        Math.abs(event.sample_index - index) < Math.abs(best.sample_index - index) ? event : best,
      );
      if (nearest && Math.abs(nearest.seconds - seconds) < 0.25) setSelectedEventId(nearest.id);
    }
  };

  const handleRegister = (event: React.FormEvent) => {
    event.preventDefault();
    run(
      () => registerTrial(registerForm),
      (record) => {
        setBootstrap((current) => ({ ...current, trials: [...(current?.trials || []), record] }));
        setRegisterForm({ source_path: "", subject_id: "", session_id: "", trial_id: "" });
      },
    );
  };

  const updateLabel = (track: string, index: number, field: string, value: any) => {
    setSchemaDraft((current: ApiResult | null) => {
      if (!current) return current;
      const copy = structuredClone(current);
      copy[track + "_labels"][index][field] = value;
      return copy;
    });
  };

  const addLabel = () => {
    if (!schemaDraft || !newLabel.code.trim() || !newLabel.display_name.trim()) return;
    const key = newLabel.track + "_labels";
    const copy = structuredClone(schemaDraft);
    copy[key].push({
      code: newLabel.code.trim(),
      display_name: newLabel.display_name.trim(),
      color: "#607D8B",
      description: "",
      active: true,
    });
    setSchemaDraft(copy);
    setNewLabel({ ...newLabel, code: "", display_name: "" });
  };

  const addState = () => {
    if (!schemaDraft || !newState.activity || !newState.terrain) return;
    const copy = structuredClone(schemaDraft);
    if (!copy.states.some((item: ApiResult) => item.activity === newState.activity && item.terrain === newState.terrain)) {
      copy.states.push({ activity: newState.activity, terrain: newState.terrain, color: "#607D8B" });
      setSchemaDraft(copy);
    }
  };

  const saveSchema = () => {
    if (!schemaDraft) return;
    run(
      () => updateSchema(schemaDraft, schemaHash),
      (value) => {
        setSchemaDraft(value);
        setSchemaHash(value.content_hash);
      },
    );
  };

  const publish = () =>
    run(
      () => publishSchema(schemaHash),
      (value) => {
        setSchemaDraft({ ...value, content_hash: value.content_hash });
        setSchemaHash(value.content_hash);
        getBootstrap().then(setBootstrap).catch(() => undefined);
      },
    );

  if (!bootstrap) {
    return <div className="loading">{error || "正在打开项目…"}</div>;
  }

  return (
    <div className="app-shell">
      <header className="topbar">
        <div className="brand-mark"><Gauge size={18} />步态数据标注</div>
        <div className="project-meta">
          <span>{bootstrap.project.name}</span>
          <span className="muted">{bootstrap.trials.length} 个试次</span>
        </div>
        <div className="top-actions">
          {workspace && <span className={workspace.document.ready_to_save ? "status ready" : "status draft"}>{workspace.document.ready_to_save ? "可正式保存" : "草稿"}</span>}
          {busy && <span className="busy">处理中…</span>}
        </div>
      </header>

      {error && <div className="error-banner"><CircleAlert size={16} />{error}<button className="icon-button" onClick={() => setError("")}><X size={15} /></button></div>}

      <div className="workspace-layout">
        <aside className="sidebar">
          <section className="panel trial-panel">
            <div className="panel-heading"><span>试次</span><span className="count">{bootstrap.trials.length}</span></div>
            <div className="trial-list">
              {bootstrap.trials.map((trial: ApiResult) => (
                <button key={trial.id} className={"trial-row " + (workspace?.trial_record.id === trial.id ? "selected" : "")} onClick={() => openTrial(trial.id)}>
                  <span><strong>{trial.subject_id} / {trial.session_id}</strong><small>{trial.trial_id} · {trial.input_file}</small></span>
                  <ChevronRight size={15} />
                </button>
              ))}
              {!bootstrap.trials.length && <div className="empty-note">还没有登记试次</div>}
            </div>
          </section>

          <form className="panel register-panel" onSubmit={handleRegister}>
            <div className="panel-heading"><span>登记原始试次</span><FilePlus2 size={16} /></div>
            <label>CSV 路径<input required value={registerForm.source_path} onChange={(e) => setRegisterForm({ ...registerForm, source_path: e.target.value })} placeholder="/data/P01_T01.csv" /></label>
            <div className="two-fields">
              <label>受试者<input required value={registerForm.subject_id} onChange={(e) => setRegisterForm({ ...registerForm, subject_id: e.target.value })} /></label>
              <label>会话<input required value={registerForm.session_id} onChange={(e) => setRegisterForm({ ...registerForm, session_id: e.target.value })} /></label>
            </div>
            <label>试次<input required value={registerForm.trial_id} onChange={(e) => setRegisterForm({ ...registerForm, trial_id: e.target.value })} /></label>
            <button className="primary-button" type="submit" disabled={busy}><Plus size={15} />登记试次</button>
          </form>
        </aside>

        <main className="main-area">
          {!workspace ? (
            <div className="empty-workspace">
              <MousePointer2 size={28} />
              <h1>选择一个试次开始</h1>
              <p>左侧登记或打开原始传感器 CSV。</p>
            </div>
          ) : (
            <>
              <div className="session-heading">
                <div><span className="eyebrow">当前试次</span><h1>{workspace.trial_record.subject_id} / {workspace.trial_record.session_id} / {workspace.trial_record.trial_id}</h1><p>{workspace.trial_record.input_file} · {workspace.trial.sample_count.toLocaleString()} 点 · {workspace.trial.duration.toFixed(2)} s</p></div>
                <div className="session-actions">
                  <button className="icon-button" title="重新运行 V2 识别" onClick={() => run(() => recognize(workspace.workspace_id), setWorkspace)}><Play size={16} />识别</button>
                  <button className="icon-button" title="撤销" disabled={!workspace.document.can_undo} onClick={() => runCommand("undo")}><Undo2 size={16} /></button>
                  <button className="icon-button" title="重做" disabled={!workspace.document.can_redo} onClick={() => runCommand("redo")}><Redo2 size={16} /></button>
                  <button className="primary-button" onClick={() => run(() => commit(workspace.workspace_id), setWorkspace)} disabled={busy || !workspace.document.ready_to_save}><Save size={16} />提交修订</button>
                </div>
              </div>

              <SignalCanvas workspace={workspace} signals={signals} selectedSample={selectedSample} onPick={pickSample} />

              <div className="editor-grid">
                <section className="panel event-panel">
                  <div className="panel-heading"><span>标注事件</span><span className="count">{workspace.document.events.length}</span></div>
                  <div className="event-table-wrap">
                    <table><thead><tr><th>时间</th><th>活动</th><th>地形</th><th>来源</th></tr></thead>
                      <tbody>
                        {workspace.document.events.map((event: ApiResult) => (
                          <tr key={event.id} className={event.id === selectedEventId ? "active-row" : ""} onClick={() => { setSelectedEventId(event.id); setSelectedSample(event.sample_index); }}>
                            <td>{event.seconds.toFixed(2)}s</td><td>{labelName(workspace.schema, "activity", event.activity)}</td><td>{labelName(workspace.schema, "terrain", event.terrain)}</td><td><span className="provenance">{event.provenance}</span></td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                </section>

                <section className="panel edit-panel">
                  <div className="panel-heading"><span>编辑</span><span className="sample-readout">采样点 {selectedSample}</span></div>
                  <div className="control-block">
                    <label>定位到采样点<input type="number" min="0" max={workspace.trial.sample_count - 1} value={selectedSample} onChange={(e) => setSelectedSample(Number(e.target.value))} /></label>
                    <button className="secondary-button" onClick={() => selectedEvent && runCommand("move_event", { event_id: selectedEvent.id, sample_index: selectedSample })} disabled={!selectedEvent}><MousePointer2 size={15} />移动选中事件</button>
                  </div>
                  {selectedBoundary && <div className="control-block">
                    <label>标签<select value={selectedBoundary.value} onChange={(e) => runCommand("set_boundary_value", { boundary_id: selectedBoundary.id, value: e.target.value })}>
                      {activeLabels(selectedBoundary.track).map((item: ApiResult) => <option key={item.code} value={item.code}>{item.display_name}</option>)}
                    </select></label>
                    <label>备注<input defaultValue={selectedBoundary.user_note} onKeyDown={(e) => { if (e.key === "Enter") runCommand("set_note", { component_id: selectedBoundary.id, note: (e.target as HTMLInputElement).value }); }} /></label>
                    <button className="danger-button" onClick={() => runCommand("delete_component", { component_id: selectedBoundary.id, reason: qaReason })}><Trash2 size={15} />删除边界</button>
                  </div>}
                  <div className="control-block">
                    <div className="two-fields"><label>轨<select value={newLabel.track} onChange={(e) => setNewLabel({ ...newLabel, track: e.target.value })}>{tracks.map((track) => <option key={track.value} value={track.value}>{track.label}</option>)}</select></label><label>新标签<select value={newLabel.code} onChange={(e) => setNewLabel({ ...newLabel, code: e.target.value })}><option value="">选择标签</option>{activeLabels(newLabel.track).map((item: ApiResult) => <option key={item.code} value={item.code}>{item.display_name}</option>)}</select></label></div>
                    <button className="secondary-button" onClick={() => newLabel.code && runCommand("add_boundary", { track: newLabel.track, sample_index: selectedSample, value: newLabel.code })}><Plus size={15} />在光标处加边界</button>
                  </div>
                  <div className="control-block">
                    <div className="two-fields"><label>区间起点<input type="number" value={selectedSample} onChange={(e) => setSelectedSample(Number(e.target.value))} /></label><label>区间终点<input type="number" value={Math.min(selectedSample + 2, workspace.trial.sample_count - 1)} readOnly /></label></div>
                    <button className="secondary-button" onClick={() => runCommand("add_interval", { track: newLabel.track, start_index: selectedSample, end_index: Math.min(selectedSample + 2, workspace.trial.sample_count - 1), value: newLabel.code })} disabled={!newLabel.code}><Plus size={15} />添加区间</button>
                  </div>
                </section>
              </div>

              <section className="panel qa-panel">
                <div className="panel-heading"><span>QA 与复核</span><span className="count">{workspace.document.issues.filter((item: ApiResult) => !item.resolved).length} 未闭环</span></div>
                {workspace.document.structural_errors.map((item: string) => <div className="issue-row blocking" key={item}><CircleAlert size={15} />{item}</div>)}
                {workspace.document.issues.map((issue: ApiResult) => <div className="issue-row" key={issue.id}><span><strong>{issue.code}</strong> {issue.message}</span>{!issue.resolved && <button className="secondary-button" onClick={() => runCommand("resolve_issue", { issue_id: issue.id, resolution: "accepted", reason: qaReason || "已在信号视图中核对" })}><Check size={15} />接受</button>}</div>)}
                <div className="qa-actions"><input placeholder="QA 接受理由 / 删除自动项理由" value={qaReason} onChange={(e) => setQaReason(e.target.value)} /><input value={annotatorId} onChange={(e) => setAnnotatorId(e.target.value)} placeholder="标注员 ID" /><button className="secondary-button" onClick={() => runCommand("attest", { annotator_id: annotatorId })}><Check size={15} />声明已复核</button></div>
              </section>
            </>
          )}
        </main>

        <aside className="schema-sidebar">
          <section className="panel schema-panel">
            <div className="panel-heading"><span>标签模式草稿</span><Settings2 size={16} /></div>
            {schemaDraft && <SchemaEditor schema={schemaDraft} updateLabel={updateLabel} newLabel={newLabel} setNewLabel={setNewLabel} addLabel={addLabel} newState={newState} setNewState={setNewState} addState={addState} save={saveSchema} publish={publish} busy={busy} />}
          </section>
          {workspace && <section className="panel history-panel"><div className="panel-heading"><span>修订历史</span><RotateCcw size={15} /></div>{workspace.revisions.length ? workspace.revisions.map((revision: ApiResult) => <div className="history-row" key={revision.id}><strong>{revision.id.slice(0, 8)}</strong><span>v{revision.schema_version} · {revision.annotator_id}</span></div>) : <div className="empty-note">尚无正式修订</div>}</section>}
        </aside>
      </div>
    </div>
  );
}

function labelName(schema: ApiResult, track: string, code: string) {
  return schema?.[track + "_labels"]?.find((item: ApiResult) => item.code === code)?.display_name || code;
}

function SignalCanvas({ workspace, signals, selectedSample, onPick }: { workspace: ApiResult; signals: SignalData | null; selectedSample: number; onPick: (index: number) => void }) {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas || !signals) return;
    const ratio = window.devicePixelRatio || 1;
    const width = canvas.clientWidth;
    const height = canvas.clientHeight;
    canvas.width = width * ratio;
    canvas.height = height * ratio;
    const ctx = canvas.getContext("2d");
    if (!ctx) return;
    ctx.scale(ratio, ratio);
    ctx.clearRect(0, 0, width, height);
    ctx.fillStyle = "#f8faf8";
    ctx.fillRect(0, 0, width, height);
    const seconds = signals.seconds;
    const maxSecond = seconds[seconds.length - 1] || 1;
    const x = (second: number) => (second / maxSecond) * width;
    const y = (value: number) => height / 2 - Math.max(-1, Math.min(1, value / 12)) * (height * 0.37);
    ctx.strokeStyle = "#d8e2de";
    ctx.lineWidth = 1;
    for (let line = 1; line < 5; line += 1) { ctx.beginPath(); ctx.moveTo(0, (height * line) / 5); ctx.lineTo(width, (height * line) / 5); ctx.stroke(); }
    workspace.document.intervals.forEach((interval: ApiResult) => {
      ctx.fillStyle = (interval.color || "#607D8B") + "25";
      ctx.fillRect(x(interval.start), 0, Math.max(1, x(interval.end) - x(interval.start)), height);
    });
    const draw = (key: string, color: string) => {
      ctx.strokeStyle = color;
      ctx.lineWidth = 1.4;
      ctx.beginPath();
      signals.channels[key].forEach((value: number, index: number) => {
        const px = x(seconds[index]);
        const py = y(value);
        if (index === 0) ctx.moveTo(px, py); else ctx.lineTo(px, py);
      });
      ctx.stroke();
    };
    draw("left", "#D84315");
    draw("right", "#1565C0");
    ctx.strokeStyle = "#9a5b46";
    ctx.setLineDash([4, 4]);
    workspace.document.events.forEach((event: ApiResult) => { ctx.beginPath(); ctx.moveTo(x(event.seconds), 0); ctx.lineTo(x(event.seconds), height); ctx.stroke(); });
    ctx.setLineDash([]);
    const selectedSecond = workspace.trial.duration * (selectedSample / Math.max(1, workspace.trial.sample_count - 1));
    ctx.strokeStyle = "#111c19";
    ctx.lineWidth = 2;
    ctx.beginPath(); ctx.moveTo(x(selectedSecond), 0); ctx.lineTo(x(selectedSecond), height); ctx.stroke();
  }, [workspace, signals, selectedSample]);

  const click = (event: React.MouseEvent<HTMLCanvasElement>) => {
    if (!signals) return;
    const rect = event.currentTarget.getBoundingClientRect();
    const second = ((event.clientX - rect.left) / rect.width) * (signals.seconds[signals.seconds.length - 1] || 1);
    let best = 0;
    signals.seconds.forEach((value: number, index: number) => { if (Math.abs(value - second) < Math.abs(signals.seconds[best] - second)) best = index; });
    onPick(signals.indices[best]);
  };

  return <section className="signal-panel panel"><div className="panel-heading"><span>信号与标注</span><span className="legend"><i className="left-dot" />左腿 <i className="right-dot" />右腿 <i className="cursor-dot" />光标</span></div>{signals ? <canvas ref={canvasRef} onClick={click} className="signal-canvas" /> : <div className="signal-loading">正在读取信号…</div>}<div className="axis-labels"><span>0 s</span><span>{workspace.trial.duration.toFixed(2)} s</span></div></section>;
}

function SchemaEditor({ schema, updateLabel, newLabel, setNewLabel, addLabel, newState, setNewState, addState, save, publish, busy }: any) {
  return <div className="schema-editor">
    <div className="schema-version">schema {schema.schema_id.slice(0, 12)} · 草稿 v{schema.version}</div>
    {tracks.map((track) => <div className="schema-track" key={track.value}><h3>{track.label}轨</h3>{schema[track.value + "_labels"].map((label: ApiResult, index: number) => <div className="label-row" key={label.code}><input className="swatch-input" type="color" value={label.color} onChange={(e) => updateLabel(track.value, index, "color", e.target.value)} /><input value={label.display_name} onChange={(e) => updateLabel(track.value, index, "display_name", e.target.value)} /><code>{label.code}</code><label className="switch"><input type="checkbox" checked={label.active} onChange={(e) => updateLabel(track.value, index, "active", e.target.checked)} /><span /></label></div>)}</div>)}
    <div className="schema-add"><select value={newLabel.track} onChange={(e) => setNewLabel({ ...newLabel, track: e.target.value })}>{tracks.map((track) => <option key={track.value} value={track.value}>{track.label}</option>)}</select><input placeholder="稳定 code" value={newLabel.code} onChange={(e) => setNewLabel({ ...newLabel, code: e.target.value })} /><input placeholder="显示名" value={newLabel.display_name} onChange={(e) => setNewLabel({ ...newLabel, display_name: e.target.value })} /><button className="icon-button" title="添加标签" onClick={addLabel}><Plus size={15} /></button></div>
    <div className="combo-editor"><h3>合法组合</h3>{schema.states.map((state: ApiResult) => <div className="combo-row" key={state.activity + state.terrain}><span>{labelName(schema, "activity", state.activity)} · {labelName(schema, "terrain", state.terrain)}</span><button className="icon-button" title="停用组合暂未提供，保留历史可解释性" disabled><Trash2 size={13} /></button></div>)}<div className="schema-add"><select value={newState.activity} onChange={(e) => setNewState({ ...newState, activity: e.target.value })}><option value="">活动</option>{schema.activity_labels.filter((item: ApiResult) => item.active).map((item: ApiResult) => <option key={item.code} value={item.code}>{item.display_name}</option>)}</select><select value={newState.terrain} onChange={(e) => setNewState({ ...newState, terrain: e.target.value })}><option value="">地形</option>{schema.terrain_labels.filter((item: ApiResult) => item.active).map((item: ApiResult) => <option key={item.code} value={item.code}>{item.display_name}</option>)}</select><button className="icon-button" title="添加合法组合" onClick={addState}><Plus size={15} /></button></div></div>
    <div className="schema-actions"><button className="secondary-button" onClick={save} disabled={busy}><Save size={14} />保存草稿</button><button className="primary-button" onClick={publish} disabled={busy}><Check size={14} />发布新版本</button></div>
  </div>;
}

export default App;
