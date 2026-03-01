import React, { useMemo } from 'react';
import { AgGridReact } from 'ag-grid-react';
import 'ag-grid-community/styles/ag-grid.css';
import 'ag-grid-community/styles/ag-theme-alpine.css';

export default function DataGrid({ data, schema }) {
  const columnDefs = useMemo(() => {
    if (!schema) return [];
    
    const rowNumCol = {
      headerName: '',
      valueGetter: 'node.rowIndex + 1',
      width: 50,
      minWidth: 50,
      maxWidth: 50,
      pinned: 'left',
      sortable: false,
      filter: false,
      resizable: false,
      cellClass: 'row-number-cell',
      headerClass: 'row-number-header',
    };

    const dataCols = Object.keys(schema).map(field => ({
      field,
      headerName: field, // Clean header name to match design
      sortable: true,
      filter: true,
      resizable: true,
      minWidth: 100, // Reduced minWidth to match denser design
      cellStyle: (params) => {
        if (params.data && params.data._audit_status === 'mismatch' && params.data._mismatched_columns?.includes(field)) {
          return { backgroundColor: 'rgba(234, 179, 8, 0.5)' };
        }
        return null;
      }
    }));
    
    return [rowNumCol, ...dataCols];
  }, [schema]);

  const defaultColDef = useMemo(() => {
    return {
      flex: 1,
      minWidth: 80,
    };
  }, []);

  const getRowStyle = (params) => {
    if (params.data && params.data._audit_status) {
      const status = params.data._audit_status;
      if (status === 'missing_in_b') return { background: 'var(--color-audit-missing)' };
      if (status === 'missing_in_a') return { background: 'var(--color-audit-missing)' };
      if (status === 'mismatch') return { background: 'var(--color-audit-mismatch)' };
    }
    return null;
  };

  if (!data || data.length === 0) {
    return (
      <div className="flex h-full items-center justify-center text-on-surface-variant font-ui-label">
        Load a file to see data
      </div>
    );
  }

  return (
    <div className="ag-theme-alpine ag-theme-loom" style={{ height: '100%', width: '100%' }}>
      <AgGridReact
        rowData={data}
        columnDefs={columnDefs}
        defaultColDef={defaultColDef}
        getRowStyle={getRowStyle}
        rowSelection="multiple"
        animateRows={true}
        rowHeight={36}
        headerHeight={36}
      />
    </div>
  );
}
