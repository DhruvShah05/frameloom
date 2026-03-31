import React, { useEffect, useMemo, useState } from 'react';
import { api, formatValue } from '../api';
import { useDialogs } from './ui';

const inputClass = "bg-white border border-grid-border rounded-DEFAULT px-2 h-[28px] text-[12px] outline-none focus:border-primary-container";

function MetricForm({ onAdd }) {
  const [key, setKey] = useState('');
  const [value, setValue] = useState('');
  const submit = e => {
    e.preventDefault();
    if (!key.trim()) return;
    const num = Number(value);
    onAdd(key.trim(), value.trim() !== '' && !Number.isNaN(num) ? num : value);
    setKey('');
    setValue('');
  };
  return (
    <form onSubmit={submit} className="flex gap-1 items-center">
      <input className={`${inputClass} w-[90px]`} placeholder="metric" value={key} onChange={e => setKey(e.target.value)} aria-label="Metric name" />
      <input className={`${inputClass} w-[70px]`} placeholder="value" value={value} onChange={e => setValue(e.target.value)} aria-label="Metric value" />
      <button className="toolbar-btn" type="submit" disabled={!key.trim()}>Add</button>
    </form>
  );
}

export default function VersionsView({ project, onChanged, onOpenNode, onNewVersion, notify }) {
  const [sortBy, setSortBy] = useState({ key: 'created_at', desc: false });
  const { confirm } = useDialogs();

  // Metrics usually arrive from Python (loom.log_metrics) while this is open:
  // refresh when the view opens and whenever the window regains focus.
  useEffect(() => {
    onChanged();
    window.addEventListener('focus', onChanged);
    return () => window.removeEventListener('focus', onChanged);
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);
  const [editingNotes, setEditingNotes] = useState(null); // { name, text }
  const versions = useMemo(() => Object.values(project.versions || {}), [project]);
  const metricKeys = useMemo(() => Array.from(new Set(versions.flatMap(v => Object.keys(v.metrics || {})))).sort(), [versions]);

  const sorted = useMemo(() => {
    const get = v => (sortBy.key.startsWith('metric:') ? v.metrics?.[sortBy.key.slice(7)] : v[sortBy.key]);
    return [...versions].sort((a, b) => {
      const x = get(a), y = get(b);
      if (x === y) return 0;
      if (x === undefined || x === null) return 1;
      if (y === undefined || y === null) return -1;
      return (x < y ? -1 : 1) * (sortBy.desc ? -1 : 1);
    });
  }, [versions, sortBy]);

  const patch = async (name, body) => {
    try {
      await api(`/project/${project.id}/versions/${encodeURIComponent(name)}`, { method: 'PATCH', body });
      onChanged();
    } catch (e) {
      notify(e.message);
    }
  };

  const remove = async name => {
    if (!(await confirm(`Delete version “${name}”?`, 'The steps stay; only the name, notes and metrics go.', { confirmLabel: 'Delete version', danger: true }))) return;
    try {
      await api(`/project/${project.id}/versions/${encodeURIComponent(name)}`, { method: 'DELETE' });
      onChanged();
    } catch (e) {
      notify(e.message);
    }
  };

  const snippet = name => `import loom\ndf = loom.load(${JSON.stringify(project.name || project.id)}, ${JSON.stringify(name)})  # pandas=True for pandas`;
  const header = (key, label) => (
    <th className="text-left px-3 py-2 border-b border-grid-border font-grid-header text-grid-header cursor-pointer select-none whitespace-nowrap"
      onClick={() => setSortBy(s => ({ key, desc: s.key === key ? !s.desc : key.startsWith('metric:') }))}>
      {label}{sortBy.key === key ? (sortBy.desc ? ' ▼' : ' ▲') : ''}
    </th>
  );

  return (
    <div className="flex-1 flex flex-col min-w-0 overflow-hidden">
      <div className="min-h-[48px] flex flex-wrap items-center gap-3 px-4 py-2 border-b border-grid-border bg-sidebar-bg">
        <span className="text-[13px] text-on-surface">
          <strong>{versions.length}</strong> version{versions.length === 1 ? '' : 's'} — named, fixed points in the history you can load from code and compare by model results.
        </span>
        <button className="toolbar-btn ml-auto" onClick={onNewVersion}><span className="material-symbols-outlined text-[16px]">bookmark_add</span> Save current step as a version</button>
      </div>
      <div className="flex-1 overflow-auto p-4 grid-scroll">
        {!versions.length ? (
          <div className="max-w-[560px] text-[13px] text-on-surface-variant flex flex-col gap-2">
            <p className="m-0">No versions yet. Save one when the data reaches a state you might train on, e.g. <code>baseline</code>, then load it from Python:</p>
            <pre className="code-block text-xs m-0">{snippet('baseline')}</pre>
            <p className="m-0">After training, record the results so you can see which data worked best:</p>
            <pre className="code-block text-xs m-0">{`loom.log_metrics(${JSON.stringify(project.name || project.id)}, "baseline", val_auc=0.83)`}</pre>
          </div>
        ) : (
          <table className="border-collapse text-[13px] bg-white border border-grid-border rounded-lg min-w-full">
            <thead className="bg-surface-container-low">
              <tr>
                {header('name', 'Version')}
                {header('message', 'Message')}
                {metricKeys.map(k => <React.Fragment key={k}>{header(`metric:${k}`, k)}</React.Fragment>)}
                {header('created_at', 'Saved')}
                <th className="text-left px-3 py-2 border-b border-grid-border font-grid-header text-grid-header">Notes</th>
                <th className="px-3 py-2 border-b border-grid-border"></th>
              </tr>
            </thead>
            <tbody>
              {sorted.map(v => (
                <tr key={v.name} className="align-top hover:bg-highlight-bg">
                  <td className="px-3 py-2 border-b border-grid-border whitespace-nowrap">
                    <button className="bg-transparent border-0 p-0 cursor-pointer font-semibold text-primary hover:underline" onClick={() => onOpenNode(v.node_id)} title="View this version's data">{v.name}</button>
                    <div className="text-[11px] text-on-surface-variant">{v.branch ? `on ${v.branch} · ` : ''}{project.nodes[v.node_id]?.operation.label}</div>
                  </td>
                  <td className="px-3 py-2 border-b border-grid-border min-w-[160px]">{v.message || <span className="text-outline">—</span>}</td>
                  {metricKeys.map(k => (
                    <td key={k} className="px-3 py-2 border-b border-grid-border text-right tabular-nums whitespace-nowrap">
                      {v.metrics?.[k] !== undefined ? formatValue(v.metrics[k]) : <span className="text-outline">—</span>}
                    </td>
                  ))}
                  <td className="px-3 py-2 border-b border-grid-border whitespace-nowrap text-on-surface-variant">{new Date(v.created_at).toLocaleString()}</td>
                  <td className="px-3 py-2 border-b border-grid-border min-w-[200px]">
                    {editingNotes?.name === v.name ? (
                      <form className="flex flex-col gap-1" onSubmit={e => { e.preventDefault(); patch(v.name, { notes: editingNotes.text }); setEditingNotes(null); }}>
                        <textarea autoFocus rows={3} className={`${inputClass} h-auto py-1`} value={editingNotes.text} onChange={e => setEditingNotes({ name: v.name, text: e.target.value })} />
                        <div className="flex gap-1"><button className="toolbar-btn" type="submit">Save</button><button className="toolbar-btn" type="button" onClick={() => setEditingNotes(null)}>Cancel</button></div>
                      </form>
                    ) : (
                      <button className="bg-transparent border-0 p-0 text-left cursor-pointer text-on-surface hover:text-primary whitespace-pre-wrap" onClick={() => setEditingNotes({ name: v.name, text: v.notes || '' })}>
                        {v.notes || <span className="text-outline">Add notes…</span>}
                      </button>
                    )}
                  </td>
                  <td className="px-3 py-2 border-b border-grid-border">
                    <div className="flex flex-col gap-1 items-end">
                      <MetricForm onAdd={(key, value) => patch(v.name, { metrics: { [key]: value } })} />
                      <div className="flex gap-1">
                        <button className="toolbar-btn" onClick={() => navigator.clipboard.writeText(snippet(v.name)).then(() => notify('Python snippet copied', 'info'), () => notify(snippet(v.name), 'info'))}>
                          <span className="material-symbols-outlined text-[14px]">content_copy</span> Python
                        </button>
                        <button className="toolbar-btn" onClick={() => remove(v.name)} aria-label={`Delete version ${v.name}`}>
                          <span className="material-symbols-outlined text-[14px]">delete</span>
                        </button>
                      </div>
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </div>
  );
}
