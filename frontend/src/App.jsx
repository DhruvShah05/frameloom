import React, { useState, useEffect } from 'react';
import DataGrid from './components/DataGrid';
import OperationMenu from './components/OperationMenu';
import ProjectSidebar from './components/ProjectSidebar';
import BranchTree from './components/BranchTree';
import AuditMode from './components/AuditMode';
import CompareView from './components/CompareView';

const API_BASE = 'http://127.0.0.1:8000';

function App() {
  const [project, setProject] = useState(null);
  const [activeNode, setActiveNode] = useState(null);
  const [compareNode, setCompareNode] = useState(null);
  const [data, setData] = useState(null);
  const [schema, setSchema] = useState(null);
  const [compareData, setCompareData] = useState(null);
  const [compareSchema, setCompareSchema] = useState(null);
  const [auditData, setAuditData] = useState(null);
  const [auditSchema, setAuditSchema] = useState(null);
  
  const [viewMode, setViewMode] = useState('grid');
  const [selectingCompareTarget, setSelectingCompareTarget] = useState(false);

  const [codeModal, setCodeModal] = useState(false);
  const [code, setCode] = useState('');
  const [branchName, setBranchName] = useState('');

  // Recipes
  const [recipes, setRecipes] = useState([]);
  const [recipeName, setRecipeName] = useState('');
  const [selectedRecipeId, setSelectedRecipeId] = useState('');
  const [validationWarnings, setValidationWarnings] = useState(null);
  const [pendingRecipeData, setPendingRecipeData] = useState(null); // {path, recipeId}

  useEffect(() => {
    const init = async () => {
      let fetchedRecipes = [];
      try {
        const res = await fetch(`${API_BASE}/recipes`);
        if (res.ok) {
          fetchedRecipes = await res.json();
          setRecipes(fetchedRecipes);
        }
      } catch (e) {}

      const params = new URLSearchParams(window.location.search);
      const pathParam = params.get('path');
      const recipeNameParam = params.get('recipe_name');
      
      if (pathParam) {
        if (recipeNameParam) {
          const matched = fetchedRecipes.find(r => r.name === recipeNameParam);
          if (matched) {
            loadProject(pathParam, matched.id);
          } else {
            alert(`Recipe '${recipeNameParam}' not found! Loading without recipe.`);
            loadProject(pathParam);
          }
        } else {
          loadProject(pathParam);
        }
      }
    };
    init();
  }, []);

  const fetchRecipes = async () => {
    try {
      const res = await fetch(`${API_BASE}/recipes`);
      if (res.ok) {
        setRecipes(await res.json());
      }
    } catch(e) {}
  };

  const loadProject = async (path, recipeId = null, force = false) => {
    try {
      if (!recipeId) {
        const res = await fetch(`${API_BASE}/project/load`, {
          method: 'POST',
          headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({path})
        });
        if (res.ok) {
          const p = await res.json();
          setProject(p);
          const rootNodeId = p.branches[p.head].head_node_id;
          setActiveNode(rootNodeId);
          fetchData(p.id, rootNodeId);
        }
      } else {
        const res = await fetch(`${API_BASE}/recipe/apply`, {
          method: 'POST',
          headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({recipe_id: recipeId, new_source_path: path, force})
        });
        if (res.ok) {
          const result = await res.json();
          if (result.status === 'warning') {
            setValidationWarnings(result.warnings);
            setPendingRecipeData({path, recipeId});
          } else if (result.status === 'success') {
            const p = result.project;
            setProject(p);
            const rootNodeId = p.branches[p.head].head_node_id;
            setActiveNode(rootNodeId);
            fetchData(p.id, rootNodeId);
            setValidationWarnings(null);
            setPendingRecipeData(null);
          }
        }
      }
    } catch (e) {
      alert("Error connecting to backend: " + e.message);
    }
  };

  const forceApplyRecipe = () => {
    if (pendingRecipeData) {
      loadProject(pendingRecipeData.path, pendingRecipeData.recipeId, true);
    }
  };

  const saveRecipe = async () => {
    if (!project || !activeNode || !recipeName) return;
    try {
      const res = await fetch(`${API_BASE}/project/${project.id}/recipe/save`, {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({ name: recipeName, node_id: activeNode })
      });
      if (res.ok) {
        setRecipeName('');
        fetchRecipes();
        alert("Recipe saved!");
      }
    } catch(e) {}
  };

  const fetchData = async (projectId, nodeId, isCompare = false) => {
    try {
      const res = await fetch(`${API_BASE}/project/${projectId}/data?node_id=${nodeId}`);
      if (res.ok) {
        const d = await res.json();
        if (isCompare) {
          setCompareData(d.data);
          setCompareSchema(d.schema);
        } else {
          setData(d.data);
          setSchema(d.schema);
        }
      }
    } catch (e) {
      console.error(e);
    }
  };

  const handleRunAudit = async (nodeA, nodeB, keyColumn) => {
    if (!project || !nodeA || !nodeB || !keyColumn) return;
    try {
      const res = await fetch(`${API_BASE}/project/${project.id}/audit/compare`, {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({ node_a: nodeA, node_b: nodeB, key_column: keyColumn })
      });
      if (res.ok) {
        const d = await res.json();
        setAuditData(d.data);
        setAuditSchema(d.schema);
      }
    } catch (e) {
      console.error(e);
    }
  };

  const fetchTree = async () => {
    if (!project) return;
    try {
      const res = await fetch(`${API_BASE}/project/${project.id}/tree`);
      if (res.ok) {
        setProject(await res.json());
      }
    } catch (e) {}
  };

  const applyOp = async (type, params) => {
    if (!project || !activeNode) return;
    try {
      const res = await fetch(`${API_BASE}/project/${project.id}/op`, {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({
          parent_node_id: activeNode,
          operation: { type, params, label: type }
        })
      });
      if (res.ok) {
        const newNode = await res.json();
        setActiveNode(newNode.id);
        fetchData(project.id, newNode.id);
        fetchTree();
      }
    } catch (e) {
      console.error(e);
    }
  };

  const checkoutNode = async (projectId, targetId) => {
    try {
      let checkoutTarget = targetId;
      if (project) {
        const branchMatch = Object.entries(project.branches).find(([bName, b]) => b.head_node_id === targetId);
        if (branchMatch) {
          checkoutTarget = branchMatch[0];
        }
      }
      await fetch(`${API_BASE}/project/${projectId}/checkout`, {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({ target: checkoutTarget })
      });
      fetchTree();
    } catch (e) {
      console.error("Checkout failed:", e);
    }
  };

  const handleExport = async () => {
    if (!project || !activeNode) return;
    try {
      const res = await fetch(`${API_BASE}/project/${project.id}/export/code`, {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({ node_id: activeNode })
      });
      if (res.ok) {
        const d = await res.json();
        setCode(d.code);
        setCodeModal(true);
      }
    } catch(e) {}
  };

  const [branchModal, setBranchModal] = useState(false);
  const [newBranchName, setNewBranchName] = useState('');

  const createBranch = async () => {
    if (!project || !activeNode || !newBranchName) return;
    try {
      const res = await fetch(`${API_BASE}/project/${project.id}/branch`, {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({ name: newBranchName, from_node_id: activeNode })
      });
      if (res.ok) {
        setBranchModal(false);
        setNewBranchName('');
        fetchTree();
      }
    } catch(e) {
      console.error(e);
    }
  };

  const switchBranch = async (branchName) => {
    if (!project) return;
    const branch = project.branches[branchName];
    if (!branch) return;
    
    await checkoutNode(project.id, branch.head_node_id);
    setActiveNode(branch.head_node_id);
    fetchData(project.id, branch.head_node_id);
    setBranchModal(false);
    fetchTree();
  };

  const renderHistory = () => {
    if (!project || !activeNode) return null;
    
    const path = [];
    let currentId = activeNode;
    
    // Trace from the branch head if possible, so we don't truncate future events
    if (project.head) {
      const branch = project.branches[project.head];
      if (branch) {
        currentId = branch.head_node_id;
      } else {
        currentId = project.head; // Detached head
      }
    }

    while(currentId) {
      const n = project.nodes[currentId];
      if (!n) break;
      path.unshift(n);
      currentId = n.parent_id;
    }

    return (
      <div className="p-4 flex flex-col gap-3 flex-1 overflow-y-auto grid-scroll">
        <div className="flex items-center justify-between">
          <h3 className="font-panel-title text-panel-title text-on-surface m-0">History</h3>
          <span className="font-ui-small text-ui-small text-on-surface-variant">{path.length} Steps</span>
        </div>
        
        <div className="flex flex-col gap-1 relative">
          <div className="absolute left-[11px] top-4 bottom-4 w-px bg-grid-border -z-10"></div>
          {path.map((node, i) => {
            const isActive = node.id === activeNode;
            if (isActive) {
              return (
                <div key={node.id} className="flex items-start gap-2 p-1.5 rounded-DEFAULT bg-highlight-bg border border-bright-accent cursor-pointer z-10">
                  <div className="w-6 h-6 rounded-full bg-primary-container border border-primary-container flex items-center justify-center shrink-0 mt-0.5 text-white shadow-sm">
                    <span className="font-ui-small text-ui-small">{i + 1}</span>
                  </div>
                  <div className="flex flex-col">
                    <span className="font-ui-label text-ui-label text-on-surface capitalize">{node.operation.label.replace('_', ' ')}</span>
                    <span className="font-ui-small text-ui-small text-on-surface-variant">Step {i+1}</span>
                  </div>
                </div>
              );
            }
            return (
              <div 
                key={node.id} 
                className="flex items-start gap-2 p-1.5 rounded-DEFAULT hover:bg-surface-container-high cursor-pointer group"
                onClick={() => {
                  setActiveNode(node.id);
                  fetchData(project.id, node.id);
                }}
              >
                <div className="w-6 h-6 rounded-full bg-white border border-grid-border flex items-center justify-center shrink-0 mt-0.5 group-hover:border-primary-container">
                  <span className="font-ui-small text-ui-small text-on-surface-variant">{i + 1}</span>
                </div>
                <div className="flex flex-col">
                  <span className="font-ui-label text-ui-label text-on-surface capitalize">{node.operation.label.replace('_', ' ')}</span>
                  <span className="font-ui-small text-ui-small text-on-surface-variant">Click to view</span>
                </div>
              </div>
            );
          })}
        </div>
      </div>
    );
  };

  const getColCount = () => {
    if (!schema) return 0;
    return Object.keys(schema).length;
  };
  
  const getRowCount = () => {
    if (!data) return 0;
    return data.length;
  };

  return (
    <div className="h-screen w-screen flex flex-col text-on-surface bg-surface overflow-hidden">
      
      {/* Top Navbar */}
      <div className="bg-primary-container text-on-primary flex items-center px-4 w-full justify-between h-[48px] border-b border-grid-border shadow-none">
        <div className="flex items-center gap-6 h-full">
          <span className="font-panel-title text-panel-title font-bold text-white flex items-center gap-1">
            Loom
          </span>
          {project && (
            <nav className="hidden md:flex h-full items-end gap-6">
              <button onClick={() => setViewMode('grid')} className={`pb-[12px] font-toolbar-button text-toolbar-button flex items-center ${viewMode === 'grid' ? 'text-white border-b-2 border-white' : 'text-white/80 hover:text-white transition-colors'}`}>
                  Grid
              </button>
              <button onClick={() => setViewMode('tree')} className={`pb-[14px] font-toolbar-button text-toolbar-button flex items-center transition-colors ${viewMode === 'tree' ? 'text-white font-bold' : 'text-white/80 hover:text-white'}`}>
                  Branch Tree
              </button>
              <button onClick={() => setViewMode('compare')} className={`pb-[14px] font-toolbar-button text-toolbar-button flex items-center transition-colors ${viewMode === 'compare' ? 'text-white font-bold' : 'text-white/80 hover:text-white'}`}>
                  Compare
              </button>
              <button onClick={() => setViewMode('audit')} className={`pb-[14px] font-toolbar-button text-toolbar-button flex items-center transition-colors ${viewMode === 'audit' ? 'text-white font-bold' : 'text-white/80 hover:text-white'}`}>
                  Audit
              </button>
            </nav>
          )}
        </div>
        
        {project && (
          <div className="flex items-center gap-4">
            <div onClick={() => setBranchModal(true)} className="flex items-center bg-white/10 hover:bg-white/20 px-3 py-1.5 rounded-full border border-transparent transition-colors text-white font-toolbar-button text-toolbar-button gap-1.5 cursor-pointer">
              <span className="material-symbols-outlined text-[16px]">alt_route</span>
              {project.head}
            </div>
            <button onClick={handleExport} className="hover:bg-white/10 px-3 py-1.5 rounded-full transition-colors text-white flex items-center gap-1.5 font-toolbar-button text-toolbar-button border border-transparent">
              <span className="material-symbols-outlined text-[18px]">file_download</span> Export Code
            </button>
            <button onClick={saveRecipe} className="hover:bg-white/10 px-3 py-1.5 rounded-full transition-colors text-white flex items-center gap-1.5 font-toolbar-button text-toolbar-button border border-white/20">
              <span className="material-symbols-outlined text-[18px]">save</span> Save Recipe
            </button>
          </div>
        )}
      </div>

      {!project ? (
         <div className="flex-1 flex items-center justify-center bg-surface-container-lowest">
            <div className="bg-white p-8 rounded-lg border border-grid-border shadow-sm w-[400px]">
              <div className="flex flex-col gap-4">
                <h2 className="font-panel-title text-panel-title font-bold text-center m-0">Start a Session</h2>
                <div className="flex flex-col gap-2">
                  <label className="font-ui-small text-ui-small text-on-surface-variant">Data File Path</label>
                  <input type="text" id="filepath" placeholder="/absolute/path/to/data.csv" className="w-full bg-white border border-grid-border rounded-DEFAULT px-2 h-row-height font-grid-data text-grid-data focus:border-primary-container focus:ring-1 focus:ring-bright-accent outline-none" />
                </div>
                <button onClick={() => loadProject(document.getElementById('filepath').value, selectedRecipeId)} className="w-full bg-primary-container text-white font-ui-label text-ui-label h-row-height rounded-DEFAULT hover:bg-primary transition-colors flex items-center justify-center">
                  Load & Start
                </button>
              </div>
            </div>
         </div>
      ) : (
        <div className="flex flex-1 overflow-hidden">
          {viewMode === 'grid' ? (
            <aside className="w-sidebar-width-prompt bg-sidebar-bg border-r border-grid-border flex flex-col shrink-0 overflow-y-auto">
              <OperationMenu onApplyOp={applyOp} schema={schema} />
              {renderHistory()}
            </aside>
          ) : (
            <ProjectSidebar />
          )}

          <main className="flex-1 bg-surface-container-lowest flex flex-col relative overflow-hidden">
            {viewMode === 'grid' && (
              <>
                <div className="flex-1 overflow-auto bg-surface-container-lowest">
                  <DataGrid data={data} schema={schema} />
                </div>
                
                {/* Status Bar */}
                <div className="h-[24px] bg-sidebar-bg border-t border-grid-border flex items-center px-4 justify-between font-ui-small text-ui-small text-on-surface-variant shrink-0 w-full z-20">
                  <div className="flex items-center gap-4">
                    <span>{getRowCount().toLocaleString()} rows</span>
                    <span className="w-1 h-1 rounded-full bg-on-surface-variant opacity-50"></span>
                    <span>{getColCount().toLocaleString()} columns</span>
                  </div>
                  <div className="flex items-center gap-4">
                    <span className="flex items-center gap-1"><span className="material-symbols-outlined text-[12px]">alt_route</span> Branch: {project.head}</span>
                  </div>
                </div>
              </>
            )}

            {viewMode === 'tree' && (
               <BranchTree project={project} activeNode={activeNode} onNodeClick={async (nodeId) => {
                  await checkoutNode(project.id, nodeId);
                  setActiveNode(nodeId);
                  fetchData(project.id, nodeId);
               }} />
            )}
            
            {viewMode === 'compare' && (
               <CompareView 
                  project={project}
                  baseNode={activeNode}
                  compareNode={compareNode}
                  setCompareNode={setCompareNode}
                  data={data}
                  schema={schema}
                  compareData={compareData}
                  compareSchema={compareSchema}
                  fetchData={fetchData}
               />
            )}
            
            {viewMode === 'audit' && (
               <AuditMode 
                  project={project} 
                  onRunAudit={handleRunAudit} 
                  auditData={auditData} 
                  auditSchema={auditSchema} 
               />
            )}
          </main>
        </div>
      )}

      {/* Code Modal */}
      {codeModal && (
        <div className="modal-overlay" onClick={() => setCodeModal(false)}>
          <div className="modal-content" onClick={e => e.stopPropagation()}>
            <h2 className="font-panel-title font-bold text-lg mb-2 text-on-surface">Generated Code</h2>
            <p className="text-on-surface-variant text-sm mb-4">This Polars script recreates your data up to the selected step.</p>
            <div className="code-block text-xs">
              {code}
            </div>
            <button className="btn" onClick={() => setCodeModal(false)}>Close</button>
          </div>
        </div>
      )}

      {/* Branch Modal */}
      {branchModal && project && (
        <div className="modal-overlay" onClick={() => setBranchModal(false)}>
          <div className="modal-content" onClick={e => e.stopPropagation()}>
            <h2 className="font-panel-title font-bold text-lg mb-4 text-on-surface">Branch Management</h2>
            
            <div className="flex flex-col gap-4">
              <div>
                <h3 className="font-ui-label font-bold text-on-surface mb-2">Create New Branch</h3>
                <div className="flex gap-2">
                  <input 
                    type="text" 
                    placeholder="feature-branch" 
                    value={newBranchName} 
                    onChange={e => setNewBranchName(e.target.value)}
                    className="flex-1 bg-white border border-grid-border rounded-DEFAULT px-2 h-[36px] font-grid-data text-grid-data focus:border-primary-container focus:ring-1 focus:ring-bright-accent outline-none" 
                  />
                  <button onClick={createBranch} className="btn h-[36px]">Create</button>
                </div>
              </div>
              
              <div className="mt-4">
                <h3 className="font-ui-label font-bold text-on-surface mb-2">Switch Branch</h3>
                <div className="flex flex-col gap-2 max-h-[200px] overflow-y-auto">
                  {Object.values(project.branches).map(b => (
                    <div 
                      key={b.name} 
                      onClick={() => switchBranch(b.name)}
                      className={`flex items-center justify-between p-2 rounded cursor-pointer border ${project.head === b.name ? 'bg-highlight-bg border-bright-accent' : 'border-grid-border hover:bg-surface-container-high'}`}
                    >
                      <div className="flex items-center gap-2">
                        <span className="material-symbols-outlined text-[16px] text-on-surface-variant">alt_route</span>
                        <span className="font-ui-label text-on-surface">{b.name}</span>
                      </div>
                      {project.head === b.name && <span className="font-ui-small text-on-surface-variant">Active</span>}
                    </div>
                  ))}
                </div>
              </div>
            </div>
            
            <div className="flex justify-end mt-6">
              <button className="btn-secondary" onClick={() => setBranchModal(false)}>Close</button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

export default App;
