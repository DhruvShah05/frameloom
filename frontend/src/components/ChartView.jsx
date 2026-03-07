import React, { useEffect, useMemo, useState } from 'react';
import Chart from './Chart';
import { api, formatValue, isNumericType, isTemporalType } from '../api';
import { AGG_OPTIONS } from '../opsConfig';

const selectClass = "bg-white border border-grid-border rounded-DEFAULT px-2 h-[30px] text-[13px] outline-none focus:border-primary-container";

export default function ChartView({ projectId, nodeId, schema, onError }) {
  const columns = useMemo(() => Object.keys(schema || {}), [schema]);
  const numeric = columns.filter(c => isNumericType(schema[c]));
  const [spec, setSpec] = useState({ type: 'histogram', x: '', y: '', agg: 'sum', bins: 30 });
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(false);
  const [showTable, setShowTable] = useState(false);

  // Pick sensible defaults when the columns change.
  useEffect(() => {
    setSpec(s => {
      if (s.x && columns.includes(s.x)) return s;
      return { ...s, x: numeric[0] || columns[0] || '', y: numeric[1] || numeric[0] || '' };
    });
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [columns.join('|')]);

  const set = patch => setSpec(s => ({ ...s, ...patch }));
  const needsY = spec.type === 'line' || spec.type === 'scatter' || (spec.type === 'bar' && spec.agg !== 'count');
  const xChoices = spec.type === 'histogram' || spec.type === 'scatter'
    ? columns.filter(c => isNumericType(schema[c]) || (spec.type === 'histogram' && isTemporalType(schema[c])))
    : columns;

  useEffect(() => {
    if (!projectId || !nodeId || !spec.x || !columns.includes(spec.x)) return undefined;
    if (needsY && (!spec.y || !columns.includes(spec.y))) return undefined;
    let cancelled = false;
    setLoading(true);
    const body = { node_id: nodeId, type: spec.type, x: spec.x, bins: spec.bins };
    if (spec.type !== 'histogram') {
      body.agg = spec.type === 'scatter' ? 'none' : spec.agg;
      if (needsY) body.y = spec.y;
    }
    api(`/project/${projectId}/chart`, { method: 'POST', body })
      .then(d => { if (!cancelled) setData(d); })
      .catch(e => { if (!cancelled) { setData(null); onError?.(e.message); } })
      .finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [projectId, nodeId, spec.type, spec.x, spec.y, spec.agg, spec.bins, columns.join('|')]);

  const title = !data ? '' : data.type === 'histogram' ? `Distribution of ${data.x}`
    : data.type === 'bar' ? (data.agg === 'count' && !data.y ? `Rows per ${data.x}` : `${data.agg} of ${data.y} by ${data.x}`)
    : data.type === 'line' ? `${data.agg && data.agg !== 'none' ? `${data.agg} of ` : ''}${data.y} over ${data.x}` : `${data.y} vs ${data.x}`;

  const tableRows = !data ? [] : data.type === 'histogram'
    ? data.bins.map(b => [`${formatValue(b.x0, data.x_type === 'temporal' ? 'Datetime' : '')} – ${formatValue(b.x1, data.x_type === 'temporal' ? 'Datetime' : '')}`, b.count])
    : data.points.map(p => [formatValue(p.x, data.x_type === 'temporal' ? 'Datetime' : ''), p.y]);

  return (
    <div className="flex-1 flex flex-col min-w-0 overflow-hidden">
      <div className="min-h-[48px] flex flex-wrap items-center gap-3 px-4 py-2 border-b border-grid-border bg-sidebar-bg">
        <label className="flex items-center gap-2 text-[12px] text-on-surface-variant">Chart
          <select className={selectClass} value={spec.type} onChange={e => set({ type: e.target.value })}>
            <option value="histogram">Histogram</option>
            <option value="bar">Bar</option>
            <option value="line">Line</option>
            <option value="scatter">Scatter</option>
          </select>
        </label>
        <label className="flex items-center gap-2 text-[12px] text-on-surface-variant">{spec.type === 'bar' ? 'Category' : 'X'}
          <select className={selectClass} value={spec.x} onChange={e => set({ x: e.target.value })}>
            <option value="">Select…</option>
            {xChoices.map(c => <option key={c} value={c}>{c}</option>)}
          </select>
        </label>
        {(spec.type === 'bar' || spec.type === 'line') && (
          <label className="flex items-center gap-2 text-[12px] text-on-surface-variant">Value
            <select className={selectClass} value={spec.agg} onChange={e => set({ agg: e.target.value })}>
              {spec.type === 'line' && <option value="none">Raw values</option>}
              {AGG_OPTIONS.map(([v, l]) => <option key={v} value={v}>{v === 'count' ? 'Row count' : l}</option>)}
            </select>
          </label>
        )}
        {needsY && (
          <label className="flex items-center gap-2 text-[12px] text-on-surface-variant">{spec.type === 'bar' || spec.type === 'line' ? 'of' : 'Y'}
            <select className={selectClass} value={spec.y} onChange={e => set({ y: e.target.value })}>
              <option value="">Select…</option>
              {numeric.map(c => <option key={c} value={c}>{c}</option>)}
            </select>
          </label>
        )}
        {spec.type === 'histogram' && (
          <label className="flex items-center gap-2 text-[12px] text-on-surface-variant">Bins
            <input type="number" min={2} max={200} className={`${selectClass} w-[70px]`} value={spec.bins} onChange={e => set({ bins: Number(e.target.value) || 30 })} />
          </label>
        )}
        <button className="toolbar-btn ml-auto" onClick={() => setShowTable(t => !t)}>
          <span className="material-symbols-outlined text-[16px]">{showTable ? 'bar_chart' : 'table'}</span>{showTable ? 'Show chart' : 'Show table'}
        </button>
      </div>
      <div className="flex-1 overflow-auto p-6 bg-white">
        {!columns.length ? (
          <div className="text-on-surface-variant font-ui-label">No columns to chart.</div>
        ) : !data ? (
          <div className="text-on-surface-variant font-ui-label">{loading ? 'Loading…' : 'Choose columns to chart.'}</div>
        ) : (
          <div className={loading ? 'opacity-50 transition-opacity' : ''}>
            <h3 className="m-0 mb-1 font-panel-title text-panel-title text-on-surface">{title}</h3>
            <p className="m-0 mb-3 text-[12px] text-on-surface-variant">
              {data.truncated && `Top ${data.points.length} of ${data.total_groups.toLocaleString()} groups. `}
              {data.sampled && `${data.type === 'scatter' ? 'Random sample' : 'Evenly spaced subset'} of ${data.points.length.toLocaleString()} of ${data.total_points.toLocaleString()} points. `}
              Computed on all rows of this step.
            </p>
            {showTable ? (
              <table className="text-[13px] border-collapse min-w-[320px]">
                <thead><tr><th className="text-left border-b border-grid-border px-2 py-1">{data.type === 'histogram' ? 'Range' : data.x}</th><th className="text-right border-b border-grid-border px-2 py-1">{data.type === 'histogram' ? 'Rows' : data.y || 'Rows'}</th></tr></thead>
                <tbody>{tableRows.map((r, i) => <tr key={i}><td className="px-2 py-[2px] border-b border-grid-border">{r[0]}</td><td className="px-2 py-[2px] border-b border-grid-border text-right tabular-nums">{formatValue(r[1])}</td></tr>)}</tbody>
              </table>
            ) : (
              <Chart data={data} xLabel={data.x} yLabel={data.type === 'histogram' ? 'rows' : data.type === 'bar' && data.agg === 'count' && !data.y ? 'rows' : data.y} />
            )}
          </div>
        )}
      </div>
    </div>
  );
}
