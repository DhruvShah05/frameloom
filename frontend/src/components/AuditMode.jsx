import React, { useState, useMemo } from 'react';
import { AgGridReact } from 'ag-grid-react';

export default function AuditMode({ project, onRunAudit, auditData, auditSchema }) {
  const [nodeA, setNodeA] = useState('');
  const [nodeB, setNodeB] = useState('');
  const [keyColumn, setKeyColumn] = useState('');

  const headToBranches = {};
  if (project?.branches) {
    Object.entries(project.branches).forEach(([bName, b]) => {
      if (!headToBranches[b.head_node_id]) headToBranches[b.head_node_id] = [];
      headToBranches[b.head_node_id].push(bName);
    });
  }

  const nodesList = Object.values(project?.nodes || {}).map(n => {
    const branches = headToBranches[n.id] ? ` [${headToBranches[n.id].join(', ')}]` : '';
    const typeStr = n.operation.label !== n.operation.type ? ` (${n.operation.type})` : '';
    return {
      id: n.id,
      label: `${n.operation.label}${typeStr}${branches} - ${n.id.substring(0,6)}`
    };
  });

  const getRowStyle = (params) => {
    if (!params.data) return null;
    const status = params.data._audit_status;
    if (status === 'missing_in_b' || status === 'missing_in_a') {
      return { background: 'var(--color-audit-missing)' };
    }
    if (status === 'mismatch') {
      return { background: 'var(--color-audit-mismatch)' };
    }
    if (status === 'match') {
      return { background: 'rgba(232, 245, 233, 0.5)' }; // audit-match/50
    }
    return null;
  };

  const colDefs = useMemo(() => {
    if (!auditSchema || !auditData) return [];

    const keys = Object.keys(auditSchema).filter(k => k !== '_audit_status' && k !== '_mismatched_columns');
    
    // Find base columns and key column
    const aCols = keys.filter(k => k.endsWith('_a')).map(k => k.slice(0, -2));
    const mainKeyCol = keys.find(k => !k.endsWith('_a') && !k.endsWith('_b'));

    const defs = [
      {
        headerName: '',
        valueGetter: 'node.rowIndex + 1',
        width: 50,
        pinned: 'left',
        cellClass: (params) => {
           const status = params.data?._audit_status;
           let baseClass = 'row-number-cell border-r-2 !border-[var(--color-surface-dim)] flex items-center justify-center font-bold ';
           if (status === 'mismatch' || status === 'missing_in_a' || status === 'missing_in_b') {
              return baseClass + 'text-on-surface';
           }
           return baseClass + 'text-outline text-[10px]';
        }
      }
    ];

    if (mainKeyCol) {
      defs.push({
        field: mainKeyCol,
        headerName: mainKeyCol,
        width: 150,
        cellClass: 'font-mono'
      });
    }

    aCols.forEach(col => {
      defs.push({
        field: `${col}_a`,
        headerName: `${col} (A)`,
        width: 200,
        cellRenderer: (params) => {
          if (params.data?._audit_status === 'missing_in_a' || params.value == null) {
            return <div className="w-full h-full text-outline italic bg-surface-container-lowest/50 border-x border-dashed border-error/30 flex items-center px-2 -mx-2">null</div>;
          }
          return params.value;
        }
      });
      defs.push({
        field: `${col}_b`,
        headerName: `${col} (B)`,
        width: 200,
        cellClass: (params) => {
          if (params.data?._mismatched_columns?.includes(col)) {
             return 'border-l-2 !border-[var(--color-secondary-container)] bg-[var(--color-audit-mismatch)] font-bold';
          }
          return '';
        },
        cellRenderer: (params) => {
          if (params.data?._audit_status === 'missing_in_b' || params.value == null) {
            return <div className="w-full h-full text-outline italic bg-surface-container-lowest/50 border-r border-dashed border-error/30 flex items-center px-2 -mr-2">null</div>;
          }
          return params.value;
        }
      });
    });

    // Notes Column
    defs.push({
      headerName: 'Audit Notes',
      flex: 1,
      minWidth: 200,
      cellRenderer: (params) => {
        const status = params.data?._audit_status;
        if (status === 'missing_in_b') {
           return (
             <div className="flex items-center h-full">
               <span className="px-2 py-0.5 bg-error text-on-error rounded-sm text-[10px] font-bold tracking-wide uppercase leading-none">Missing in B</span>
             </div>
           );
        }
        if (status === 'missing_in_a') {
           return (
             <div className="flex items-center h-full">
               <span className="px-2 py-0.5 bg-error text-on-error rounded-sm text-[10px] font-bold tracking-wide uppercase leading-none">Missing in A</span>
             </div>
           );
        }
        if (status === 'mismatch') {
           const count = params.data._mismatched_columns?.length || 1;
           return (
             <div className="flex items-center h-full text-secondary font-medium text-[11px] gap-1">
               <span className="material-symbols-outlined text-[14px]">warning</span>
               Value discrepancy ({count} column{count > 1 ? 's' : ''})
             </div>
           );
        }
        if (status === 'match') {
           return (
             <div className="flex items-center h-full text-tertiary-container font-medium text-[11px] gap-1">
               <span className="material-symbols-outlined text-[14px]">check_circle</span>
               Verified exact match
             </div>
           );
        }
        return null;
      }
    });

    return defs;
  }, [auditSchema, auditData]);

  return (
    <div className="flex-1 flex flex-col min-w-0 bg-surface-container-lowest relative z-0 h-full">
      {/* Audit Setup Bar */}
      <div className="h-[48px] bg-[#F7F7F2] border-b border-surface-dim flex items-center px-4 gap-4 shrink-0 font-ui-small text-ui-small">
        <div className="flex items-center gap-2">
          <span className="text-on-surface-variant font-medium">Dataset A:</span>
          <div className="bg-surface-container-lowest border border-surface-dim rounded flex items-center">
            <span className="material-symbols-outlined text-[14px] text-tertiary ml-2">data_object</span>
            <select 
              value={nodeA} 
              onChange={e => setNodeA(e.target.value)}
              className="bg-transparent border-none outline-none py-1 pr-2 text-sm font-mono cursor-pointer"
            >
              <option value="">Select Base Node...</option>
              {nodesList.map(n => <option key={n.id} value={n.id}>{n.label}</option>)}
            </select>
          </div>
        </div>

        <span className="material-symbols-outlined text-[16px] text-outline">compare_arrows</span>
        
        <div className="flex items-center gap-2">
          <span className="text-on-surface-variant font-medium">Dataset B:</span>
          <div className="bg-surface-container-lowest border border-surface-dim rounded flex items-center">
            <span className="material-symbols-outlined text-[14px] text-tertiary ml-2">data_object</span>
            <select 
              value={nodeB} 
              onChange={e => setNodeB(e.target.value)}
              className="bg-transparent border-none outline-none py-1 pr-2 text-sm font-mono cursor-pointer"
            >
              <option value="">Select Compare Node...</option>
              {nodesList.map(n => <option key={n.id} value={n.id}>{n.label}</option>)}
            </select>
          </div>
        </div>
        
        <div className="h-4 w-px bg-surface-variant mx-2"></div>
        
        <div className="flex items-center gap-2">
          <span className="text-on-surface-variant font-medium">Match on:</span>
          <div className="bg-surface-container-lowest border border-surface-dim rounded flex items-center">
            <span className="material-symbols-outlined text-[14px] text-tertiary ml-2">key</span>
            <input 
              type="text" 
              value={keyColumn} 
              onChange={e => setKeyColumn(e.target.value)} 
              placeholder="e.g. id" 
              className="bg-transparent border-none outline-none py-1 px-2 text-sm font-mono w-[120px]"
            />
          </div>
        </div>
        
        <div className="flex-1"></div>
        
        <button 
          onClick={() => onRunAudit(nodeA, nodeB, keyColumn)}
          className="bg-primary-container text-on-primary h-7 px-4 rounded text-ui-small font-medium hover:bg-primary transition-colors flex items-center gap-1 cursor-pointer"
        >
          <span className="material-symbols-outlined text-[14px]">play_arrow</span>
          Run Audit
        </button>
      </div>
      
      {/* Legend Row */}
      <div className="h-[36px] bg-surface-container-lowest border-b border-surface-dim flex items-center px-4 gap-6 shrink-0 font-ui-small text-ui-small">
        <span className="text-on-surface-variant font-medium">Audit Results:</span>
        <div className="flex items-center gap-2">
          <div className="w-3 h-3 rounded-sm bg-audit-match border border-surface-dim"></div>
          <span>Match (Default)</span>
        </div>
        <div className="flex items-center gap-2">
          <div className="w-3 h-3 rounded-sm bg-audit-mismatch border border-surface-dim"></div>
          <span>Mismatch</span>
        </div>
        <div className="flex items-center gap-2">
          <div className="w-3 h-3 rounded-sm bg-audit-missing border border-surface-dim"></div>
          <span>Missing</span>
        </div>
      </div>
      
      {/* Data Grid Wrapper */}
      <div className="flex-1 overflow-auto bg-surface-container-lowest ag-theme-alpine ag-theme-loom">
        {auditData && auditData.length > 0 ? (
          <AgGridReact
            rowData={auditData}
            columnDefs={colDefs}
            rowHeight={30}
            headerHeight={30}
            getRowStyle={getRowStyle}
            animateRows={true}
          />
        ) : (
          <div className="flex h-full items-center justify-center text-on-surface-variant font-ui-label">
            Run an audit to see results
          </div>
        )}
      </div>

      {/* Floating Action Button (FAB) */}
      <button className="absolute bottom-6 right-6 h-12 px-6 bg-primary-container text-on-primary rounded-full shadow-lg hover:bg-primary transition-all flex items-center gap-2 font-ui-label text-ui-label font-bold z-30 ring-4 ring-surface-container-lowest border border-primary cursor-pointer">
        <span className="material-symbols-outlined">code_blocks</span>
        Generate Discrepancy Code
      </button>
    </div>
  );
}
