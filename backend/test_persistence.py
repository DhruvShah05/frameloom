import pytest
import os
import shutil
import tempfile
from fastapi.testclient import TestClient
from backend.main import app, projects, recipes_db, _load_persisted_state, PROJECTS_DIR, RECIPES_DIR

client = TestClient(app)

@pytest.fixture(autouse=True)
def clean_persistence_dirs():
    # Clean up before tests
    if os.path.exists(PROJECTS_DIR):
        shutil.rmtree(PROJECTS_DIR)
    if os.path.exists(RECIPES_DIR):
        shutil.rmtree(RECIPES_DIR)
    projects.clear()
    recipes_db.clear()
    
    yield
    
    # Clean up after tests
    if os.path.exists(PROJECTS_DIR):
        shutil.rmtree(PROJECTS_DIR)
    if os.path.exists(RECIPES_DIR):
        shutil.rmtree(RECIPES_DIR)
    projects.clear()
    recipes_db.clear()

def test_project_persistence():
    # Create a temporary CSV file
    with tempfile.NamedTemporaryFile(mode='w', suffix='.csv', delete=False) as f:
        f.write("id,value\n1,10\n2,20\n")
        temp_csv = f.name
        
    try:
        # 1. Load project
        response = client.post("/project/load", json={"path": temp_csv})
        assert response.status_code == 200
        project_data = response.json()
        project_id = project_data["id"]
        root_node_id = project_data["branches"]["main"]["head_node_id"]
        
        # 2. Add an operation
        op1_req = {
            "parent_node_id": root_node_id,
            "operation": {
                "type": "filter_rows",
                "params": {"column": "value", "operator": ">", "value": 15},
                "label": "Filter value > 15"
            }
        }
        response = client.post(f"/project/{project_id}/op", json=op1_req)
        assert response.status_code == 200
        op1_node_id = response.json()["id"]
        
        # Add a second operation
        op2_req = {
            "parent_node_id": op1_node_id,
            "operation": {
                "type": "drop_column",
                "params": {"columns": ["id"]},
                "label": "Drop id"
            }
        }
        response = client.post(f"/project/{project_id}/op", json=op2_req)
        assert response.status_code == 200
        
        # 3. Simulate server restart by clearing in-memory state
        projects.clear()
        
        # (The store re-reads project files from disk, so nothing more to check here.)
        # 4. Reload from disk
        _load_persisted_state()
        
        # 5. Verify the state was recovered
        response = client.get(f"/project/{project_id}/tree")
        assert response.status_code == 200
        recovered_tree = response.json()
        
        # Check that we have 3 nodes: ingest + 2 operations
        assert len(recovered_tree["nodes"]) == 3
        
        # Check that branch head is correctly updated
        assert recovered_tree["head"] == "main"
        
        # The head of main should be the last op we added
        main_branch_head = recovered_tree["branches"]["main"]["head_node_id"]
        last_node = recovered_tree["nodes"][main_branch_head]
        assert last_node["operation"]["type"] == "drop_column"
        
    finally:
        if os.path.exists(temp_csv):
            os.remove(temp_csv)
