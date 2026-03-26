import React, { useEffect, useMemo, useState } from 'react';
import { OP_GROUPS, OPS_BY_TYPE, FILTER_OPERATORS, NO_VALUE_OPERATORS, AGG_OPTIONS, buildParams, paramsToForm } from '../opsConfig';
import { isNumericType, isTextType, isDateType } from '../api';

const selectClass = "w-full bg-white border border-grid-border rounded-DEFAULT px-2 h-row-height font-grid-data text-grid-data focus:border-primary-container focus:ring-1 focus:ring-bright-accent outline-none";
const inputClass = selectClass;
const labelClass = "font-ui-small text-ui-small text-on-surface-variant";
const smallBtn = "text-[11px] text-primary hover:underline cursor-pointer bg-transparent border-0 p-0";

function columnsOfType(schema, types) {
  const cols = Object.keys(schema || {});
  if (!types) return cols;
  const test = { numeric: isNumericType, text: isTextType, date: isDateType }[types];
  return cols.filter(c => test(schema[c]));
}

function ColumnSelect({ value, onChange, columns, optional, placeholder = 'Select column…' }) {
  return (
    <select className={selectClass} value={value || ''} onChange={e => onChange(e.target.value || undefined)}>
      <option value="">{optional ? '(none)' : placeholder}</option>
      {columns.map(c => <option key={c} value={c}>{c}</option>)}
    </select>
  );
}

function MultiColumnSelect({ value = [], onChange, columns }) {
  const [q, setQ] = useState('');
  const shown = columns.filter(c => c.toLowerCase().includes(q.toLowerCase()));
  const toggle = c => onChange(value.includes(c) ? value.filter(v => v !== c) : [...value, c]);
  return (
    <div className="border border-grid-border rounded-DEFAULT bg-white">
      {columns.length > 8 && (
        <input className="w-full border-0 border-b border-grid-border px-2 h-[26px] text-[12px] outline-none" placeholder="Search columns…" value={q} onChange={e => setQ(e.target.value)} />
      )}
      <div className="max-h-[132px] overflow-y-auto grid-scroll py-1">
        {shown.map(c => (
          <label key={c} className="flex items-center gap-2 px-2 py-[2px] text-[12px] cursor-pointer hover:bg-highlight-bg">
            <input type="checkbox" checked={value.includes(c)} onChange={() => toggle(c)} className="accent-[#c98f00]" />
            <span className="truncate">{c}</span>
            {value.includes(c) && value.length > 1 && <span className="ml-auto text-[10px] text-on-surface-variant">{value.indexOf(c) + 1}</span>}
          </label>
        ))}
        {!shown.length && <div className="px-2 text-[12px] text-on-surface-variant">No columns</div>}
      </div>
      <div className="flex gap-3 px-2 py-1 border-t border-grid-border">
        <button type="button" className={smallBtn} onClick={() => onChange(shown)}>All</button>
        <button type="button" className={smallBtn} onClick={() => onChange([])}>None</button>
        <span className="ml-auto text-[11px] text-on-surface-variant">{value.length} selected</span>
      </div>
    </div>
  );
}

function ConditionsEditor({ value = [], combine = 'and', onChange, onCombine, schema }) {
  const columns = Object.keys(schema || {});
  const update = (i, patch) => onChange(value.map((c, j) => (j === i ? { ...c, ...patch } : c)));
  return (
    <div className="flex flex-col gap-2">
      {value.map((c, i) => {
        const type = schema?.[c.column];
        const inputType = isNumericType(type) && !['in', 'not_in', 'contains', 'not_contains', 'starts_with', 'ends_with', 'regex'].includes(c.operator) ? 'number' : 'text';
        return (
          <div key={i} className="flex flex-col gap-1 p-2 rounded-DEFAULT border border-grid-border bg-white">
            <div className="flex gap-1">
              <ColumnSelect value={c.column} onChange={v => update(i, { column: v })} columns={columns} placeholder="Column…" />
              <select className={`${selectClass} max-w-[110px]`} value={c.operator || 'eq'} onChange={e => update(i, { operator: e.target.value })}>
                {FILTER_OPERATORS.map(([v, l]) => <option key={v} value={v}>{l}</option>)}
              </select>
            </div>
            {!NO_VALUE_OPERATORS.includes(c.operator) && (
              <div className="flex gap-1 items-center">
                <input className={inputClass} type={inputType} value={c.value ?? ''} onChange={e => update(i, { value: e.target.value })}
                  placeholder={['in', 'not_in'].includes(c.operator) ? 'a, b, c' : isDateType(type) ? 'YYYY-MM-DD' : 'value'} />
                {c.operator === 'between' && (
                  <>
                    <span className="text-[11px] text-on-surface-variant">and</span>
                    <input className={inputClass} type={inputType} value={c.value2 ?? ''} onChange={e => update(i, { value2: e.target.value })} />
                  </>
                )}
              </div>
            )}
            <div className="flex items-center gap-3">
              {['contains', 'not_contains', 'starts_with', 'ends_with'].includes(c.operator) && (
                <label className="flex items-center gap-1 text-[11px] text-on-surface-variant">
                  <input type="checkbox" checked={!!c.ignore_case} onChange={e => update(i, { ignore_case: e.target.checked })} className="accent-[#c98f00]" /> Ignore case
                </label>
              )}
              {value.length > 1 && <button type="button" className={`${smallBtn} ml-auto text-error`} onClick={() => onChange(value.filter((_, j) => j !== i))}>Remove</button>}
            </div>
          </div>
        );
      })}
      <div className="flex items-center gap-2">
        <button type="button" className={smallBtn} onClick={() => onChange([...value, { column: '', operator: 'eq', value: '' }])}>+ Add condition</button>
        {value.length > 1 && (
          <select className={`${selectClass} ml-auto max-w-[150px] h-[26px]`} value={combine} onChange={e => onCombine(e.target.value)}>
            <option value="and">Match all (AND)</option>
            <option value="or">Match any (OR)</option>
          </select>
        )}
      </div>
    </div>
  );
}

function AggsEditor({ value = [], onChange, columns }) {
  const update = (i, patch) => onChange(value.map((a, j) => (j === i ? { ...a, ...patch } : a)));
  return (
    <div className="flex flex-col gap-1">
      {value.map((a, i) => (
        <div key={i} className="flex gap-1 items-center">
          <select className={`${selectClass} max-w-[100px]`} value={a.agg} onChange={e => update(i, { agg: e.target.value })}>
            {AGG_OPTIONS.map(([v, l]) => <option key={v} value={v}>{l}</option>)}
          </select>
          <span className="text-[11px] text-on-surface-variant">of</span>
          <ColumnSelect value={a.column} onChange={v => update(i, { column: v })} columns={columns} optional={a.agg === 'count'} placeholder="column" />
          {value.length > 1 && <button type="button" className={`${smallBtn} text-error`} onClick={() => onChange(value.filter((_, j) => j !== i))} aria-label="Remove">✕</button>}
        </div>
      ))}
      <button type="button" className={`${smallBtn} self-start`} onClick={() => onChange([...value, { column: '', agg: 'sum' }])}>+ Add summary</button>
    </div>
  );
}

function SortEditor({ value = [], onChange, columns }) {
  const update = (i, patch) => onChange(value.map((s, j) => (j === i ? { ...s, ...patch } : s)));
  return (
    <div className="flex flex-col gap-1">
      {value.map((s, i) => (
        <div key={i} className="flex gap-1 items-center">
          <ColumnSelect value={s.column} onChange={v => update(i, { column: v })} columns={columns} />
          <select className={`${selectClass} max-w-[96px]`} value={s.descending ? 'desc' : 'asc'} onChange={e => update(i, { descending: e.target.value === 'desc' })}>
            <option value="asc">A → Z</option>
            <option value="desc">Z → A</option>
          </select>
          {value.length > 1 && <button type="button" className={`${smallBtn} text-error`} onClick={() => onChange(value.filter((_, j) => j !== i))} aria-label="Remove">✕</button>}
        </div>
      ))}
      <button type="button" className={`${smallBtn} self-start`} onClick={() => onChange([...value, { column: '', descending: false }])}>+ Then by</button>
    </div>
  );
}

function FitOnEditor({ value, onChange, columns }) {
  const v = value || {};
  return (
    <div className="flex gap-1 items-center">
      <select className={`${selectClass} max-w-[130px]`} value={v.column || ''} onChange={e => onChange(e.target.value ? { ...v, column: e.target.value } : undefined)}>
        <option value="">All rows</option>
        {columns.map(c => <option key={c} value={c}>{c}</option>)}
      </select>
      {v.column && (
        <>
          <span className="text-[11px] text-on-surface-variant">=</span>
          <input className={inputClass} value={v.value ?? ''} placeholder="train" onChange={e => onChange({ ...v, value: e.target.value })} />
        </>
      )}
    </div>
  );
}

function MultiOptionSelect({ value = [], onChange, options }) {
  const toggle = k => onChange(value.includes(k) ? value.filter(v => v !== k) : [...value, k]);
  return (
    <div className="grid grid-cols-2 gap-x-2 gap-y-[2px] border border-grid-border rounded-DEFAULT bg-white p-2">
      {options.map(([k, label]) => (
        <label key={k} className="flex items-center gap-1 text-[12px] cursor-pointer">
          <input type="checkbox" checked={value.includes(k)} onChange={() => toggle(k)} className="accent-[#c98f00]" /> {label}
        </label>
      ))}
    </div>
  );
}

/**
 * editing: { nodeId, type, params, label } to change an existing step.
 * viewingEarlier: the selected step isn't the branch head, so a new step can
 * either be inserted there (later steps replay) or start a new branch.
 */
export default function OperationMenu({ onApplyOp, onSaveEdit, onCancelEdit, editing, viewingEarlier, schema, busy }) {
  const [opType, setOpType] = useState('filter_rows');
  const [params, setParams] = useState(() => structuredClone(OPS_BY_TYPE.filter_rows.defaults || {}));
  const [insert, setInsert] = useState(true);

  useEffect(() => {
    if (editing && OPS_BY_TYPE[editing.type]) {
      setOpType(editing.type);
      setParams(paramsToForm(OPS_BY_TYPE[editing.type], editing.params));
    }
  }, [editing]);

  const hasTime = useMemo(() => columnsOfType(schema, 'date').length > 0, [schema]);
  const def = OPS_BY_TYPE[opType];
  const set = (key, value) => setParams(p => ({ ...p, [key]: value }));

  const chooseOp = type => {
    setOpType(type);
    setParams(structuredClone(OPS_BY_TYPE[type].defaults || {}));
  };

  const apply = async () => {
    if (editing) {
      await onSaveEdit(buildParams(def, params));
      return;
    }
    const ok = await onApplyOp(opType, buildParams(def, params), { insert: viewingEarlier && insert });
    if (ok && opType === 'filter_rows') {
      setParams(structuredClone(def.defaults));
    }
  };

  const renderField = f => {
    if (f.show && !f.show(params)) return null;
    const cols = columnsOfType(schema, f.types);
    let control;
    switch (f.kind) {
      case 'column':
        control = <ColumnSelect value={params[f.key]} onChange={v => set(f.key, v)} columns={cols} optional={f.optional} />;
        break;
      case 'columns':
        control = <MultiColumnSelect value={params[f.key] || []} onChange={v => set(f.key, v)} columns={cols} />;
        break;
      case 'select':
        control = (
          <select className={selectClass} value={params[f.key] ?? f.options[0][0]} onChange={e => set(f.key, e.target.value)}>
            {f.options.map(([v, l]) => <option key={v} value={v}>{l}</option>)}
          </select>
        );
        break;
      case 'checkbox':
        return (
          <label key={f.key} className="flex items-center gap-2 font-ui-small text-ui-small text-on-surface cursor-pointer">
            <input type="checkbox" checked={!!params[f.key]} onChange={e => set(f.key, e.target.checked)} className="accent-[#c98f00]" />
            {f.label}
          </label>
        );
      case 'textarea':
        control = (
          <textarea className={`${inputClass} h-auto py-1 font-mono text-[12px] resize-y`} rows={f.rows || 3} value={params[f.key] ?? ''}
            placeholder={f.placeholder} spellCheck={false} onChange={e => set(f.key, e.target.value)} />
        );
        break;
      case 'number':
        control = <input className={inputClass} type="number" value={params[f.key] ?? ''} onChange={e => set(f.key, e.target.value === '' ? undefined : Number(e.target.value))} />;
        break;
      case 'conditions':
        control = <ConditionsEditor value={params.conditions} combine={params.combine} onChange={v => set('conditions', v)} onCombine={v => set('combine', v)} schema={schema} />;
        break;
      case 'aggs':
        control = <AggsEditor value={params[f.key]} onChange={v => set(f.key, v)} columns={Object.keys(schema || {})} />;
        break;
      case 'sortby':
        control = <SortEditor value={params[f.key]} onChange={v => set(f.key, v)} columns={Object.keys(schema || {})} />;
        break;
      case 'fit_on':
        control = <FitOnEditor value={params[f.key]} onChange={v => set(f.key, v)} columns={Object.keys(schema || {})} />;
        break;
      case 'multiselect':
        control = <MultiOptionSelect value={params[f.key] || []} onChange={v => set(f.key, v)} options={f.options} />;
        break;
      case 'path':
        control = <input className={`${inputClass} font-mono text-[12px]`} type="text" placeholder="/path/to/other.csv" value={params[f.key] ?? ''} onChange={e => set(f.key, e.target.value)} />;
        break;
      default:
        control = <input className={inputClass} type="text" placeholder={f.placeholder} value={params[f.key] ?? ''} onChange={e => set(f.key, e.target.value)} />;
    }
    return (
      <div key={f.key} className="flex flex-col gap-1">
        <label className={labelClass}>{f.label}</label>
        {control}
      </div>
    );
  };

  return (
    <div className={`p-4 border-b border-grid-border flex flex-col gap-3 ${editing ? 'bg-highlight-bg' : ''}`}>
      <div className="flex items-center gap-2">
        <h3 className="font-panel-title text-panel-title text-on-surface m-0">{editing ? 'Edit step' : 'Transform'}</h3>
        {editing && <button type="button" className={`${smallBtn} ml-auto`} onClick={onCancelEdit}>Cancel</button>}
      </div>
      {editing && <p className="m-0 text-[11px] text-on-surface-variant">Changing “{editing.label}”. Later steps on this branch will replay on top; undo restores the old history.</p>}
      <div className="flex flex-col gap-1">
        <label className={labelClass}>Operation</label>
        <select className={selectClass} value={opType} disabled={!!editing} onChange={e => chooseOp(e.target.value)}>
          {OP_GROUPS.filter(g => !g.timeOnly || hasTime).map(g => (
            <optgroup key={g.name} label={g.name}>
              {g.ops.map(o => <option key={o.type} value={o.type}>{o.label}</option>)}
            </optgroup>
          ))}
        </select>
      </div>

      {def.fields.map(renderField)}
      {def.hint && <p className="m-0 text-[11px] text-on-surface-variant">{def.hint}</p>}

      {viewingEarlier && !editing && (
        <div className="flex flex-col gap-1 text-[12px] rounded-DEFAULT border border-bright-accent bg-highlight-bg p-2">
          <span className="text-on-surface-variant">You're viewing an earlier step.</span>
          <label className="flex items-center gap-2 cursor-pointer"><input type="radio" checked={insert} onChange={() => setInsert(true)} className="accent-[#c98f00]" /> Insert here (later steps replay on top)</label>
          <label className="flex items-center gap-2 cursor-pointer"><input type="radio" checked={!insert} onChange={() => setInsert(false)} className="accent-[#c98f00]" /> Start a new branch from here</label>
        </div>
      )}

      <button disabled={busy} className="mt-1 w-full bg-primary-container text-white font-ui-label text-ui-label h-row-height rounded-DEFAULT hover:bg-primary transition-colors flex items-center justify-center gap-1 disabled:opacity-60 disabled:cursor-wait" onClick={apply}>
        {busy ? 'Working…' : editing ? 'Save changes' : viewingEarlier && insert ? 'Insert step' : 'Apply'}
      </button>
    </div>
  );
}
