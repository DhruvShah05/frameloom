import React, { useState } from 'react';

export default function OperationMenu({ onApplyOp, schema }) {
  const [opType, setOpType] = useState('drop_column');
  const [params, setParams] = useState({});

  const handleApply = () => {
    let finalParams = { ...params };
    if (opType === 'rename_column' && finalParams.oldCol && finalParams.newName) {
      finalParams.mapping = { [finalParams.oldCol]: finalParams.newName };
    }
    onApplyOp(opType, finalParams);
  };

  const columns = schema ? Object.keys(schema) : [];
  const timeColumns = columns.filter(c => schema[c].toLowerCase().includes('date') || schema[c].toLowerCase().includes('time'));
  const hasTimeOps = timeColumns.length > 0;

  const selectClass = "w-full bg-white border border-grid-border rounded-DEFAULT px-2 h-row-height font-grid-data text-grid-data focus:border-primary-container focus:ring-1 focus:ring-bright-accent outline-none appearance-none bg-[url('data:image/svg+xml;charset=US-ASCII,%3Csvg%20xmlns%3D%22http%3A%2F%2Fwww.w3.org%2F2000%2Fsvg%22%20width%3D%2216%22%20height%3D%2216%22%20viewBox%3D%220%200%2024%2024%22%20fill%3D%22none%22%20stroke%3D%22%23504534%22%20stroke-width%3D%222%22%20stroke-linecap%3D%22round%22%20stroke-linejoin%3D%22round%22%3E%3Cpolyline%20points%3D%226%209%2012%2015%2018%209%22%3E%3C%2Fpolyline%3E%3C%2Fsvg%3E')] bg-no-repeat bg-[position:right_8px_center]";
  const inputClass = "w-full bg-white border border-grid-border rounded-DEFAULT px-2 h-row-height font-grid-data text-grid-data focus:border-primary-container focus:ring-1 focus:ring-bright-accent outline-none";
  const labelClass = "font-ui-small text-ui-small text-on-surface-variant mb-1";
  const groupClass = "flex flex-col gap-1";

  return (
    <div className="p-4 border-b border-grid-border flex flex-col gap-4">
      <h3 className="font-panel-title text-panel-title text-on-surface m-0">Transform</h3>
      
      <div className={groupClass}>
        <label className={labelClass}>Operation</label>
        <select className={selectClass} value={opType} onChange={e => {setOpType(e.target.value); setParams({});}}>
          <optgroup label="Standard">
            <option value="drop_column">Drop Column</option>
            <option value="filter_rows">Filter Rows</option>
            <option value="rename_column">Rename Column</option>
          </optgroup>
          {hasTimeOps && (
            <optgroup label="Time Ops (Domain Specific)">
              <option value="resample">Resample (Time Bucket)</option>
              <option value="rolling">Rolling Window</option>
              <option value="fill_gaps">Fill Gaps</option>
            </optgroup>
          )}
        </select>
      </div>

      {opType === 'drop_column' && (
        <div className={groupClass}>
          <label className={labelClass}>Column</label>
          <select className={selectClass} value={params.columns || ""} onChange={e => setParams({...params, columns: e.target.value})}>
            <option value="">Select column...</option>
            {columns.map(c => <option key={c} value={c}>{c}</option>)}
          </select>
        </div>
      )}

      {opType === 'filter_rows' && (
        <>
          <div className="flex gap-2">
             <div className={`${groupClass} flex-1`}>
                <label className={labelClass}>Column</label>
                <select className={selectClass} value={params.column || ""} onChange={e => setParams({...params, column: e.target.value})}>
                  <option value="">Select...</option>
                  {columns.map(c => <option key={c} value={c}>{c}</option>)}
                </select>
             </div>
             <div className={`${groupClass} flex-1`}>
                <label className={labelClass}>Condition</label>
                <select className={selectClass} value={params.operator || "eq"} onChange={e => setParams({...params, operator: e.target.value})}>
                  <option value="eq">Equals (==)</option>
                  <option value="ne">Not Equals (!=)</option>
                  <option value="gt">Greater Than (&gt;)</option>
                  <option value="lt">Less Than (&lt;)</option>
                  <option value="ge">Greater or Eq</option>
                  <option value="le">Less or Eq</option>
                  <option value="contains">Contains</option>
                  <option value="is_null">Is Null</option>
                  <option value="is_not_null">Not Null</option>
                </select>
             </div>
          </div>
          {!["is_null", "is_not_null"].includes(params.operator || "eq") && (
            <div className={groupClass}>
              <label className={labelClass}>Value</label>
              <input 
                className={inputClass}
                type={params.column && schema && schema[params.column] && (schema[params.column].toLowerCase().includes('int') || schema[params.column].toLowerCase().includes('float') || schema[params.column].toLowerCase().includes('numeric')) ? "number" : "text"}
                value={params.value || ""} 
                onChange={e => setParams({...params, value: e.target.value})} 
              />
            </div>
          )}
        </>
      )}

      {opType === 'rename_column' && (
        <>
          <div className={groupClass}>
            <label className={labelClass}>Column to Rename</label>
            <select className={selectClass} value={params.oldCol || ""} onChange={e => setParams({...params, oldCol: e.target.value})}>
              <option value="">Select column...</option>
              {columns.map(c => <option key={c} value={c}>{c}</option>)}
            </select>
          </div>
          <div className={groupClass}>
            <label className={labelClass}>New Name</label>
            <input 
              className={inputClass}
              type="text" 
              value={params.newName || ""} 
              onChange={e => setParams({...params, newName: e.target.value})} 
            />
          </div>
        </>
      )}

      {opType === 'resample' && (
        <>
          <div className={groupClass}>
            <label className={labelClass}>Time Column</label>
            <select className={selectClass} value={params.time_col || ""} onChange={e => setParams({...params, time_col: e.target.value})}>
              <option value="">Select time column...</option>
              {timeColumns.map(c => <option key={c} value={c}>{c}</option>)}
            </select>
          </div>
          <div className={groupClass}>
            <label className={labelClass}>Frequency (e.g. 1d, 1w)</label>
            <input className={inputClass} type="text" value={params.freq || ""} onChange={e => setParams({...params, freq: e.target.value})} />
          </div>
          <div className={groupClass}>
            <label className={labelClass}>Value Column to Aggregate</label>
            <select className={selectClass} value={params.val_col || ""} onChange={e => setParams({...params, val_col: e.target.value})}>
              <option value="">Select column...</option>
              {columns.filter(c => !timeColumns.includes(c)).map(c => <option key={c} value={c}>{c}</option>)}
            </select>
          </div>
          <div className={groupClass}>
            <label className={labelClass}>Aggregation Function</label>
            <select className={selectClass} value={params.agg || ""} onChange={e => setParams({...params, agg: e.target.value})}>
              <option value="">Select...</option>
              <option value="mean">Mean</option>
              <option value="sum">Sum</option>
              <option value="max">Max</option>
            </select>
          </div>
        </>
      )}

      {opType === 'rolling' && (
        <>
          <div className={groupClass}>
            <label className={labelClass}>Time Column (for sorting index)</label>
            <select className={selectClass} value={params.time_col || ""} onChange={e => setParams({...params, time_col: e.target.value})}>
              <option value="">Select time column...</option>
              {timeColumns.map(c => <option key={c} value={c}>{c}</option>)}
            </select>
          </div>
          <div className={groupClass}>
            <label className={labelClass}>Window (e.g. 3d)</label>
            <input className={inputClass} type="text" value={params.window || ""} onChange={e => setParams({...params, window: e.target.value})} />
          </div>
          <div className={groupClass}>
            <label className={labelClass}>Value Column</label>
            <select className={selectClass} value={params.val_col || ""} onChange={e => setParams({...params, val_col: e.target.value})}>
              <option value="">Select column...</option>
              {columns.filter(c => !timeColumns.includes(c)).map(c => <option key={c} value={c}>{c}</option>)}
            </select>
          </div>
          <div className={groupClass}>
            <label className={labelClass}>Aggregation Function</label>
            <select className={selectClass} value={params.agg || ""} onChange={e => setParams({...params, agg: e.target.value})}>
              <option value="">Select...</option>
              <option value="mean">Mean</option>
              <option value="sum">Sum</option>
              <option value="max">Max</option>
            </select>
          </div>
        </>
      )}

      {opType === 'fill_gaps' && (
        <>
          <div className={groupClass}>
            <label className={labelClass}>Time Column (for sorting)</label>
            <select className={selectClass} value={params.time_col || ""} onChange={e => setParams({...params, time_col: e.target.value})}>
              <option value="">Select time column...</option>
              {timeColumns.map(c => <option key={c} value={c}>{c}</option>)}
            </select>
          </div>
          <div className={groupClass}>
            <label className={labelClass}>Value Column to Fill</label>
            <select className={selectClass} value={params.val_col || ""} onChange={e => setParams({...params, val_col: e.target.value})}>
              <option value="">Select column...</option>
              {columns.filter(c => !timeColumns.includes(c)).map(c => <option key={c} value={c}>{c}</option>)}
            </select>
          </div>
          <div className={groupClass}>
            <label className={labelClass}>Strategy</label>
            <select className={selectClass} value={params.method || ""} onChange={e => setParams({...params, method: e.target.value})}>
              <option value="">Select...</option>
              <option value="forward">Forward Fill</option>
              <option value="backward">Backward Fill</option>
            </select>
          </div>
        </>
      )}

      <button className="mt-2 w-full bg-primary-container text-white font-ui-label text-ui-label h-row-height rounded-DEFAULT hover:bg-primary transition-colors flex items-center justify-center gap-1" onClick={handleApply}>
        Apply
      </button>
      
      {hasTimeOps && (
        <div className="mt-2 text-xs text-primary-container flex items-center gap-1">
          <span className="material-symbols-outlined text-[14px]">schedule</span> Time Ops Unlocked
        </div>
      )}
    </div>
  );
}
