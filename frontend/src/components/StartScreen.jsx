import React, { useEffect, useState } from 'react';
import { api } from '../api';

const inputClass = "w-full bg-white border border-grid-border rounded-DEFAULT px-2 h-row-height font-grid-data text-grid-data focus:border-primary-container focus:ring-1 focus:ring-bright-accent outline-none";
const labelClass = "font-ui-small text-ui-small text-on-surface-variant";

const FORMAT_LABELS = {
  csv: 'CSV', tsv: 'TSV', parquet: 'Parquet', ipc: 'Arrow / Feather', json: 'JSON', ndjson: 'NDJSON',
  excel: 'Excel', sqlite: 'SQLite database', duckdb: 'DuckDB database',
};

export default function StartScreen({ recipes, onLoad, onOpenProject, onError, busy, initialPath = '' }) {
  const [path, setPath] = useState(initialPath);
  const [info, setInfo] = useState(null);
  const [options, setOptions] = useState({});
  const [recipeId, setRecipeId] = useState('');
  const [showAdvanced, setShowAdvanced] = useState(false);
  const [recent, setRecent] = useState([]);
  const [pin, setPin] = useState(null); // null = server default (copy files up to 2 GB)

  const refreshRecent = () => api('/projects').then(setRecent).catch(() => {});
  useEffect(() => { refreshRecent(); }, []);

  const inspect = async () => {
    const p = path.trim();
    if (!p) return null;
    try {
      const r = await api('/source/inspect', { method: 'POST', body: { path: p, options: options.format ? { format: options.format } : {} } });
      setInfo(r);
      if (r.tables.length && r.format === 'excel' && !options.sheet_name) setOptions(o => ({ ...o, sheet_name: r.tables[0] }));
      if (r.tables.length && r.needs_table && !options.table && !options.query) setOptions(o => ({ ...o, table: r.tables[0] }));
      return r;
    } catch (e) {
      setInfo(null);
      onError(e.message);
      return null;
    }
  };

  const submit = async e => {
    e?.preventDefault();
    const r = info?.path === path.trim() ? info : await inspect();
    if (!r) return;
    if (r.needs_table && !options.table && !options.query) {
      onError('Pick a table (or write a query) for this database.');
      return;
    }
    onLoad(path.trim(), options, recipeId || null, pin);
  };

  const removeProject = async p => {
    if (!window.confirm(`Delete the saved history for "${p.name}"? The data file itself is not touched. This can't be undone.`)) return;
    try {
      await api(`/project/${p.id}`, { method: 'DELETE' });
      refreshRecent();
    } catch (e) {
      onError(e.message);
    }
  };

  const fmt = info?.format;
  const setOpt = (k, v) => setOptions(o => ({ ...o, [k]: v === '' ? undefined : v }));

  return (
    <div className="flex-1 overflow-auto bg-surface-container-lowest">
      <div className="max-w-[880px] mx-auto py-12 px-4 flex flex-col md:flex-row gap-6 items-start">
        <form onSubmit={submit} className="bg-white p-6 rounded-lg border border-grid-border shadow-sm w-full md:w-[420px] flex flex-col gap-4 shrink-0">
          <h2 className="font-panel-title text-panel-title font-bold m-0">Open data</h2>
          <div className="flex flex-col gap-1">
            <label className={labelClass} htmlFor="filepath">File path or URL</label>
            <input id="filepath" className={`${inputClass} font-mono text-[12px]`} placeholder="/path/to/data.csv  ·  s3://bucket/file.parquet" value={path}
              onChange={e => { setPath(e.target.value); setInfo(null); }} onBlur={() => path.trim() && inspect()} autoFocus />
            <span className="text-[11px] text-on-surface-variant">CSV, TSV, Parquet, Excel, JSON, NDJSON, Arrow, SQLite, DuckDB — local or http/s3/gs.</span>
          </div>

          {fmt && (
            <div className="text-[12px] text-on-surface flex items-center gap-1">
              <span className="material-symbols-outlined text-[16px] text-primary-container">check_circle</span>
              Detected: <strong>{FORMAT_LABELS[fmt] || fmt}</strong>
            </div>
          )}

          {fmt === 'excel' && info.tables.length > 0 && (
            <div className="flex flex-col gap-1">
              <label className={labelClass}>Sheet</label>
              <select className={inputClass} value={options.sheet_name || ''} onChange={e => setOpt('sheet_name', e.target.value)}>
                {info.tables.map(t => <option key={t} value={t}>{t}</option>)}
              </select>
            </div>
          )}

          {info?.needs_table && (
            <>
              <div className="flex flex-col gap-1">
                <label className={labelClass}>Table</label>
                <select className={inputClass} value={options.table || ''} onChange={e => setOpt('table', e.target.value)}>
                  <option value="">(use a query instead)</option>
                  {info.tables.map(t => <option key={t} value={t}>{t}</option>)}
                </select>
              </div>
              {!options.table && (
                <div className="flex flex-col gap-1">
                  <label className={labelClass}>Query</label>
                  <textarea className={`${inputClass} h-auto py-1 font-mono text-[12px]`} rows={3} value={options.query || ''} onChange={e => setOpt('query', e.target.value)} placeholder="SELECT * FROM orders WHERE year = 2024" />
                </div>
              )}
            </>
          )}

          {(fmt === 'csv' || fmt === 'tsv' || !fmt) && (
            <div>
              <button type="button" className="text-[12px] text-primary bg-transparent border-0 p-0 cursor-pointer hover:underline" onClick={() => setShowAdvanced(s => !s)}>
                {showAdvanced ? '▾' : '▸'} Text file options
              </button>
              {showAdvanced && (
                <div className="grid grid-cols-2 gap-3 mt-2">
                  <div className="flex flex-col gap-1">
                    <label className={labelClass}>Separator</label>
                    <select className={inputClass} value={options.separator || ''} onChange={e => setOpt('separator', e.target.value)}>
                      <option value="">Auto ({fmt === 'tsv' ? 'tab' : 'comma'})</option>
                      <option value=",">Comma ,</option>
                      <option value=";">Semicolon ;</option>
                      <option value={'\t'}>Tab</option>
                      <option value="|">Pipe |</option>
                    </select>
                  </div>
                  <div className="flex flex-col gap-1">
                    <label className={labelClass}>Encoding</label>
                    <select className={inputClass} value={options.encoding || ''} onChange={e => setOpt('encoding', e.target.value)}>
                      <option value="">UTF-8</option>
                      <option value="utf8-lossy">UTF-8 (replace bad bytes)</option>
                      <option value="latin-1">Latin-1 / ISO-8859-1</option>
                      <option value="cp1252">Windows-1252</option>
                      <option value="utf-16">UTF-16</option>
                    </select>
                  </div>
                  <div className="flex flex-col gap-1">
                    <label className={labelClass}>Skip first rows</label>
                    <input type="number" min={0} className={inputClass} value={options.skip_rows ?? ''} onChange={e => setOpt('skip_rows', e.target.value === '' ? '' : Number(e.target.value))} />
                  </div>
                  <div className="flex flex-col gap-1">
                    <label className={labelClass}>Treat as empty</label>
                    <input className={inputClass} placeholder="NA, -" value={(options.null_values || []).join(', ')}
                      onChange={e => setOpt('null_values', e.target.value ? e.target.value.split(',').map(s => s.trim()).filter(Boolean) : '')} />
                  </div>
                  <label className="col-span-2 flex items-center gap-2 text-[12px] cursor-pointer">
                    <input type="checkbox" className="accent-[#c98f00]" checked={options.has_header !== false} onChange={e => setOpt('has_header', e.target.checked ? '' : false)} /> First row is column names
                  </label>
                  <label className="col-span-2 flex items-center gap-2 text-[12px] cursor-pointer">
                    <input type="checkbox" className="accent-[#c98f00]" checked={options.try_parse_dates !== false} onChange={e => setOpt('try_parse_dates', e.target.checked ? '' : false)} /> Detect dates automatically
                  </label>
                </div>
              )}
            </div>
          )}

          {fmt && (
            <label className="flex items-start gap-2 text-[12px] cursor-pointer">
              <input type="checkbox" className="accent-[#c98f00] mt-[2px]" checked={pin ?? info.snapshot_by_default}
                onChange={e => setPin(e.target.checked)} />
              <span>
                Keep a frozen copy so versions never change, even if the file does
                {info.size_bytes != null && <span className="text-on-surface-variant"> ({(info.size_bytes / 1048576).toFixed(info.size_bytes > 1048576 ? 0 : 1)} MB)</span>}
                {!info.snapshot_by_default && <span className="block text-on-surface-variant">Off by default for files over 2 GB.</span>}
              </span>
            </label>
          )}

          {recipes.length > 0 && (
            <div className="flex flex-col gap-1">
              <label className={labelClass}>Apply a recipe (optional)</label>
              <select className={inputClass} value={recipeId} onChange={e => setRecipeId(e.target.value)}>
                <option value="">No recipe</option>
                {recipes.map(r => <option key={r.id} value={r.id}>{r.name} ({r.operations.length} steps)</option>)}
              </select>
            </div>
          )}

          <button type="submit" disabled={busy || !path.trim()} className="w-full bg-primary-container text-white font-ui-label text-ui-label h-[34px] rounded-DEFAULT hover:bg-primary transition-colors disabled:opacity-60 cursor-pointer border-0">
            {busy ? 'Opening…' : 'Open'}
          </button>
        </form>

        <div className="flex-1 min-w-0 w-full">
          <h3 className="font-panel-title text-panel-title m-0 mb-3">Recent projects</h3>
          {!recent.length && <p className="text-[13px] text-on-surface-variant m-0">Projects you open appear here, with their full step history.</p>}
          <div className="flex flex-col gap-2">
            {recent.slice(0, 20).map(p => (
              <div key={p.id} className="group flex items-center gap-3 bg-white border border-grid-border rounded-lg px-3 py-2 hover:border-primary-container">
                <button className="flex-1 min-w-0 text-left bg-transparent border-0 p-0 cursor-pointer disabled:cursor-not-allowed" disabled={!p.source_exists} onClick={() => onOpenProject(p.id)}>
                  <div className="font-ui-label text-ui-label text-on-surface truncate">{p.name}</div>
                  <div className="text-[11px] text-on-surface-variant truncate font-mono" title={p.source_path}>{p.source_path}</div>
                  <div className="text-[11px] text-on-surface-variant">
                    {p.steps} step{p.steps === 1 ? '' : 's'} · {p.branches} branch{p.branches === 1 ? '' : 'es'}{p.versions ? ` · ${p.versions} version${p.versions === 1 ? '' : 's'}` : ''}{p.pinned ? ' · pinned' : ''} · {new Date(p.created_at).toLocaleString()}
                    {!p.source_exists && <span className="text-error"> · file missing</span>}
                  </div>
                </button>
                <button className="opacity-0 group-hover:opacity-100 focus:opacity-100 bg-transparent border-0 cursor-pointer text-on-surface-variant hover:text-error" title="Delete saved history" onClick={() => removeProject(p)}>
                  <span className="material-symbols-outlined text-[18px]">delete</span>
                </button>
              </div>
            ))}
          </div>
        </div>
      </div>
    </div>
  );
}
