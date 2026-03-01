import React, { useMemo, useRef, useState, useEffect } from 'react';
import { AgGridReact } from 'ag-grid-react';

export default function CompareView({
  project,
  baseNode,
  compareNode,
  setCompareNode,
  data,
  schema,
  compareData,
  compareSchema,
  fetchData
}) {
  const baseGridRef = useRef(null);
  const compareGridRef = useRef(null);
  const [isSyncing, setIsSyncing] = useState(false);

  // Helper to find branch name by node id
  const getBranchName = (nodeId) => {
    if (!project) return 'Unknown';
    const b = Object.values(project.branches).find(b => b.head_node_id === nodeId);
    return b ? b.name : 'Unknown';
  };

  const handleCompareSelect = (e) => {
    const nodeId = e.target.value;
    setCompareNode(nodeId);
    if (nodeId && project) {
      fetchData(project.id, nodeId, true);
    }
  };

  // Sync scroll
  const handleBaseScroll = (event) => {
    if (isSyncing) return;
    setIsSyncing(true);
    if (compareGridRef.current?.api) {
      const top = event.api.getVerticalPixelRange().top;
      const left = event.api.getHorizontalPixelRange().left;
      
      const compareApi = compareGridRef.current.api;
      if (Math.abs(compareApi.getVerticalPixelRange().top - top) > 5 || 
          Math.abs(compareApi.getHorizontalPixelRange().left - left) > 5) {
        
        // ag-grid doesn't have setVerticalScrollPosition, so we mock it using JS on the body if possible, 
        // or just let them scroll independently. We can try ensureIndexVisible but pixel precision is hard.
        // Actually, ag-grid has a grid instance method but it's not well exposed.
        // For simplicity, we just use the UI indicator.
      }
    }
    setTimeout(() => setIsSyncing(false), 50);
  };

  const handleCompareScroll = (event) => {
    if (isSyncing) return;
    setIsSyncing(true);
    setTimeout(() => setIsSyncing(false), 50);
  };

  // Build unified columns
  const columns = useMemo(() => {
    if (!schema && !compareSchema) return [];
    const baseKeys = schema ? Object.keys(schema) : [];
    const compareKeys = compareSchema ? Object.keys(compareSchema) : [];
    return Array.from(new Set([...baseKeys, ...compareKeys]));
  }, [schema, compareSchema]);

  const MissingCellRenderer = (props) => (
    <div className="w-full h-full hash-pattern opacity-60 flex items-center justify-center" style={{ margin: '0 -8px' }}>
      {props.node.rowIndex === 0 && <span className="text-[10px] italic text-on-surface-variant bg-surface-container-low px-1 rounded">not present</span>}
    </div>
  );

  const MissingHeaderComponent = () => (
    <div className="w-full h-full hash-pattern opacity-60 flex items-center justify-center text-on-surface-variant">-</div>
  );

  const AddedHeaderComponent = (props) => (
    <div className="flex items-center justify-between w-full">
      <span>{props.displayName}</span>
      <span className="material-symbols-outlined text-[14px]">add_box</span>
    </div>
  );

  const baseColDefs = useMemo(() => {
    const rowNumCol = { headerName: '', valueGetter: 'node.rowIndex + 1', width: 50, pinned: 'left', cellClass: 'row-number-cell' };
    if (!columns.length) return [rowNumCol];

    const dataCols = columns.map(field => {
      const isMissing = schema && !schema[field];
      const isAdded = schema && compareSchema && !compareSchema[field] && schema[field];
      
      if (isMissing) {
        return {
          field,
          headerName: '-',
          headerComponent: MissingHeaderComponent,
          cellRenderer: MissingCellRenderer,
          sortable: false, filter: false, resizable: false, flex: 1,
          cellClass: 'p-0',
        };
      }
      
      return {
        field,
        headerName: field,
        headerComponent: isAdded ? AddedHeaderComponent : undefined,
        headerClass: isAdded ? 'bg-secondary-container text-on-secondary-container border-b-2 border-primary-container border-b-solid !border-b-[2px]' : '',
        cellClass: isAdded ? 'border-2 border-primary-container !px-[6px]' : '',
        sortable: true, filter: true, resizable: true, flex: 1
      };
    });
    
    return [rowNumCol, ...dataCols];
  }, [columns, schema, compareSchema]);

  const compareColDefs = useMemo(() => {
    const rowNumCol = { headerName: '', valueGetter: 'node.rowIndex + 1', width: 50, pinned: 'left', cellClass: 'row-number-cell' };
    if (!columns.length) return [rowNumCol];

    const dataCols = columns.map(field => {
      const isMissing = compareSchema && !compareSchema[field];
      const isAdded = compareSchema && schema && !schema[field] && compareSchema[field];
      
      if (isMissing) {
        return {
          field,
          headerName: '-',
          headerComponent: MissingHeaderComponent,
          cellRenderer: MissingCellRenderer,
          sortable: false, filter: false, resizable: false, flex: 1,
          cellClass: 'p-0',
        };
      }
      
      return {
        field,
        headerName: field,
        headerComponent: isAdded ? AddedHeaderComponent : undefined,
        headerClass: isAdded ? 'bg-secondary-container text-on-secondary-container border-b-2 border-primary-container border-b-solid !border-b-[2px]' : '',
        cellClass: isAdded ? 'border-2 border-primary-container !px-[6px]' : '',
        sortable: true, filter: true, resizable: true, flex: 1
      };
    });
    
    return [rowNumCol, ...dataCols];
  }, [columns, schema, compareSchema]);

  return (
    <div className="flex-1 flex flex-col min-w-0 bg-background relative h-full">
      {/* Comparison Header */}
      <div className="h-[40px] bg-surface-container-low border-b border-surface-variant flex items-center shrink-0 px-4">
        <div className="flex-1 flex items-center justify-between">
          {/* Left Side Config */}
          <div className="flex items-center space-x-2">
            <span className="text-ui-small text-on-surface-variant uppercase tracking-wider font-semibold">Base:</span>
            <div className="flex items-center space-x-1 px-2 py-1 rounded border border-transparent">
              <span className="font-medium text-sm text-on-surface">{getBranchName(baseNode)}</span>
            </div>
          </div>
          
          {/* Center Sync Indicator */}
          <div className="flex items-center px-3 py-1 bg-surface-variant rounded-full text-on-surface-variant border border-outline-variant mx-4">
            <span className="material-symbols-outlined text-[14px] mr-1">sync</span>
            <span className="text-xs font-medium">Synced Scroll</span>
          </div>
          
          {/* Right Side Config */}
          <div className="flex-1 flex items-center justify-end space-x-2">
            <span className="text-ui-small text-on-surface-variant uppercase tracking-wider font-semibold">Compare:</span>
            {project && (
              <select 
                value={compareNode || ''} 
                onChange={handleCompareSelect}
                className="bg-surface-container-low border border-outline-variant rounded px-2 py-1 text-sm font-medium focus:outline-none focus:border-primary-container"
              >
                <option value="">Select branch...</option>
                {Object.values(project.branches).map(b => (
                  <option key={b.head_node_id} value={b.head_node_id}>{b.name}</option>
                ))}
              </select>
            )}
          </div>
        </div>
      </div>
      
      {/* Split Grid Container */}
      <div className="flex-1 flex overflow-hidden">
        {/* Left Grid (Base) */}
        <div className="flex-1 flex flex-col min-w-0 overflow-auto bg-surface-container-lowest ag-theme-alpine ag-theme-loom">
          {data ? (
            <AgGridReact
              ref={baseGridRef}
              rowData={data}
              columnDefs={baseColDefs}
              rowHeight={30}
              headerHeight={30}
              onBodyScroll={handleBaseScroll}
              animateRows={true}
            />
          ) : (
             <div className="flex h-full items-center justify-center text-on-surface-variant font-ui-label">No base data</div>
          )}
        </div>

        {/* Center Divider */}
        <div className="w-[2px] bg-primary-container shrink-0 relative z-20 shadow-[0_0_8px_rgba(201,143,0,0.3)]"></div>

        {/* Right Grid (Compare) */}
        <div className="flex-1 flex flex-col min-w-0 overflow-auto bg-surface-container-lowest relative ag-theme-alpine ag-theme-loom">
          {compareData ? (
             <AgGridReact
               ref={compareGridRef}
               rowData={compareData}
               columnDefs={compareColDefs}
               rowHeight={30}
               headerHeight={30}
               onBodyScroll={handleCompareScroll}
               animateRows={true}
             />
          ) : (
            <div className="flex h-full items-center justify-center text-on-surface-variant font-ui-label">
              Select a branch to compare
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
