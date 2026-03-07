import React, { useMemo, useState } from 'react';
import Chart from './Chart';
import { formatValue } from '../api';

function Stat({ label, value }) {
  return (
    <div className="flex justify-between gap-2 text-[12px] py-[2px] border-b border-grid-border last:border-0">
      <span className="text-on-surface-variant">{label}</span>
      <span className="text-on-surface tabular-nums truncate" title={String(value)}>{value}</span>
    </div>
  );
}

function ColumnCard({ col, onFix, busy }) {
  const stats = [
    ['Type', col.dtype],
    ['Filled', `${col.count.toLocaleString()} (${(100 - col.null_pct).toFixed(1)}%)`],
    ['Empty', `${col.nulls.toLocaleString()} (${col.null_pct}%)`],
    col.unique != null && ['Distinct', col.unique.toLocaleString()],
    col.min != null && ['Min', formatValue(col.min, col.dtype)],
    col.max != null && ['Max', formatValue(col.max, col.dtype)],
    col.mean != null && ['Mean', formatValue(col.mean)],
    col.median != null && ['Median', formatValue(col.median)],
    col.std != null && ['Std dev', formatValue(col.std)],
    col.zeros != null && ['Zeros', col.zeros.toLocaleString()],
    col.negatives != null && ['Negatives', col.negatives.toLocaleString()],
    col.min_length != null && ['Length', `${col.min_length}–${col.max_length} chars`],
  ].filter(Boolean);
  const topMax = Math.max(1, ...(col.top_values || []).map(t => t.count));
  const histData = col.histogram?.length
    ? { type: 'histogram', x: col.name, x_type: col.kind === 'temporal' ? 'temporal' : 'numeric', bins: col.histogram }
    : null;

  return (
    <div className="bg-white border border-grid-border rounded-lg p-3 flex flex-col gap-2 min-w-0">
      <div className="flex items-center gap-2 min-w-0">
        <h4 className="m-0 font-panel-title text-[14px] text-on-surface truncate" title={col.name}>{col.name}</h4>
        <span className="ml-auto text-[11px] text-on-surface-variant shrink-0">{col.kind}</span>
      </div>
      <div className="h-[3px] w-full bg-error/30 rounded-full overflow-hidden" title={`${(100 - col.null_pct).toFixed(1)}% filled`}>
        <div className="h-full bg-[#7a8f6a]" style={{ width: `${100 - col.null_pct}%` }} />
      </div>
      <div>{stats.map(([l, v]) => <Stat key={l} label={l} value={v} />)}</div>
      {histData && <Chart data={histData} height={150} />}
      {col.top_values?.length > 0 && (
        <div className="flex flex-col gap-[3px]">
          <span className="text-[11px] text-on-surface-variant">Most common</span>
          {col.top_values.slice(0, 6).map((t, i) => (
            <div key={i} className="flex items-center gap-2 text-[12px]">
              <span className="w-[45%] truncate" title={String(t.value)}>{t.value === '' ? <em className="text-on-surface-variant">(blank)</em> : String(t.value)}</span>
              <div className="flex-1 h-[8px] bg-surface-container rounded-sm overflow-hidden">
                <div className="h-full bg-[#a87600] rounded-sm" style={{ width: `${(100 * t.count) / topMax}%` }} />
              </div>
              <span className="w-[48px] text-right tabular-nums text-on-surface-variant">{t.count.toLocaleString()}</span>
            </div>
          ))}
        </div>
      )}
      {col.issues?.length > 0 && (
        <div className="flex flex-col gap-1 pt-1">
          {col.issues.map((issue, i) => (
            <div key={i} className={`flex items-start gap-2 text-[12px] rounded-DEFAULT px-2 py-1 ${issue.level === 'warning' ? 'bg-audit-mismatch' : 'bg-surface-container-low'}`}>
              <span className="material-symbols-outlined text-[14px] mt-[1px] text-on-surface-variant">{issue.level === 'warning' ? 'warning' : 'info'}</span>
              <span className="flex-1">{issue.message}</span>
              {issue.fix && (
                <button disabled={busy} className="shrink-0 text-[11px] font-semibold text-primary hover:underline bg-transparent border-0 cursor-pointer p-0 disabled:opacity-50"
                  onClick={() => onFix(issue.fix)} title={`Adds a "${issue.fix.type.replace(/_/g, ' ')}" step`}>
                  Fix
                </button>
              )}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

export default function ProfileView({ profile, loading, onApplyOp, busy }) {
  const [q, setQ] = useState('');
  const [onlyIssues, setOnlyIssues] = useState(false);
  const cols = useMemo(() => (profile?.columns || []).filter(c =>
    c.name.toLowerCase().includes(q.toLowerCase()) && (!onlyIssues || c.issues.some(i => i.level === 'warning'))), [profile, q, onlyIssues]);
  const warningCount = (profile?.columns || []).reduce((n, c) => n + c.issues.filter(i => i.level === 'warning').length, 0);

  return (
    <div className="flex-1 flex flex-col min-w-0 overflow-hidden">
      <div className="min-h-[48px] flex flex-wrap items-center gap-4 px-4 py-2 border-b border-grid-border bg-sidebar-bg">
        {profile && (
          <span className="text-[13px] text-on-surface">
            <strong>{profile.row_count.toLocaleString()}</strong> rows · <strong>{profile.column_count}</strong> columns ·{' '}
            <strong>{warningCount}</strong> data-quality warning{warningCount === 1 ? '' : 's'}
          </span>
        )}
        <input className="bg-white border border-grid-border rounded-DEFAULT px-2 h-[30px] text-[13px] outline-none focus:border-primary-container ml-auto w-[220px]" placeholder="Search columns…" value={q} onChange={e => setQ(e.target.value)} />
        <label className="flex items-center gap-2 text-[12px] text-on-surface-variant cursor-pointer">
          <input type="checkbox" checked={onlyIssues} onChange={e => setOnlyIssues(e.target.checked)} className="accent-[#c98f00]" /> Only columns with warnings
        </label>
      </div>
      <div className="flex-1 overflow-auto p-4 grid-scroll">
        {!profile ? (
          <div className="text-on-surface-variant font-ui-label">{loading ? 'Profiling every column…' : 'No profile yet.'}</div>
        ) : (
          <div className={`grid gap-3 ${loading ? 'opacity-50' : ''}`} style={{ gridTemplateColumns: 'repeat(auto-fill, minmax(280px, 1fr))' }}>
            {cols.map(c => <ColumnCard key={c.name} col={c} busy={busy} onFix={fix => onApplyOp(fix.type, fix.params)} />)}
            {!cols.length && <div className="text-on-surface-variant font-ui-label">No matching columns.</div>}
          </div>
        )}
      </div>
    </div>
  );
}
