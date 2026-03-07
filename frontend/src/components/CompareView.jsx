import React, { useEffect, useMemo, useRef, useState } from 'react';
import { AgGridReact } from 'ag-grid-react';
import { api, formatValue } from '../api';

const ROW_LIMIT = 500;

function nodeOptions(project) {
  const headToBranches = {};
  Object.values(project.branches).forEach(b => { (headToBranches[b.head_node_id] ||= []).push(b.name); });
  const heads = [];
  const others = [];
  Object.values(project.nodes).forEach(n => {
    const branches = headToBranches[n.id];
    const label = `${branches ? `[${branches.join(', ')}] ` : ''}${n.operation.label}`;
    (branches ? heads : others).push({ id: n.id, label });
  });
  return { heads, others };
}

function MissingCellRenderer(props) {
  return (
    <div className="w-full h-full hash-pattern opacity-60 flex items-center justify-center" style={{ margin: '0 -8px' }}>
      {props.node.rowIndex === 0 && <span className="text-[10px] italic text-on-surface-variant bg-surface-container-low px-1 rounded">not present</span>}
    </div>
  );
}

function buildColDefs(columns, mine, other) {
  const rowNumCol = { headerName: '#', valueGetter: 'node.rowIndex + 1', width: 56, pinned: 'left', cellClass: 'row-number-cell' };
  if (!columns.length) return [rowNumCol];
  return [rowNumCol, ...columns.map(field => {
    if (mine && !mine[field]) {
      return { field, headerName: '–', cellRenderer: MissingCellRenderer, sortable: false, resizable: false, minWidth: 110, flex: 1, cellClass: 'p-0' };
    }
    const added = mine && other && !other[field];
    const changed = mine && other && other[field] && other[field] !== mine[field];
    return {
      field,
      headerName: `${field}${added ? '  +' : ''}${changed ? `  (${mine[field]})` : ''}`,
      headerClass: added || changed ? 'bg-secondary-container text-on-secondary-container' : '',
      cellClass: added ? 'border-x-2 border-primary-container' : '',
      valueFormatter: p => formatValue(p.value, mine[field]),
      sortable: true, resizable: true, minWidth: 110, flex: 1,
    };
  })];
}

export default function CompareView({ project, baseNode, compareNode, setCompareNode, data, schema, compareData, compareSchema, fetchData }) {
  const leftRef = useRef(null);
  const rightRef = useRef(null);
  const [diff, setDiff] = useState(null);
  const [sync, setSync] = useState(true);
  const { heads, others } = useMemo(() => nodeOptions(project), [project]);

  useEffect(() => {
    if (!compareNode || !baseNode) { setDiff(null); return; }
    api(`/project/${project.id}/diff`, { query: { node_a: baseNode, node_b: compareNode } }).then(setDiff).catch(() => setDiff(null));
  }, [project.id, baseNode, compareNode]);

  // Keep both grids at the same scroll position.
  useEffect(() => {
    if (!sync) return undefined;
    const viewport = el => el?.querySelector('.ag-body-viewport');
    const hscroll = el => el?.querySelector('.ag-center-cols-viewport');
    let lock = false;
    const link = (from, to) => () => {
      if (lock) return;
      lock = true;
      const a = viewport(from), b = viewport(to);
      if (a && b) b.scrollTop = a.scrollTop;
      const ha = hscroll(from), hb = hscroll(to);
      if (ha && hb) hb.scrollLeft = ha.scrollLeft;
      requestAnimationFrame(() => { lock = false; });
    };
    const l = leftRef.current, r = rightRef.current;
    const lv = viewport(l), rv = viewport(r), lh = hscroll(l), rh = hscroll(r);
    const onL = link(l, r), onR = link(r, l);
    [lv, lh].forEach(el => el?.addEventListener('scroll', onL, { passive: true }));
    [rv, rh].forEach(el => el?.addEventListener('scroll', onR, { passive: true }));
    return () => {
      [lv, lh].forEach(el => el?.removeEventListener('scroll', onL));
      [rv, rh].forEach(el => el?.removeEventListener('scroll', onR));
    };
  }, [sync, data, compareData]);

  const columns = useMemo(() => Array.from(new Set([...Object.keys(schema || {}), ...Object.keys(compareSchema || {})])), [schema, compareSchema]);
  const baseColDefs = useMemo(() => buildColDefs(columns, schema, compareSchema), [columns, schema, compareSchema]);
  const compareColDefs = useMemo(() => buildColDefs(columns, compareSchema, schema), [columns, schema, compareSchema]);
  const baseLabel = project.nodes[baseNode]?.operation.label || 'Current step';

  return (
    <div className="flex-1 flex flex-col min-w-0 bg-background relative h-full">
      <div className="min-h-[44px] bg-surface-container-low border-b border-surface-variant flex flex-wrap items-center shrink-0 px-4 py-1 gap-3">
        <span className="text-ui-small text-on-surface-variant uppercase tracking-wider font-semibold">Base</span>
        <span className="font-medium text-sm text-on-surface truncate max-w-[240px]" title={baseLabel}>{baseLabel}</span>
        <label className="flex items-center gap-1 px-3 py-1 bg-surface-variant rounded-full text-on-surface-variant border border-outline-variant text-xs cursor-pointer">
          <input type="checkbox" checked={sync} onChange={e => setSync(e.target.checked)} className="accent-[#c98f00]" /> Sync scroll
        </label>
        <span className="text-ui-small text-on-surface-variant uppercase tracking-wider font-semibold ml-auto">Compare with</span>
        <select value={compareNode || ''} onChange={e => { setCompareNode(e.target.value); if (e.target.value) fetchData(project.id, e.target.value, true); }}
          className="bg-white border border-outline-variant rounded px-2 py-1 text-sm max-w-[320px] focus:outline-none focus:border-primary-container">
          <option value="">Select a step…</option>
          <optgroup label="Branch heads">{heads.map(n => <option key={n.id} value={n.id}>{n.label}</option>)}</optgroup>
          {others.length > 0 && <optgroup label="Other steps">{others.map(n => <option key={n.id} value={n.id}>{n.label}</option>)}</optgroup>}
        </select>
      </div>
      {diff && (
        <div className="px-4 py-2 border-b border-surface-variant bg-white text-[12px] text-on-surface flex flex-wrap gap-x-6 gap-y-1">
          <span>Rows: {diff.row_count_a.toLocaleString()} → {diff.row_count_b.toLocaleString()} <strong className={diff.row_count_delta ? 'text-secondary' : ''}>({diff.row_count_delta >= 0 ? '+' : ''}{diff.row_count_delta.toLocaleString()})</strong></span>
          {diff.columns_added.length > 0 && <span>Added: {diff.columns_added.join(', ')}</span>}
          {diff.columns_removed.length > 0 && <span>Removed: {diff.columns_removed.join(', ')}</span>}
          {diff.dtype_changes.length > 0 && <span>Type changes: {diff.dtype_changes.map(d => `${d.column} ${d.from}→${d.to}`).join(', ')}</span>}
          {!diff.columns_added.length && !diff.columns_removed.length && !diff.dtype_changes.length && <span className="text-on-surface-variant">Same columns and types</span>}
          <span className="ml-auto text-on-surface-variant">Grids show the first {ROW_LIMIT} rows of each</span>
        </div>
      )}
      <div className="flex-1 flex overflow-hidden">
        <div ref={leftRef} className="flex-1 flex flex-col min-w-0 bg-surface-container-lowest ag-theme-alpine ag-theme-loom">
          {data ? <AgGridReact rowData={data} columnDefs={baseColDefs} rowHeight={30} headerHeight={30} /> : <div className="flex h-full items-center justify-center text-on-surface-variant font-ui-label">No base data</div>}
        </div>
        <div className="w-[2px] bg-primary-container shrink-0"></div>
        <div ref={rightRef} className="flex-1 flex flex-col min-w-0 bg-surface-container-lowest ag-theme-alpine ag-theme-loom">
          {compareNode && compareData ? <AgGridReact rowData={compareData} columnDefs={compareColDefs} rowHeight={30} headerHeight={30} /> : <div className="flex h-full items-center justify-center text-on-surface-variant font-ui-label">Pick a step to compare</div>}
        </div>
      </div>
    </div>
  );
}

export { ROW_LIMIT };
