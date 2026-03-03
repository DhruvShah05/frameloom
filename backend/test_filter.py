import pytest
import os
import tempfile
from fastapi.testclient import TestClient
from backend.main import app

client = TestClient(app)

def test_chained_filter_rows():
    # Create a temporary CSV file
    with tempfile.NamedTemporaryFile(mode='w', suffix='.csv', delete=False) as f:
        f.write("id,value,category\n1,10,A\n2,20,B\n3,30,A\n4,40,B\n5,50,A\n")
        temp_csv = f.name
        
    try:
        # 1. Load project
        response = client.post("/project/load", json={"path": temp_csv})
        assert response.status_code == 200
        project_data = response.json()
        project_id = project_data["id"]
        root_node_id = project_data["branches"]["main"]["head_node_id"]
        
        # 2. Add first filter operation (value > 15)
        op1_req = {
            "parent_node_id": root_node_id,
            "operation": {
                "type": "filter_rows",
                "params": {"column": "value", "operator": "gt", "value": 15},
                "label": "Filter value > 15"
            }
        }
        response = client.post(f"/project/{project_id}/op", json=op1_req)
        assert response.status_code == 200
        op1_node_id = response.json()["id"]
        
        # Verify first filter results
        response = client.get(f"/project/{project_id}/data?node_id={op1_node_id}")
        assert response.status_code == 200
        data1 = response.json()["data"]
        assert len(data1) == 4 # id 2, 3, 4, 5
        
        # 3. Add second chained filter operation (category == A)
        op2_req = {
            "parent_node_id": op1_node_id,
            "operation": {
                "type": "filter_rows",
                "params": {"column": "category", "operator": "eq", "value": "A"},
                "label": "Filter category == A"
            }
        }
        response = client.post(f"/project/{project_id}/op", json=op2_req)
        assert response.status_code == 200
        op2_node_id = response.json()["id"]
        
        # Verify chained filter results (intersection: value > 15 AND category == A)
        response = client.get(f"/project/{project_id}/data?node_id={op2_node_id}")
        assert response.status_code == 200
        data2 = response.json()["data"]
        assert len(data2) == 2 # id 3, 5
        assert data2[0]["id"] == 3
        assert data2[1]["id"] == 5
        
        # Test code generation for chained filters
        response = client.post(f"/project/{project_id}/export/code", json={"node_id": op2_node_id, "target": "polars"})
        assert response.status_code == 200
        code = response.json()["code"]
        assert "pl.col('value') > 15" in code
        assert "pl.col('category') == 'A'" in code
        
    finally:
        if os.path.exists(temp_csv):
            os.remove(temp_csv)
