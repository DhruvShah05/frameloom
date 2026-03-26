import React, { useCallback, useEffect, useRef, useState } from 'react';
import DataGrid from './components/DataGrid';
import OperationMenu from './components/OperationMenu';
import ProjectSidebar from './components/ProjectSidebar';
import BranchTree from './components/BranchTree';
import AuditMode from './components/AuditMode';
import CompareView, { ROW_LIMIT } from './components/CompareView';
import ProfileView from './components/ProfileView';
import ChartView from './components/ChartView';
import StartScreen from './components/StartScreen';
import VersionsView from './components/VersionsView';
import { api, download } from './api';
import { OPS_BY_TYPE } from './opsConfig';

const VIEWS = [['grid', 'Grid'], ['profile', 'Profile'], ['chart', 'Chart'], ['tree', 'Branch Tree'], ['versions', 'Versions'], ['compare', 'Compare'], ['audit', 'Audit']];
const SAMPLE_CHOICES = [['', 'All rows'], ['head:10000', 'First 10,000 rows'], ['random:10000', 'Random 10,000 rows'], ['head:100000', 'First 100,000 rows'], ['random:100000', 'Random 100,000 rows']];
const EXPORTS = [['csv', 'CSV'], ['xlsx', 'Excel (.xlsx)'], ['parquet', 'Parquet'], ['json', 'JSON'], ['ndjson', 'NDJSON'], ['tsv', 'TSV'], ['ipc', 'Arrow']];

function headNodeId(project) {
  if (!project) return null;
  return project.branches[project.head]?.head_node_id || project.head;
}

function setUrlProject(id) {
  const url = new URL(window.location.href);
  url.search = id ? `?project=${encodeURIComponent(id)}` : '';
  window.history.replaceState(null, '', url);
}

function describeView(view) {
  const parts = [];
  if (view.filters.length) parts.push(`${view.filters.length} filter${view.filters.length > 1 ? 's' : ''}`);
  if (view.sort.length) parts.push(`sorted by ${view.sort.map(s => s.column).join(', ')}`);
  return parts.join(' · ');
}

function App() {
  const [project, setProject] = useState(null);
  const [activeNode, setActiveNode] = useState(null);
  const [schema, setSchema] = useState(null);
  const [totalRows, setTotalRows] = useState(null);
  const [visibleRows, setVisibleRows] = useState(null);
  const [profile, setProfile] = useState(null);
  const [profileLoading, setProfileLoading] = useState(false);
  const [refreshKey, setRefreshKey] = useState(0);
  const [view, setView] = useState({ sort: [], filters: [] });
  const [viewMode, setViewMode] = useState('grid');
  const [busy, setBusy] = useState(false);
  const [notices, setNotices] = useState([]);

  const [compareNode, setCompareNode] = useState(null);
  const [baseData, setBaseData] = useState(null);
  const [compareData, setCompareData] = useState(null);
  const [compareSchema, setCompareSchema] = useState(null);
  const [audit, setAudit] = useState({ data: null, schema: null, summary: null, running: false });

  const [recipes, setRecipes] = useState([]);
  const [recipeWarnings, setRecipeWarnings] = useState(null); // { warnings, retry }
  const [modal, setModal] = useState(null); // 'code' | 'branch' | 'recipe'
  const [code, setCode] = useState('');
  const [exportOpen, setExportOpen] = useState(false);
  const [newBranchName, setNewBranchName] = useState('');
  const [recipeName, setRecipeName] = useState('');
  const [codeTarget, setCodeTarget] = useState('polars');
  const [sourceStatus, setSourceStatus] = useState(null);
  const [editing, setEditing] = useState(null); // { nodeId, type, params, label }
  const [stepMenu, setStepMenu] = useState(null); // node id whose menu is open
  const [pickNode, setPickNode] = useState(null); // node to copy to another branch
  const [versionForm, setVersionForm] = useState({ name: '', message: '' });

  const gridRef = useRef(null);
  const viewingEarlierRef = useRef(false);
  const requestSeq = useRef(0);

  // ---------------------------------------------------------------- notices
  const notify = useCallback((text, kind = 'error') => {
    const id = Math.random().toString(36).slice(2);
    setNotices(n => [...n.slice(-3), { id, text, kind }]);
    setTimeout(() => setNotices(n => n.filter(x => x.id !== id)), kind === 'error' ? 8000 : 3500);
  }, []);

  // ----------------------------------------------------------------- loading
  const fetchRecipes = useCallback(async () => {
    try { setRecipes(await api('/recipes')); } catch { /* server not up yet */ }
  }, []);

  const selectNode = useCallback(async (p, nodeId) => {
    const seq = ++requestSeq.current;
    setActiveNode(nodeId);
    setView({ sort: [], filters: [] });
    setRefreshKey(k => k + 1);
    try {
      const meta = await api(`/project/${p.id}/data`, { query: { node_id: nodeId, limit: 1 } });
      if (seq !== requestSeq.current) return;
      setSchema(meta.schema);
      setTotalRows(meta.total_rows);
      setVisibleRows(null);
    } catch (e) {
      if (seq === requestSeq.current) notify(e.message);
    }
    setProfileLoading(true);
    try {
      const prof = await api(`/project/${p.id}/profile`, { query: { node_id: nodeId } });
      if (seq === requestSeq.current) setProfile(prof);
    } catch (e) {
      if (seq === requestSeq.current) { setProfile(null); notify(`Profile failed: ${e.message}`); }
    } finally {
      if (seq === requestSeq.current) setProfileLoading(false);
    }
  }, [notify]);

  const showProject = useCallback((p, nodeId) => {
    setProject(p);
    setCompareNode(null);
    setCompareData(null);
    setAudit({ data: null, schema: null, summary: null, running: false });
    setUrlProject(p.id);
    setEditing(null);
    setSourceStatus(null);
    api(`/project/${p.id}/source`).then(setSourceStatus).catch(() => {});
    selectNode(p, nodeId || headNodeId(p));
  }, [selectNode]);

  const loadProject = useCallback(async (path, options = {}, recipeId = null, force = false, snapshot = null) => {
    setBusy(true);
    try {
      if (!recipeId) {
        const p = await api('/project/load', { method: 'POST', body: { path, options, snapshot } });
        showProject(p);
      } else {
        const result = await api('/recipe/apply', { method: 'POST', body: { recipe_id: recipeId, new_source_path: path, options, force } });
        if (result.status === 'warning') {
          setRecipeWarnings({ warnings: result.warnings, retry: () => loadProject(path, options, recipeId, true) });
        } else {
          setRecipeWarnings(null);
          showProject(result.project);
          notify(`Recipe applied: ${Object.keys(result.project.nodes).length - 1} steps`, 'info');
        }
      }
    } catch (e) {
      notify(e.message);
    } finally {
      setBusy(false);
    }
  }, [showProject, notify]);

  const openProject = useCallback(async id => {
    try {
      showProject(await api(`/project/${id}/tree`));
    } catch (e) {
      notify(e.message);
      setUrlProject(null);
    }
  }, [showProject, notify]);

  useEffect(() => {
    (async () => {
      let fetched = [];
      try { fetched = await api('/recipes'); setRecipes(fetched); } catch { /* shown on first action */ }
      const params = new URLSearchParams(window.location.search);
      if (params.get('project')) return openProject(params.get('project'));
      const path = params.get('path');
      if (!path) return undefined;
      let options = {};
      try { options = JSON.parse(params.get('options') || '{}'); } catch { /* ignore */ }
      const recipeName = params.get('recipe_name');
      if (recipeName) {
        const matched = fetched.find(r => r.name === recipeName);
        if (!matched) notify(`Recipe '${recipeName}' not found — opening without it.`);
        return loadProject(path, options, matched?.id);
      }
      return loadProject(path, options);
    })();
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const refreshTree = useCallback(async () => {
    if (!project) return null;
    try {
      const p = await api(`/project/${project.id}/tree`);
      setProject(p);
      return p;
    } catch (e) {
      notify(e.message);
      return null;
    }
  }, [project, notify]);

  // -------------------------------------------------------------- operations
  const addStep = useCallback(async (parentId, type, params) => {
    const node = await api(`/project/${project.id}/op`, {
      method: 'POST',
      body: { parent_node_id: parentId, operation: { type, params } },
    });
    return node;
  }, [project]);

  const applyOp = useCallback(async (type, params, { insert = false } = {}) => {
    if (!project || !activeNode || busy) return false;
    setBusy(true);
    try {
      if (insert) {
        // Put the step here and replay the later steps of this branch on top.
        const r = await api(`/project/${project.id}/step/${activeNode}/insert`, { method: 'POST', body: { operation: { type, params } } });
        setProject(r.project);
        selectNode(r.project, r.node_id);
        notify('Step inserted; later steps replayed', 'info');
        return true;
      }
      const node = await addStep(activeNode, type, params);
      const p = await refreshTree();
      if (p) selectNode(p, node.id);
      return true;
    } catch (e) {
      notify(e.message);
      return false;
    } finally {
      setBusy(false);
    }
  }, [project, activeNode, busy, addStep, refreshTree, selectNode, notify]);

  // History rewrites return the new project and the step to show.
  const rewrite = useCallback(async (path, body, message) => {
    if (!project || busy) return false;
    setBusy(true);
    try {
      const r = await api(`/project/${project.id}${path}`, { method: 'POST', body });
      setProject(r.project);
      selectNode(r.project, r.node_id);
      if (message) notify(message, 'info');
      return true;
    } catch (e) {
      notify(e.message);
      return false;
    } finally {
      setBusy(false);
    }
  }, [project, busy, selectNode, notify]);

  const saveEdit = async params => {
    if (await rewrite(`/step/${editing.nodeId}/edit`, { params }, 'Step changed; later steps replayed')) setEditing(null);
  };

  const deleteStep = async node => {
    setStepMenu(null);
    if (!window.confirm(`Remove "${node.operation.label}" and replay the steps after it? (Undo brings it back.)`)) return;
    rewrite(`/step/${node.id}/delete`, {}, 'Step removed; later steps replayed');
  };

  const noteStep = async node => {
    setStepMenu(null);
    const note = window.prompt('Note for this step (why it was done):', node.note || '');
    if (note === null) return;
    try {
      await api(`/project/${project.id}/step/${node.id}/note`, { method: 'POST', body: { note } });
      await refreshTree();
    } catch (e) {
      notify(e.message);
    }
  };

  const copyStepTo = async branch => {
    const node = pickNode;
    setPickNode(null);
    rewrite('/cherry-pick', { node_ids: [node.id], branch }, `Copied "${node.operation.label}" to ${branch}`);
  };

  const mergeBranch = async source => {
    setModal(null);
    rewrite('/merge', { source }, `Merged ${source} into ${project.head}`);
  };

  const deleteBranch = async name => {
    if (!window.confirm(`Delete branch "${name}"? Its steps stay in the history; only the name goes.`)) return;
    try {
      await api(`/project/${project.id}/branch/${encodeURIComponent(name)}`, { method: 'DELETE' });
      await refreshTree();
    } catch (e) {
      notify(e.message);
    }
  };

  const saveVersion = async () => {
    try {
      await api(`/project/${project.id}/versions`, { method: 'POST', body: { name: versionForm.name.trim(), message: versionForm.message, node_id: activeNode } });
      notify(`Saved version "${versionForm.name.trim()}"`, 'info');
      setVersionForm({ name: '', message: '' });
      setModal(null);
      await refreshTree();
    } catch (e) {
      notify(e.message);
    }
  };

  const setSample = async choice => {
    const [method, rows] = choice ? choice.split(':') : ['head', ''];
    try {
      await api(`/project/${project.id}/sample`, { method: 'POST', body: { rows: rows ? Number(rows) : null, method, seed: 0 } });
      const p = await refreshTree();
      if (p) selectNode(p, activeNode);
    } catch (e) {
      notify(e.message);
    }
  };

  const acceptSource = async snapshot => {
    try {
      setSourceStatus(await api(`/project/${project.id}/source/accept`, { method: 'POST', body: { snapshot } }));
      const p = await refreshTree();
      if (p) selectNode(p, activeNode);
      notify(snapshot ? 'The current file is now pinned as the baseline' : 'The current file is now the baseline', 'info');
    } catch (e) {
      notify(e.message);
    }
  };

  const saveViewAsSteps = async () => {
    if (!project || busy) return;
    setBusy(true);
    try {
      let parent = activeNode;
      for (const f of view.filters) {
        const params = f.conditions ? f : { conditions: [f], combine: 'and' };
        parent = (await addStep(parent, 'filter_rows', params)).id;
      }
      if (view.sort.length) parent = (await addStep(parent, 'sort', { by: view.sort })).id;
      gridRef.current?.clearView();
      const p = await refreshTree();
      if (p) selectNode(p, parent);
    } catch (e) {
      notify(e.message);
    } finally {
      setBusy(false);
    }
  };

  const editCell = useCallback((row, column, value) => {
    applyOp('edit_cell', { row, column, value }, { insert: viewingEarlierRef.current });
  }, [applyOp]);

  const undoRedo = useCallback(async which => {
    if (!project || busy) return;
    try {
      const r = await api(`/project/${project.id}/${which}`, { method: 'POST' });
      setProject(r.project);
      selectNode(r.project, r.node_id);
    } catch (e) {
      notify(e.message, e.message.startsWith('Nothing') ? 'info' : 'error');
    }
  }, [project, busy, selectNode, notify]);

  useEffect(() => {
    const onKey = e => {
      if (!(e.metaKey || e.ctrlKey) || !project) return;
      const t = e.target;
      if (t.closest?.('input, textarea, select, [contenteditable="true"], .ag-cell-inline-editing, .ag-floating-filter')) return;
      const key = e.key.toLowerCase();
      if (key === 'z' && !e.shiftKey) { e.preventDefault(); undoRedo('undo'); }
      else if ((key === 'z' && e.shiftKey) || key === 'y') { e.preventDefault(); undoRedo('redo'); }
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [project, undoRedo]);

  // ----------------------------------------------------------------- branches
  const checkoutNode = async (p, nodeId) => {
    const branch = Object.values(p.branches).find(b => b.head_node_id === nodeId);
    try {
      await api(`/project/${p.id}/checkout`, { method: 'POST', body: { target: branch ? branch.name : nodeId } });
    } catch (e) {
      notify(e.message);
    }
  };

  const switchBranch = async name => {
    const branch = project?.branches[name];
    if (!branch) return;
    try {
      await api(`/project/${project.id}/checkout`, { method: 'POST', body: { target: name } });
      const p = await refreshTree();
      if (p) selectNode(p, branch.head_node_id);
      setModal(null);
    } catch (e) {
      notify(e.message);
    }
  };

  const createBranch = async () => {
    if (!newBranchName.trim()) return;
    try {
      await api(`/project/${project.id}/branch`, { method: 'POST', body: { name: newBranchName.trim(), from_node_id: activeNode } });
      setNewBranchName('');
      setModal(null);
      await refreshTree();
      notify(`Branch "${newBranchName.trim()}" created`, 'info');
    } catch (e) {
      notify(e.message);
    }
  };

  // ------------------------------------------------------------------ exports
  const exportCode = async (target = codeTarget) => {
    setExportOpen(false);
    try {
      const d = await api(`/project/${project.id}/export/code`, { method: 'POST', body: { node_id: activeNode, target } });
      setCode(d.code);
      setCodeTarget(target);
      setModal('code');
    } catch (e) {
      notify(e.message);
    }
  };

  const exportData = async format => {
    setExportOpen(false);
    setBusy(true);
    try {
      const name = await download(`/project/${project.id}/export/data`, { node_id: activeNode, format });
      notify(`Downloaded ${name}`, 'info');
    } catch (e) {
      notify(e.message);
    } finally {
      setBusy(false);
    }
  };

  const saveRecipe = async () => {
    if (!recipeName.trim()) return;
    try {
      await api(`/project/${project.id}/recipe/save`, { method: 'POST', body: { name: recipeName.trim(), node_id: activeNode } });
      notify(`Recipe "${recipeName.trim()}" saved`, 'info');
      setRecipeName('');
      setModal(null);
      fetchRecipes();
    } catch (e) {
      notify(e.message);
    }
  };

  // ----------------------------------------------------------------- compare
  const fetchData = useCallback(async (projectId, nodeId, isCompare = false) => {
    try {
      const d = await api(`/project/${projectId}/data`, { query: { node_id: nodeId, limit: ROW_LIMIT } });
      if (isCompare) { setCompareData(d.data); setCompareSchema(d.schema); }
      else setBaseData(d.data);
    } catch (e) {
      notify(e.message);
    }
  }, [notify]);

  useEffect(() => {
    if (viewMode === 'compare' && project && activeNode) fetchData(project.id, activeNode);
  }, [viewMode, project?.id, activeNode, fetchData]); // eslint-disable-line react-hooks/exhaustive-deps

  const runAudit = async (nodeA, nodeB, keyColumn) => {
    setAudit(a => ({ ...a, running: true }));
    try {
      const d = await api(`/project/${project.id}/audit/compare`, { method: 'POST', body: { node_a: nodeA, node_b: nodeB, key_column: keyColumn } });
      setAudit({ data: d.data, schema: d.schema, summary: d.summary, running: false });
    } catch (e) {
      setAudit(a => ({ ...a, running: false }));
      notify(e.message);
    }
  };

  // ------------------------------------------------------------------ render
  const head = headNodeId(project);
  const onBranch = !!(project && project.branches[project.head]);
  const canUndo = !!(project && (project.reflog?.[project.head]?.length || project.nodes[head]?.parent_id));
  const canRedo = !!(project && project.redo_stack?.[project.head]?.length);
  const viewingEarlier = !!(project && onBranch && activeNode && activeNode !== head && isAncestor(project, activeNode, head));
  viewingEarlierRef.current = viewingEarlier;
  const versionsAt = {};
  Object.values(project?.versions || {}).forEach(v => { (versionsAt[v.node_id] ||= []).push(v.name); });
  const sampleChoice = project?.sample ? `${project.sample.method}:${project.sample.rows}` : '';

  const renderHistory = () => {
    if (!project || !activeNode) return null;
    const path = [];
    let currentId = project.nodes[activeNode] && !isAncestor(project, activeNode, head) ? activeNode : head;
    while (currentId) {
      const n = project.nodes[currentId];
      if (!n) break;
      path.unshift(n);
      currentId = n.parent_id;
    }
    return (
      <div className="p-4 flex flex-col gap-3 flex-1">
        <div className="flex items-center justify-between gap-2">
          <h3 className="font-panel-title text-panel-title text-on-surface m-0">History</h3>
          <div className="flex items-center gap-1 ml-auto">
            <button className="icon-btn" disabled={!canUndo || busy} onClick={() => undoRedo('undo')} title="Undo last step (⌘/Ctrl+Z)" aria-label="Undo">
              <span className="material-symbols-outlined text-[18px]">undo</span>
            </button>
            <button className="icon-btn" disabled={!canRedo || busy} onClick={() => undoRedo('redo')} title="Redo (⌘/Ctrl+Shift+Z)" aria-label="Redo">
              <span className="material-symbols-outlined text-[18px]">redo</span>
            </button>
          </div>
          <span className="font-ui-small text-ui-small text-on-surface-variant">{path.length} steps</span>
        </div>
        <div className="flex flex-col gap-1 relative">
          <div className="absolute left-[11px] top-4 bottom-4 w-px bg-grid-border -z-10"></div>
          {path.map((node, i) => {
            const isActive = node.id === activeNode;
            const isRoot = !node.parent_id;
            return (
              <div key={node.id} className={`relative flex items-start gap-2 p-1.5 rounded-DEFAULT w-full z-10 group ${isActive ? 'bg-highlight-bg border border-bright-accent' : 'border border-transparent hover:bg-surface-container-high'} ${editing?.nodeId === node.id ? 'ring-2 ring-primary-container' : ''}`}>
                <button type="button" className="flex items-start gap-2 flex-1 min-w-0 text-left bg-transparent border-0 p-0 cursor-pointer"
                  onClick={() => !isActive && selectNode(project, node.id)}>
                  <div className={`w-6 h-6 rounded-full flex items-center justify-center shrink-0 mt-0.5 ${isActive ? 'bg-primary-container text-white' : 'bg-white border border-grid-border group-hover:border-primary-container'}`}>
                    <span className="font-ui-small text-ui-small">{i + 1}</span>
                  </div>
                  <div className="flex flex-col min-w-0">
                    <span className="font-ui-label text-ui-label text-on-surface break-words">{node.operation.label}</span>
                    {versionsAt[node.id] && (
                      <span className="flex flex-wrap gap-1 mt-[2px]">
                        {versionsAt[node.id].map(v => <span key={v} className="px-1.5 rounded-full text-[10px] font-semibold bg-secondary-container text-on-secondary-container">{v}</span>)}
                      </span>
                    )}
                    {node.note && <span className="text-[11px] italic text-on-surface-variant break-words">“{node.note}”</span>}
                    <span className="font-ui-small text-ui-small text-on-surface-variant">{isActive ? (node.id === head ? 'Current' : 'Viewing an earlier step') : 'Click to view'}</span>
                  </div>
                </button>
                {onBranch && (
                  <button type="button" className="icon-btn opacity-0 group-hover:opacity-100 focus:opacity-100 shrink-0" aria-label={`Actions for ${node.operation.label}`}
                    onClick={() => setStepMenu(m => (m === node.id ? null : node.id))}>
                    <span className="material-symbols-outlined text-[18px]">more_horiz</span>
                  </button>
                )}
                {stepMenu === node.id && (
                  <>
                    <div className="fixed inset-0 z-40" onClick={() => setStepMenu(null)} />
                    <div className="absolute right-1 top-8 z-50 w-[210px] bg-white border border-grid-border rounded-lg shadow-[0_8px_24px_rgba(0,0,0,0.08)] py-1">
                      {!isRoot && OPS_BY_TYPE[node.operation.type] && (
                        <button className="menu-item" onClick={() => { setStepMenu(null); setEditing({ nodeId: node.id, type: node.operation.type, params: node.operation.params, label: node.operation.label }); }}>
                          <span className="material-symbols-outlined text-[16px]">edit</span> Edit this step
                        </button>
                      )}
                      <button className="menu-item" onClick={() => { setStepMenu(null); selectNode(project, node.id); }}>
                        <span className="material-symbols-outlined text-[16px]">add</span> Insert a step after this
                      </button>
                      <button className="menu-item" onClick={() => noteStep(node)}><span className="material-symbols-outlined text-[16px]">sticky_note_2</span> {node.note ? 'Edit note' : 'Add note'}</button>
                      <button className="menu-item" onClick={() => { setStepMenu(null); selectNode(project, node.id); setVersionForm({ name: '', message: '' }); setModal('version'); }}>
                        <span className="material-symbols-outlined text-[16px]">bookmark_add</span> Save as version
                      </button>
                      {!isRoot && Object.keys(project.branches).length > 1 && (
                        <button className="menu-item" onClick={() => { setStepMenu(null); setPickNode(node); }}><span className="material-symbols-outlined text-[16px]">content_copy</span> Copy to another branch</button>
                      )}
                      {!isRoot && (
                        <button className="menu-item text-error" onClick={() => deleteStep(node)}><span className="material-symbols-outlined text-[16px]">delete</span> Remove this step</button>
                      )}
                    </div>
                  </>
                )}
              </div>
            );
          })}
        </div>
      </div>
    );
  };

  return (
    <div className="h-screen w-screen flex flex-col text-on-surface bg-surface overflow-hidden">
      {/* Top bar */}
      <div className="bg-primary-container text-on-primary flex items-center px-4 w-full justify-between h-[48px] shrink-0">
        <div className="flex items-center gap-6 h-full min-w-0">
          <button className="font-panel-title text-panel-title font-bold text-white bg-transparent border-0 cursor-pointer p-0" title="Back to start" onClick={() => { setProject(null); setUrlProject(null); fetchRecipes(); }}>
            Loom
          </button>
          {project && (
            <nav className="hidden md:flex h-full items-end gap-5">
              {VIEWS.map(([id, label]) => (
                <button key={id} onClick={() => setViewMode(id)}
                  className={`pb-[12px] bg-transparent border-0 border-b-2 cursor-pointer font-toolbar-button text-toolbar-button ${viewMode === id ? 'text-white border-white' : 'text-white/80 border-transparent hover:text-white'}`}>
                  {label}
                </button>
              ))}
            </nav>
          )}
        </div>
        {project && (
          <div className="flex items-center gap-2">
            <button onClick={() => setModal('branch')} className="topbar-btn bg-white/10">
              <span className="material-symbols-outlined text-[16px]">alt_route</span>
              <span className="max-w-[140px] truncate">{project.branches[project.head] ? project.head : 'detached'}</span>
            </button>
            <div className="relative">
              <button onClick={() => setExportOpen(o => !o)} className="topbar-btn" disabled={busy}>
                <span className="material-symbols-outlined text-[18px]">file_download</span> Export ▾
              </button>
              {exportOpen && (
                <>
                  <div className="fixed inset-0 z-40" onClick={() => setExportOpen(false)} />
                  <div className="absolute right-0 top-[38px] z-50 w-[200px] bg-white text-on-surface border border-grid-border rounded-lg shadow-[0_8px_24px_rgba(0,0,0,0.08)] py-1">
                    <button className="menu-item" onClick={() => exportCode('polars')}><span className="material-symbols-outlined text-[16px]">code</span> Python code (Polars)</button>
                    <button className="menu-item" onClick={() => exportCode('pandas')}><span className="material-symbols-outlined text-[16px]">code</span> Python code (pandas)</button>
                    <div className="h-px bg-grid-border my-1" />
                    <div className="px-3 py-1 text-[11px] text-on-surface-variant">Download data ({totalRows?.toLocaleString() ?? '…'} rows)</div>
                    {EXPORTS.map(([f, label]) => (
                      <button key={f} className="menu-item" onClick={() => exportData(f)}>{label}</button>
                    ))}
                  </div>
                </>
              )}
            </div>
            <button onClick={() => { setVersionForm({ name: '', message: '' }); setModal('version'); }} className="topbar-btn" title="Name the current step so you can load it from code">
              <span className="material-symbols-outlined text-[18px]">bookmark_add</span> Save Version
            </button>
            <button onClick={() => setModal('recipe')} className="topbar-btn border border-white/20">
              <span className="material-symbols-outlined text-[18px]">save</span> Save Recipe
            </button>
          </div>
        )}
      </div>

      {!project ? (
        <StartScreen recipes={recipes} busy={busy} onLoad={(path, options, recipeId, snapshot) => loadProject(path, options, recipeId, false, snapshot)}
          onOpenProject={openProject} onError={notify} />
      ) : (
        <div className="flex flex-1 overflow-hidden">
          {viewMode === 'grid' ? (
            <aside className="w-sidebar-width-prompt bg-sidebar-bg border-r border-grid-border flex flex-col shrink-0 overflow-y-auto grid-scroll">
              <OperationMenu onApplyOp={applyOp} schema={schema} busy={busy} editing={editing} viewingEarlier={viewingEarlier}
                onSaveEdit={saveEdit} onCancelEdit={() => setEditing(null)} />
              {renderHistory()}
            </aside>
          ) : (
            <ProjectSidebar project={project} activeNode={activeNode} onSwitchBranch={switchBranch} schema={schema} totalRows={totalRows} />
          )}

          <main className="flex-1 bg-surface-container-lowest flex flex-col relative overflow-hidden min-w-0">
            {sourceStatus && ['changed', 'missing'].includes(sourceStatus.status) && (
              <div className="shrink-0 flex flex-wrap items-center gap-3 px-3 py-2 bg-error-container text-on-error-container border-b border-error/30 text-[12px]" role="alert">
                <span className="material-symbols-outlined text-[18px]">warning</span>
                <span className="flex-1 min-w-[240px]">{sourceStatus.message}</span>
                {sourceStatus.status === 'changed' && (
                  <>
                    <button className="toolbar-btn" onClick={() => acceptSource(true)}>Use the new file and pin it</button>
                    <button className="toolbar-btn" onClick={() => acceptSource(false)}>Use the new file</button>
                  </>
                )}
              </div>
            )}
            {viewMode === 'grid' && (
              <>
                {(view.filters.length > 0 || view.sort.length > 0) && (
                  <div className="h-[34px] shrink-0 flex items-center gap-3 px-3 bg-highlight-bg border-b border-bright-accent text-[12px]">
                    <span className="material-symbols-outlined text-[16px] text-primary">filter_alt</span>
                    <span>View only: {describeView(view)} — not saved as a step yet.</span>
                    <button className="toolbar-btn ml-auto" disabled={busy} onClick={saveViewAsSteps}>Save as steps</button>
                    <button className="toolbar-btn" onClick={() => gridRef.current?.clearView()}>Clear</button>
                  </div>
                )}
                <div className="flex-1 min-h-0">
                  <DataGrid
                    ref={gridRef}
                    projectId={project.id}
                    nodeId={activeNode}
                    schema={schema}
                    profile={profile}
                    refreshKey={refreshKey}
                    onViewChange={setView}
                    onEditCell={editCell}
                    onError={notify}
                    onTotal={setVisibleRows}
                  />
                </div>
                <div className="h-[24px] bg-sidebar-bg border-t border-grid-border flex items-center px-4 justify-between font-ui-small text-ui-small text-on-surface-variant shrink-0 w-full z-20">
                  <div className="flex items-center gap-4">
                    <span>
                      {visibleRows != null && totalRows != null
                        ? `${visibleRows.toLocaleString()} of ${totalRows.toLocaleString()} rows`
                        : `${(totalRows ?? 0).toLocaleString()} rows`}
                    </span>
                    <span className="w-1 h-1 rounded-full bg-on-surface-variant opacity-50"></span>
                    <span>{Object.keys(schema || {}).length.toLocaleString()} columns</span>
                    {profileLoading && <span>· profiling…</span>}
                    {project.sample && <span className="text-secondary font-semibold">· sample — exports use all rows</span>}
                  </div>
                  <div className="flex items-center gap-3">
                    <label className="flex items-center gap-1" title="Work on a sample for speed. Exports, loom.load() and recipes always use all rows.">
                      <span className="material-symbols-outlined text-[13px]">filter_list</span>
                      <select className="bg-transparent border-0 text-[11px] text-on-surface-variant cursor-pointer outline-none" value={sampleChoice} onChange={e => setSample(e.target.value)} aria-label="Rows to work on">
                        {SAMPLE_CHOICES.map(([v, l]) => <option key={v} value={v}>{l}</option>)}
                        {sampleChoice && !SAMPLE_CHOICES.some(([v]) => v === sampleChoice) && <option value={sampleChoice}>{`${project.sample.method === 'random' ? 'Random' : 'First'} ${project.sample.rows.toLocaleString()} rows`}</option>}
                      </select>
                    </label>
                    {sourceStatus?.status === 'pinned' && <span className="flex items-center gap-1" title={sourceStatus.message}><span className="material-symbols-outlined text-[12px]">lock</span> pinned data</span>}
                  </div>
                  <span className="flex items-center gap-1"><span className="material-symbols-outlined text-[12px]">alt_route</span> {project.branches[project.head] ? project.head : 'detached'}</span>
                </div>
              </>
            )}

            {viewMode === 'profile' && <ProfileView profile={profile} loading={profileLoading} onApplyOp={applyOp} busy={busy} />}

            {viewMode === 'chart' && <ChartView projectId={project.id} nodeId={activeNode} schema={schema} onError={notify} />}

            {viewMode === 'tree' && (
              <BranchTree project={project} activeNode={activeNode}
                onNewBranch={() => setModal('branch')}
                onExport={exportCode}
                onNodeClick={async nodeId => {
                  await checkoutNode(project, nodeId);
                  const p = await refreshTree();
                  if (p) selectNode(p, nodeId);
                }} />
            )}

            {viewMode === 'versions' && (
              <VersionsView project={project} notify={notify} onChanged={refreshTree}
                onNewVersion={() => { setVersionForm({ name: '', message: '' }); setModal('version'); }}
                onOpenNode={nodeId => { selectNode(project, nodeId); setViewMode('grid'); }} />
            )}

            {viewMode === 'compare' && (
              <CompareView project={project} baseNode={activeNode} compareNode={compareNode} setCompareNode={setCompareNode}
                data={baseData} schema={schema} compareData={compareData} compareSchema={compareSchema} fetchData={fetchData} />
            )}

            {viewMode === 'audit' && (
              <AuditMode project={project} schema={schema} onRunAudit={runAudit} auditData={audit.data} auditSchema={audit.schema}
                auditSummary={audit.summary} running={audit.running} />
            )}
          </main>
        </div>
      )}

      {/* Notices */}
      <div className="fixed bottom-8 right-4 z-[60] flex flex-col gap-2 max-w-[420px]" role="status" aria-live="polite">
        {notices.map(n => (
          <div key={n.id} className={`flex items-start gap-2 px-3 py-2 rounded-lg border shadow-[0_8px_24px_rgba(0,0,0,0.08)] text-[13px] ${n.kind === 'error' ? 'bg-error-container border-error/30 text-on-error-container' : 'bg-white border-grid-border text-on-surface'}`}>
            <span className="material-symbols-outlined text-[18px]">{n.kind === 'error' ? 'error' : 'check_circle'}</span>
            <span className="flex-1 break-words">{n.text}</span>
            <button className="bg-transparent border-0 cursor-pointer p-0 text-inherit" onClick={() => setNotices(x => x.filter(y => y.id !== n.id))} aria-label="Dismiss">
              <span className="material-symbols-outlined text-[16px]">close</span>
            </button>
          </div>
        ))}
      </div>

      {/* Code modal */}
      {modal === 'code' && (
        <div className="modal-overlay" onClick={() => setModal(null)}>
          <div className="modal-content" onClick={e => e.stopPropagation()}>
            <div className="flex items-center gap-3 mb-2">
              <h2 className="font-panel-title font-bold text-lg m-0 text-on-surface">Generated code</h2>
              <div className="ml-auto flex border border-grid-border rounded-DEFAULT overflow-hidden" role="tablist">
                {[['polars', 'Polars'], ['pandas', 'pandas']].map(([t, l]) => (
                  <button key={t} role="tab" aria-selected={codeTarget === t} onClick={() => exportCode(t)}
                    className={`px-3 h-[28px] text-[12px] border-0 cursor-pointer ${codeTarget === t ? 'bg-primary-container text-white' : 'bg-white text-on-surface'}`}>{l}</button>
                ))}
              </div>
            </div>
            <p className="text-on-surface-variant text-sm mb-4">This {codeTarget === 'pandas' ? 'pandas' : 'Polars'} script recreates the data up to the selected step.</p>
            <pre className="code-block text-xs">{code}</pre>
            <div className="flex gap-2 justify-end">
              <button className="btn-secondary" onClick={() => navigator.clipboard.writeText(code).then(() => notify('Copied to clipboard', 'info'), () => notify('Copy failed — select the text instead'))}>Copy</button>
              <button className="btn" onClick={() => setModal(null)}>Close</button>
            </div>
          </div>
        </div>
      )}

      {/* Save recipe modal */}
      {modal === 'recipe' && (
        <div className="modal-overlay" onClick={() => setModal(null)}>
          <form className="modal-content" onClick={e => e.stopPropagation()} onSubmit={e => { e.preventDefault(); saveRecipe(); }}>
            <h2 className="font-panel-title font-bold text-lg mb-2 text-on-surface">Save recipe</h2>
            <p className="text-on-surface-variant text-sm mb-4">Saves the steps up to the selected one, so you can replay them on another file with the same columns.</p>
            <input autoFocus className="w-full bg-white border border-grid-border rounded-DEFAULT px-2 h-[36px] mb-4 outline-none focus:border-primary-container" placeholder="e.g. monthly-cleanup" value={recipeName} onChange={e => setRecipeName(e.target.value)} />
            <div className="flex gap-2 justify-end">
              <button type="button" className="btn-secondary" onClick={() => setModal(null)}>Cancel</button>
              <button type="submit" className="btn" disabled={!recipeName.trim()}>Save</button>
            </div>
          </form>
        </div>
      )}

      {/* Recipe warnings */}
      {recipeWarnings && (
        <div className="modal-overlay" onClick={() => setRecipeWarnings(null)}>
          <div className="modal-content" onClick={e => e.stopPropagation()}>
            <h2 className="font-panel-title font-bold text-lg mb-2 text-on-surface">This file may not fit the recipe</h2>
            <ul className="text-sm text-on-surface mb-4 pl-5">{recipeWarnings.warnings.map((w, i) => <li key={i}>{w}</li>)}</ul>
            <div className="flex gap-2 justify-end">
              <button className="btn-secondary" onClick={() => setRecipeWarnings(null)}>Cancel</button>
              <button className="btn" onClick={() => { const r = recipeWarnings.retry; setRecipeWarnings(null); r(); }}>Apply anyway</button>
            </div>
          </div>
        </div>
      )}

      {/* Save version modal */}
      {modal === 'version' && project && (
        <div className="modal-overlay" onClick={() => setModal(null)}>
          <form className="modal-content" onClick={e => e.stopPropagation()} onSubmit={e => { e.preventDefault(); saveVersion(); }}>
            <h2 className="font-panel-title font-bold text-lg mb-2 text-on-surface">Save version</h2>
            <p className="text-on-surface-variant text-sm mb-4">
              Names <strong>“{project.nodes[activeNode]?.operation.label}”</strong> so you can load it from Python and record model results against it. Versions never move.
            </p>
            <div className="flex flex-col gap-3 mb-4">
              <input autoFocus className="w-full bg-white border border-grid-border rounded-DEFAULT px-2 h-[36px] outline-none focus:border-primary-container" placeholder="v1, baseline, no-outliers…"
                value={versionForm.name} onChange={e => setVersionForm(f => ({ ...f, name: e.target.value }))} aria-label="Version name" />
              <textarea rows={2} className="w-full bg-white border border-grid-border rounded-DEFAULT px-2 py-1 outline-none focus:border-primary-container" placeholder="What's different about this data? (optional)"
                value={versionForm.message} onChange={e => setVersionForm(f => ({ ...f, message: e.target.value }))} aria-label="Version message" />
            </div>
            <div className="flex gap-2 justify-end">
              <button type="button" className="btn-secondary" onClick={() => setModal(null)}>Cancel</button>
              <button type="submit" className="btn" disabled={!versionForm.name.trim()}>Save version</button>
            </div>
          </form>
        </div>
      )}

      {/* Copy a step to another branch */}
      {pickNode && project && (
        <div className="modal-overlay" onClick={() => setPickNode(null)}>
          <div className="modal-content" onClick={e => e.stopPropagation()}>
            <h2 className="font-panel-title font-bold text-lg mb-2 text-on-surface">Copy step to another branch</h2>
            <p className="text-on-surface-variant text-sm mb-4">“{pickNode.operation.label}” will be added to the end of the branch you pick.</p>
            <div className="flex flex-col gap-2">
              {Object.values(project.branches).filter(b => b.name !== project.head).map(b => (
                <button key={b.name} className="flex items-center justify-between p-2 rounded cursor-pointer border text-left bg-white border-grid-border hover:bg-highlight-bg" onClick={() => copyStepTo(b.name)}>
                  <span className="font-ui-label">{b.name}</span>
                  <span className="text-[12px] text-on-surface-variant truncate ml-2">{project.nodes[b.head_node_id]?.operation.label}</span>
                </button>
              ))}
            </div>
            <div className="flex justify-end mt-6"><button className="btn-secondary" onClick={() => setPickNode(null)}>Cancel</button></div>
          </div>
        </div>
      )}

      {/* Branch modal */}
      {modal === 'branch' && project && (
        <div className="modal-overlay" onClick={() => setModal(null)}>
          <div className="modal-content" onClick={e => e.stopPropagation()}>
            <h2 className="font-panel-title font-bold text-lg mb-4 text-on-surface">Branches</h2>
            <form className="flex gap-2" onSubmit={e => { e.preventDefault(); createBranch(); }}>
              <input autoFocus type="text" placeholder="new-branch-name" value={newBranchName} onChange={e => setNewBranchName(e.target.value)}
                className="flex-1 bg-white border border-grid-border rounded-DEFAULT px-2 h-[36px] outline-none focus:border-primary-container" />
              <button type="submit" className="btn h-[36px]" disabled={!newBranchName.trim()}>Create from current step</button>
            </form>
            <h3 className="font-ui-label font-bold text-on-surface mt-6 mb-2">Switch branch</h3>
            <div className="flex flex-col gap-2 max-h-[240px] overflow-y-auto">
              {Object.values(project.branches).map(b => (
                <button key={b.name} onClick={() => switchBranch(b.name)}
                  className={`flex items-center justify-between p-2 rounded cursor-pointer border text-left ${project.head === b.name ? 'bg-highlight-bg border-bright-accent' : 'bg-white border-grid-border hover:bg-surface-container-high'}`}>
                  <span className="flex items-center gap-2 min-w-0">
                    <span className="material-symbols-outlined text-[16px] text-on-surface-variant">alt_route</span>
                    <span className="font-ui-label text-on-surface truncate">{b.name}</span>
                  </span>
                  <span className="font-ui-small text-on-surface-variant truncate ml-2">{project.head === b.name ? 'Active' : project.nodes[b.head_node_id]?.operation.label}</span>
                </button>
              ))}
            </div>
            {onBranch && Object.keys(project.branches).length > 1 && (
              <>
                <h3 className="font-ui-label font-bold text-on-surface mt-6 mb-1">Merge or delete</h3>
                <p className="text-[12px] text-on-surface-variant m-0 mb-2">Merging replays the other branch's new steps on top of <strong>{project.head}</strong>.</p>
                <div className="flex flex-col gap-1 max-h-[200px] overflow-y-auto">
                  {Object.values(project.branches).filter(b => b.name !== project.head).map(b => (
                    <div key={b.name} className="flex items-center gap-2 text-[13px]">
                      <span className="flex-1 truncate">{b.name}</span>
                      <button className="toolbar-btn" disabled={busy} onClick={() => mergeBranch(b.name)}>Merge into {project.head}</button>
                      <button className="toolbar-btn" onClick={() => deleteBranch(b.name)} aria-label={`Delete branch ${b.name}`}><span className="material-symbols-outlined text-[14px]">delete</span></button>
                    </div>
                  ))}
                </div>
              </>
            )}
            <div className="flex justify-end mt-6">
              <button className="btn-secondary" onClick={() => setModal(null)}>Close</button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

/** True if `nodeId` lies on the path from the root to `headId`. */
function isAncestor(project, nodeId, headId) {
  let cur = headId;
  while (cur) {
    if (cur === nodeId) return true;
    cur = project.nodes[cur]?.parent_id;
  }
  return false;
}

export default App;
