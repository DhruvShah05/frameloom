import React from 'react';

export default function ProjectSidebar() {
  return (
    <aside className="bg-surface-container-low border-r border-surface-variant h-full w-[260px] flex flex-col shrink-0">
      <div className="p-4 border-b border-surface-variant">
        <h2 className="font-panel-title text-panel-title font-bold text-primary">Project Workspace</h2>
        <p className="font-ui-small text-ui-small text-on-surface-variant mt-1">Data Pipeline Alpha</p>
      </div>
      <div className="flex-1 overflow-y-auto py-2">
        <nav className="px-2 space-y-1">
          <a className="flex items-center gap-3 px-3 py-2 text-on-surface-variant hover:bg-surface-container-high rounded-DEFAULT transition-all duration-150 font-ui-label text-ui-label cursor-pointer">
            <span className="material-symbols-outlined text-[18px]">database</span>
            Datasets
          </a>
          <a className="flex items-center gap-3 px-3 py-2 bg-secondary-container text-on-secondary-container rounded-xl transition-all duration-150 font-ui-label text-ui-label cursor-pointer">
            <span className="material-symbols-outlined fill text-[18px]">alt_route</span>
            Pipelines
          </a>
          <a className="flex items-center gap-3 px-3 py-2 text-on-surface-variant hover:bg-surface-container-high rounded-DEFAULT transition-all duration-150 font-ui-label text-ui-label cursor-pointer">
            <span className="material-symbols-outlined text-[18px]">science</span>
            Experiments
          </a>
          <a className="flex items-center gap-3 px-3 py-2 text-on-surface-variant hover:bg-surface-container-high rounded-DEFAULT transition-all duration-150 font-ui-label text-ui-label cursor-pointer">
            <span className="material-symbols-outlined text-[18px]">description</span>
            Documentation
          </a>
        </nav>
      </div>
    </aside>
  );
}
