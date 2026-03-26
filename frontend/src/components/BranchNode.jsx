import React from 'react';
import { Handle, Position } from '@xyflow/react';

export default function BranchNode({ data, selected }) {
  const isHead = data.isHead;
  const branches = data.branches || [];

  const isActive = selected || isHead;
  
  const dotClasses = isActive
    ? "w-4 h-4 rounded-full bg-secondary-container border-2 border-primary-container z-10 shadow-[0_0_8px_rgba(253,196,22,0.6)] transition-transform group-hover:scale-110"
    : "w-4 h-4 rounded-full bg-surface-container-lowest border-2 border-primary-container z-10 transition-transform group-hover:scale-110";

  const boxClasses = isActive
    ? "absolute top-6 w-48 bg-surface-container-lowest border border-primary-container p-2 rounded shadow-sm z-20"
    : "absolute top-6 w-48 bg-surface-container-lowest border border-outline-variant p-2 rounded shadow-sm opacity-80 group-hover:opacity-100 transition-opacity z-20";

  return (
    <div className="flex flex-col items-center group cursor-pointer relative" style={{ width: 16, height: 16 }}>
      {/* We center the handles directly on the 16x16 node core */}
      <Handle type="target" position={Position.Left} style={{ opacity: 0 }} />
      
      <div className={dotClasses}></div>
      
      <div className={boxClasses} style={{ left: '50%', transform: 'translateX(-50%)' }}>
        <div className="flex justify-between items-start mb-1">
          {/* Empty span to maintain spacing since no timestamp */}
          <span className="font-ui-small text-ui-small text-on-surface-variant"></span>
          
          {branches.length > 0 && (
            <div className="flex gap-1 flex-wrap justify-end">
              {branches.map(branch => (
                <span key={branch} className={isActive 
                  ? "px-1.5 py-0.5 border border-primary-container text-primary-container rounded-full text-[9px] uppercase tracking-wider font-semibold"
                  : "px-1.5 py-0.5 bg-surface-variant text-on-surface-variant rounded-full text-[9px] uppercase tracking-wider font-semibold"
                }>
                  {branch}
                </span>
              ))}
            </div>
          )}
        </div>
        <p className={`font-grid-data text-grid-data text-on-surface truncate ${isActive ? 'font-medium' : ''}`} title={data.note || data.label}>
          {data.label}
        </p>
        {data.versions?.length > 0 && (
          <div className="flex gap-1 flex-wrap mt-1">
            {data.versions.map(v => (
              <span key={v} className="px-1.5 py-0.5 rounded-full text-[9px] font-semibold bg-secondary-container text-on-secondary-container">🏷 {v}</span>
            ))}
          </div>
        )}
      </div>

      <Handle type="source" position={Position.Right} style={{ opacity: 0 }} />
    </div>
  );
}
