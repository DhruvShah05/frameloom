import React, { useMemo, useRef } from 'react';
import { ReactFlow, Background } from '@xyflow/react';
import '@xyflow/react/dist/style.css';
import dagre from 'dagre';
import BranchNode from './BranchNode';

const nodeTypes = {
  customBranchNode: BranchNode,
};

const nodeWidth = 200; // Account for the width of the tooltip box
const nodeHeight = 80;

const getLayoutedElements = (nodes, edges, direction = 'LR') => {
  const dagreGraph = new dagre.graphlib.Graph();
  dagreGraph.setDefaultEdgeLabel(() => ({}));
  dagreGraph.setGraph({ rankdir: direction, nodesep: 60, ranksep: 100 });

  nodes.forEach((node) => {
    dagreGraph.setNode(node.id, { width: nodeWidth, height: nodeHeight });
  });

  edges.forEach((edge) => {
    dagreGraph.setEdge(edge.source, edge.target);
  });

  dagre.layout(dagreGraph);

  const layoutedNodes = nodes.map((node) => {
    const nodeWithPosition = dagreGraph.node(node.id);
    return {
      ...node,
      targetPosition: 'left',
      sourcePosition: 'right',
      position: {
        x: nodeWithPosition.x - 8, // Center of the 16px node
        y: nodeWithPosition.y - 8,
      },
    };
  });

  return { nodes: layoutedNodes, edges };
};

export default function BranchTree({ project, activeNode, onNodeClick, onNewBranch, onExport }) {
  const flowRef = useRef(null);
  const { nodes: layoutedNodes, edges: layoutedEdges } = useMemo(() => {
    if (!project || !project.nodes) return { nodes: [], edges: [] };
    
    const headToBranches = {};
    Object.values(project.branches).forEach(b => {
      if (!headToBranches[b.head_node_id]) headToBranches[b.head_node_id] = [];
      headToBranches[b.head_node_id].push(b.name);
    });

    const versionsAt = {};
    Object.values(project.versions || {}).forEach(v => { (versionsAt[v.node_id] ||= []).push(v.name); });

    const rfNodes = Object.values(project.nodes).map(n => {
      const isHead = n.id === activeNode;
      const branches = headToBranches[n.id] || [];
      
      return {
        id: n.id,
        type: 'customBranchNode',
        data: { 
          label: n.operation.label,
          isHead,
          branches,
          versions: versionsAt[n.id] || [],
          note: n.note,
        },
      };
    });

    const rfEdges = [];
    Object.values(project.nodes).forEach(n => {
      if (n.parent_id) {
        rfEdges.push({
          id: `e-${n.parent_id}-${n.id}`,
          source: n.parent_id,
          target: n.id,
          type: 'smoothstep',
          animated: false,
          style: { stroke: '#dcd9d9', strokeWidth: 2, strokeDasharray: '4,4' }
        });
      }
    });

    return getLayoutedElements(rfNodes, rfEdges);
  }, [project, activeNode]);

  return (
    <div className="flex-1 relative overflow-hidden" style={{ background: 'transparent' }}>
      {/* Floating Toolbar */}
      <div className="absolute top-4 right-4 bg-surface-container-lowest border border-outline-variant rounded shadow-sm flex items-center p-1 z-10 gap-1">
        <button onClick={onNewBranch} className="p-1.5 hover:bg-surface-container-high rounded text-on-surface-variant flex items-center justify-center bg-transparent border-0 cursor-pointer" title="New branch from the selected step">
          <span className="material-symbols-outlined text-[16px]">add_circle</span>
        </button>
        <div className="w-px h-4 bg-outline-variant mx-1"></div>
        <button onClick={() => flowRef.current?.fitView({ padding: 0.35 })} className="p-1.5 hover:bg-surface-container-high rounded text-on-surface-variant flex items-center justify-center bg-transparent border-0 cursor-pointer" title="Zoom to fit">
          <span className="material-symbols-outlined text-[16px]">fit_screen</span>
        </button>
        <div className="w-px h-4 bg-outline-variant mx-1"></div>
        <button onClick={onExport} className="p-1.5 hover:bg-surface-container-high rounded text-on-surface-variant flex items-center justify-center bg-transparent border-0 cursor-pointer" title="Export the selected step as code">
          <span className="material-symbols-outlined text-[16px]">download</span>
        </button>
      </div>

      <ReactFlow 
        nodes={layoutedNodes} 
        edges={layoutedEdges} 
        onNodeClick={(_, node) => onNodeClick(node.id)}
        nodeTypes={nodeTypes}
        onInit={inst => { flowRef.current = inst; }}
        fitView
        fitViewOptions={{ padding: 0.35 }}
      >
        <Background variant="dots" size={2} color="#dcd9d9" gap={20} />
      </ReactFlow>
    </div>
  );
}
