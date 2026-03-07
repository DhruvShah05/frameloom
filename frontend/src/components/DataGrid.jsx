import React, { forwardRef, useCallback, useEffect, useImperativeHandle, useMemo, useRef, useState } from 'react';
import { AgGridReact } from 'ag-grid-react';
import 'ag-grid-community/styles/ag-grid.css';
import 'ag-grid-community/styles/ag-theme-alpine.css';
import { api, formatValue, isNumericType, isDateType, isTextType } from '../api';

const MARK = '#a87600'; // chart gold that passes 3:1 contrast on white

// ------------------------------------------------ AG Grid filter -> Loom filter
const TEXT_OPS = { contains: 'contains', notContains: 'not_contains', equals: 'eq', notEqual: 'ne', startsWith: 'starts_with', endsWith: 'ends_with', blank: 'is_null', notBlank: 'is_not_null' };
const NUM_OPS = { equals: 'eq', notEqual: 'ne', greaterThan: 'gt', greaterThanOrEqual: 'ge', lessThan: 'lt', lessThanOrEqual: 'le', inRange: 'between', blank: 'is_null', notBlank: 'is_not_null' };

function conditionFromModel(column, m, dtype) {
  if (m.filterType === 'date') {
    const op = NUM_OPS[m.type];
    const trim = v => (v && dtype === 'Date' ? v.slice(0, 10) : v);
    return op ? { column, operator: op, value: trim(m.dateFrom), value2: trim(m.dateTo) } : null;
  }
  if (m.filterType === 'number') {
    const op = NUM_OPS[m.type];
    return op ? { column, operator: op, value: m.filter, value2: m.filterTo } : null;
  }
  const op = TEXT_OPS[m.type];
  return op ? { column, operator: op, value: m.filter, ignore_case: true } : null;
}

function filterModelToFilters(model, schema) {
  const out = [];
  for (const [column, m] of Object.entries(model || {})) {
    const dtype = schema?.[column];
    if (m.conditions) {
      const conds = m.conditions.map(c => conditionFromModel(column, c, dtype)).filter(Boolean);
      if (conds.length === 1) out.push(conds[0]);
      else if (conds.length) out.push({ conditions: conds, combine: (m.operator || 'AND').toLowerCase() });
    } else {
      const c = conditionFromModel(column, m, dtype);
      if (c) out.push(c);
    }
  }
  return out;
}

// ------------------------------------------------------------ header + spark
function Sparkline({ info }) {
  if (!info) return <div className="h-[16px]" />;
  let values = [];
  if (info.histogram?.length) values = info.histogram.map(b => b.count);
  else if (info.top_values?.length) values = info.top_values.map(t => t.count);
  const max = Math.max(1, ...values);
  const n = values.length || 1;
  const w = 100 / n;
  const filled = info.count + info.nulls ? (100 * info.count) / (info.count + info.nulls) : 0;
  const tip = [
    `${info.dtype}`,
    `${info.null_pct}% empty`,
    info.unique != null ? `${info.unique.toLocaleString()} distinct` : null,
    info.min != null ? `min ${formatValue(info.min, info.dtype)}` : null,
    info.max != null ? `max ${formatValue(info.max, info.dtype)}` : null,
    info.issues?.length ? `⚠ ${info.issues.filter(i => i.level === 'warning').length} issue(s)` : null,
  ].filter(Boolean).join(' · ');
  return (
    <div className="w-full" title={tip}>
      <svg viewBox="0 0 100 14" preserveAspectRatio="none" className="w-full h-[14px] block" aria-hidden="true">
        {values.map((v, i) => {
          const h = Math.max(v > 0 ? 1.5 : 0, (v / max) * 14);
          return <rect key={i} x={i * w + 0.4} y={14 - h} width={Math.max(w - 0.8, 0.4)} height={h} fill={MARK} opacity={info.histogram ? 0.85 : 0.6} />;
        })}
      </svg>
      <div className="h-[2px] w-full bg-error/30 mt-[2px]" aria-hidden="true">
        <div className="h-full bg-[#7a8f6a]" style={{ width: `${filled}%` }} />
      </div>
    </div>
  );
}

function ProfileHeader(props) {
  const { column, displayName, profile, dtype, enableSorting, progressSort, showColumnMenu } = props;
  const menuRef = useRef(null);
  const [sort, setSort] = useState(column.getSort());
  useEffect(() => {
    const onSort = () => setSort(column.getSort());
    column.addEventListener('sortChanged', onSort);
    return () => column.removeEventListener('sortChanged', onSort);
  }, [column]);
  const warnings = profile?.issues?.filter(i => i.level === 'warning').length || 0;
  return (
    <div className="flex flex-col w-full gap-[3px] py-[3px] min-w-0">
      <div className="flex items-center gap-1 min-w-0">
        <button type="button" onClick={e => enableSorting && progressSort(e.shiftKey)}
          className="flex items-center gap-1 min-w-0 bg-transparent border-0 p-0 cursor-pointer text-left font-grid-header text-grid-header text-on-surface"
          title={`${displayName} (${dtype}) — click to sort, shift+click to add`}>
          <span className="truncate">{displayName}</span>
          {sort && <span className="text-primary text-[11px]">{sort === 'asc' ? '▲' : '▼'}</span>}
        </button>
        {warnings > 0 && <span className="text-[10px] font-semibold text-on-secondary-container bg-secondary-container rounded-full px-1" title={`${warnings} data-quality issue(s) — see Profile`}>!</span>}
        <span className="ml-auto text-[10px] text-on-surface-variant font-normal shrink-0">{shortType(dtype)}</span>
        <button ref={menuRef} type="button" aria-label="Column menu" onClick={() => showColumnMenu(menuRef.current)}
          className="bg-transparent border-0 p-0 cursor-pointer text-on-surface-variant hover:text-primary leading-none">
          <span className="material-symbols-outlined text-[16px]">more_vert</span>
        </button>
      </div>
      <Sparkline info={profile} />
    </div>
  );
}

function shortType(t = '') {
  if (t.startsWith('Datetime')) return 'datetime';
  return t.replace('String', 'text').replace('Float64', 'f64').replace('Int64', 'i64').toLowerCase();
}

// ------------------------------------------------------------- column panel
function ColumnPanel({ columns, schema, hidden, pinned, heat, onToggleHidden, onTogglePin, onToggleHeat, onJump, onClose }) {
  const [q, setQ] = useState('');
  const shown = columns.filter(c => c.toLowerCase().includes(q.toLowerCase()));
  return (
    <div className="absolute right-2 top-[44px] z-30 w-[300px] max-h-[70vh] flex flex-col bg-white border border-grid-border rounded-lg shadow-[0_8px_24px_rgba(0,0,0,0.08)]">
      <div className="flex items-center gap-2 p-2 border-b border-grid-border">
        <input autoFocus className="flex-1 border border-grid-border rounded-DEFAULT px-2 h-[28px] text-[12px] outline-none focus:border-primary-container" placeholder={`Search ${columns.length} columns…`} value={q} onChange={e => setQ(e.target.value)} />
        <button className="bg-transparent border-0 cursor-pointer text-on-surface-variant" onClick={onClose} aria-label="Close"><span className="material-symbols-outlined text-[18px]">close</span></button>
      </div>
      <div className="overflow-y-auto grid-scroll py-1">
        {shown.map(c => (
          <div key={c} className="flex items-center gap-2 px-2 py-1 text-[12px] hover:bg-highlight-bg">
            <input type="checkbox" checked={!hidden.includes(c)} onChange={() => onToggleHidden(c)} className="accent-[#c98f00]" title="Show column" />
            <button className="flex-1 min-w-0 text-left truncate bg-transparent border-0 p-0 cursor-pointer hover:underline" onClick={() => onJump(c)} title="Scroll to column">{c}</button>
            <span className="text-[10px] text-on-surface-variant">{shortType(schema[c])}</span>
            {isNumericType(schema[c]) && (
              <button className={`bg-transparent border-0 p-0 cursor-pointer ${heat.includes(c) ? 'text-primary' : 'text-outline'}`} onClick={() => onToggleHeat(c)} title="Colour cells by value">
                <span className="material-symbols-outlined text-[16px]">gradient</span>
              </button>
            )}
            <button className={`bg-transparent border-0 p-0 cursor-pointer ${pinned.includes(c) ? 'text-primary' : 'text-outline'}`} onClick={() => onTogglePin(c)} title="Pin to the left">
              <span className="material-symbols-outlined text-[16px]">push_pin</span>
            </button>
          </div>
        ))}
      </div>
    </div>
  );
}

// -------------------------------------------------------------------- grid
const DataGrid = forwardRef(function DataGrid(
  { projectId, nodeId, schema, profile, refreshKey, onViewChange, onEditCell, onError, onTotal },
  ref,
) {
  const gridRef = useRef(null);
  const schemaRef = useRef(schema);
  schemaRef.current = schema;
  const nodeRef = useRef(nodeId);
  nodeRef.current = nodeId;
  const [panelOpen, setPanelOpen] = useState(false);
  const [hidden, setHidden] = useState([]);
  const [pinned, setPinned] = useState([]);
  const [heat, setHeat] = useState([]);

  const profileByCol = useMemo(() => Object.fromEntries((profile?.columns || []).map(c => [c.name, c])), [profile]);
  const columns = useMemo(() => Object.keys(schema || {}), [schema]);

  // Forget per-column view settings for columns that no longer exist.
  useEffect(() => {
    setHidden(h => h.filter(c => columns.includes(c)));
    setPinned(p => p.filter(c => columns.includes(c)));
    setHeat(h => h.filter(c => columns.includes(c)));
  }, [columns]);

  useImperativeHandle(ref, () => ({
    clearView() {
      const gridApi = gridRef.current?.api;
      if (!gridApi) return;
      gridApi.setFilterModel(null);
      gridApi.applyColumnState({ defaultState: { sort: null } });
    },
  }), []);

  const datasource = useMemo(() => ({
    getRows: async params => {
      const sort = params.sortModel.map(s => ({ column: s.colId, descending: s.sort === 'desc' }));
      const filters = filterModelToFilters(params.filterModel, schemaRef.current);
      try {
        const r = await api(`/project/${projectId}/view`, {
          method: 'POST',
          body: { node_id: nodeId, offset: params.startRow, limit: params.endRow - params.startRow, sort, filters },
        });
        params.successCallback(r.data, r.total_rows);
        // Ignore late answers for a step the user has already moved away from.
        if (nodeRef.current === nodeId) onTotal?.(filters.length > 0 ? r.total_rows : null);
      } catch (e) {
        params.failCallback();
        onError?.(e.message);
      }
    },
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }), [projectId, nodeId, refreshKey]);

  const columnDefs = useMemo(() => {
    if (!schema) return [];
    const rowNumCol = {
      headerName: '#', colId: '__rownum', valueGetter: 'node.rowIndex + 1', width: 56, pinned: 'left',
      sortable: false, filter: false, resizable: false, editable: false, suppressMovable: true,
      cellClass: 'row-number-cell', headerClass: 'row-number-header', suppressHeaderMenuButton: true,
    };
    const dataCols = columns.map(field => {
      const dtype = schema[field];
      const info = profileByCol[field];
      const numeric = isNumericType(dtype);
      const heatOn = heat.includes(field) && info && info.min != null && info.max != null && info.max !== info.min;
      return {
        field,
        colId: field,
        headerName: field,
        hide: hidden.includes(field),
        pinned: pinned.includes(field) ? 'left' : null,
        sortable: true,
        resizable: true,
        editable: !/^(List|Struct|Array|Binary|Object)/.test(dtype),
        minWidth: 90,
        width: numeric ? 120 : 160,
        cellDataType: numeric ? 'number' : dtype === 'Boolean' ? 'boolean' : 'text',
        filter: numeric ? 'agNumberColumnFilter' : isDateType(dtype) ? 'agDateColumnFilter' : 'agTextColumnFilter',
        filterParams: { maxNumConditions: 2, buttons: ['reset'], debounceMs: 300 },
        floatingFilter: true,
        headerComponent: ProfileHeader,
        headerComponentParams: { profile: info, dtype },
        valueFormatter: p => formatValue(p.value, dtype),
        cellClassRules: { 'cell-null': p => p.value === null || p.value === undefined, 'cell-number': () => numeric },
        cellStyle: heatOn
          ? p => {
            if (typeof p.value !== 'number') return null;
            const t = Math.min(1, Math.max(0, (p.value - info.min) / (info.max - info.min)));
            return { backgroundColor: `rgba(201, 143, 0, ${0.06 + t * 0.4})` };
          }
          : null,
        tooltipValueGetter: isTextType(dtype) ? p => (typeof p.value === 'string' && p.value.length > 40 ? p.value : null) : undefined,
      };
    });
    return [rowNumCol, ...dataCols];
  }, [schema, columns, profileByCol, hidden, pinned, heat]);

  const onSortOrFilter = useCallback(() => {
    const gridApi = gridRef.current?.api;
    if (!gridApi) return;
    const sort = gridApi.getColumnState().filter(c => c.sort).sort((a, b) => (a.sortIndex ?? 0) - (b.sortIndex ?? 0))
      .map(c => ({ column: c.colId, descending: c.sort === 'desc' }));
    onViewChange?.({ sort, filters: filterModelToFilters(gridApi.getFilterModel(), schemaRef.current) });
  }, [onViewChange]);

  const onCellValueChanged = useCallback(p => {
    if (p.oldValue === p.newValue) return;
    const rowNr = p.data?.__row_nr;
    if (rowNr === undefined) return;
    onEditCell?.(rowNr, p.colDef.field, p.newValue === undefined ? null : p.newValue);
  }, [onEditCell]);

  const toggle = setter => c => setter(list => (list.includes(c) ? list.filter(x => x !== c) : [...list, c]));

  if (!schema) {
    return <div className="flex h-full items-center justify-center text-on-surface-variant font-ui-label">Loading…</div>;
  }

  return (
    <div className="relative h-full w-full flex flex-col">
      <div className="h-[36px] shrink-0 flex items-center gap-2 px-2 border-b border-grid-border bg-white">
        <button className="toolbar-btn" onClick={() => setPanelOpen(o => !o)}>
          <span className="material-symbols-outlined text-[16px]">view_column</span> Columns
          {hidden.length > 0 && <span className="text-[10px] text-on-surface-variant">({hidden.length} hidden)</span>}
        </button>
        <button className="toolbar-btn" onClick={() => gridRef.current?.api?.autoSizeAllColumns()}>
          <span className="material-symbols-outlined text-[16px]">fit_width</span> Fit widths
        </button>
        <span className="ml-auto text-[11px] text-on-surface-variant hidden lg:inline">Double-click a cell to edit · header ⋮ for pin & size · shift-click headers to sort by several</span>
      </div>
      {panelOpen && (
        <ColumnPanel
          columns={columns} schema={schema} hidden={hidden} pinned={pinned} heat={heat}
          onToggleHidden={toggle(setHidden)} onTogglePin={toggle(setPinned)} onToggleHeat={toggle(setHeat)}
          onJump={c => { setHidden(h => h.filter(x => x !== c)); setTimeout(() => gridRef.current?.api?.ensureColumnVisible(c, 'start'), 0); }}
          onClose={() => setPanelOpen(false)}
        />
      )}
      <div className="ag-theme-alpine ag-theme-loom flex-1 min-h-0">
        <AgGridReact
          ref={gridRef}
          rowModelType="infinite"
          datasource={datasource}
          cacheBlockSize={200}
          maxBlocksInCache={50}
          infiniteInitialRowCount={200}
          columnDefs={columnDefs}
          defaultColDef={{ suppressHeaderMenuButton: false }}
          rowHeight={30}
          headerHeight={52}
          floatingFiltersHeight={30}
          onSortChanged={onSortOrFilter}
          onFilterChanged={onSortOrFilter}
          onCellValueChanged={onCellValueChanged}
          singleClickEdit={false}
          stopEditingWhenCellsLoseFocus
          tooltipShowDelay={400}
          multiSortKey="shift"
          suppressDragLeaveHidesColumns
        />
      </div>
    </div>
  );
});

export default DataGrid;
