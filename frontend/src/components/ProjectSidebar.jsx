import React from 'react';

// Shown beside the non-grid views: what this project is and where you are in it.
export default function ProjectSidebar({ project, activeNode, onSwitchBranch, schema, totalRows }) {
  if (!project) return null;
  const head = project.branches[project.head]?.head_node_id || project.head;
  let steps = 0;
  for (let cur = project.nodes[head]; cur && cur.parent_id; cur = project.nodes[cur.parent_id]) steps += 1;
  return (
    <aside className="bg-surface-container-low border-r border-surface-variant h-full w-[260px] flex flex-col shrink-0">
      <div className="p-4 border-b border-surface-variant">
        <h2 className="m-0 font-panel-title text-panel-title font-bold text-primary truncate" title={project.name}>{project.name || 'Project'}</h2>
        <p className="m-0 mt-1 font-mono text-[11px] text-on-surface-variant break-all">{project.source_path}</p>
        <p className="m-0 mt-2 text-[12px] text-on-surface-variant">
          {steps} step{steps === 1 ? '' : 's'}
          {totalRows != null && <> · {totalRows.toLocaleString()} rows</>}
          {schema && <> · {Object.keys(schema).length} columns</>}
        </p>
        {activeNode !== head && <p className="m-0 mt-2 text-[11px] text-secondary">Viewing an earlier step. New steps start a branch from here.</p>}
      </div>
      <div className="flex-1 overflow-y-auto py-2 grid-scroll">
        <h3 className="m-0 px-4 pb-1 text-[11px] uppercase tracking-wider text-on-surface-variant font-semibold">Branches</h3>
        <nav className="px-2 flex flex-col gap-1">
          {Object.values(project.branches).map(b => (
            <button key={b.name} onClick={() => onSwitchBranch(b.name)}
              className={`flex items-center gap-2 px-3 py-2 rounded-DEFAULT text-left border-0 cursor-pointer font-ui-label text-ui-label ${project.head === b.name ? 'bg-secondary-container text-on-secondary-container' : 'bg-transparent text-on-surface-variant hover:bg-surface-container-high'}`}>
              <span className="material-symbols-outlined text-[16px]">alt_route</span>
              <span className="truncate">{b.name}</span>
              <span className="ml-auto text-[11px] opacity-80 truncate max-w-[90px]">{project.nodes[b.head_node_id]?.operation.label}</span>
            </button>
          ))}
        </nav>
      </div>
    </aside>
  );
}
