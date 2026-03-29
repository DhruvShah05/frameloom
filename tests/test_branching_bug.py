import unittest
from fastapi.testclient import TestClient
from frameloom.main import app, projects, LoadRequest
import os

client = TestClient(app)

class TestBranchingBug(unittest.TestCase):
    def setUp(self):
        # Clear existing projects
        projects.clear()
        
        # Ensure dummy data file exists for load
        self.dummy_data = "col1,col2\n1,2"
        with open("test_data.csv", "w") as f:
            f.write(self.dummy_data)
            
    def tearDown(self):
        if os.path.exists("test_data.csv"):
            os.remove("test_data.csv")

    def test_branching_from_history(self):
        # 1. Load project
        response = client.post("/project/load", json={"path": "test_data.csv"})
        self.assertEqual(response.status_code, 200)
        project_data = response.json()
        project_id = project_data["id"]
        
        # Get root node
        main_branch = project_data["branches"]["main"]
        root_node_id = main_branch["head_node_id"]
        
        # 2. Add an operation to advance main
        response = client.post(f"/project/{project_id}/op", json={
            "parent_node_id": root_node_id,
            "operation": {"id": "dummy_op_1", "type": "filter_rows", "params": {"column": "col1", "operator": "gt", "value": 0}, "label": "filter"}
        })
        self.assertEqual(response.status_code, 200)
        node_b_id = response.json()["id"]
        
        # Verify main is at node_b
        tree_resp = client.get(f"/project/{project_id}/tree")
        tree_data = tree_resp.json()
        self.assertEqual(tree_data["branches"]["main"]["head_node_id"], node_b_id)
        
        # 3. Explicitly checkout root_node_id (this simulates what the frontend now does)
        # Note: the bug could also happen if checkout was not called, because project.head 
        # would just be 'main' which was at node_b.
        response = client.post(f"/project/{project_id}/checkout", json={
            "target": root_node_id
        })
        self.assertEqual(response.status_code, 200)
        
        # 4. Add an operation branching off the root node (historical node)
        response = client.post(f"/project/{project_id}/op", json={
            "parent_node_id": root_node_id,
            "operation": {"id": "dummy_op_2", "type": "sort", "params": {"columns": ["col1"]}, "label": "sort"}
        })
        self.assertEqual(response.status_code, 200)
        new_node_id = response.json()["id"]
        
        # 5. Verify the graph state
        tree_resp = client.get(f"/project/{project_id}/tree")
        tree_data = tree_resp.json()
        
        # main should still point to node_b
        self.assertEqual(tree_data["branches"]["main"]["head_node_id"], node_b_id)
        
        # A new branch should have been created pointing to new_node_id
        branches = tree_data["branches"]
        
        # Find branches pointing to new_node_id
        matching_branches = [name for name, b in branches.items() if b["head_node_id"] == new_node_id]
        self.assertEqual(len(matching_branches), 1, "Exactly one branch should point to the new node")
        
        # It should not be 'main'
        new_branch_name = matching_branches[0]
        self.assertNotEqual(new_branch_name, "main")
        self.assertTrue(new_branch_name.startswith("branch-"), "Should be an auto-generated branch name")
        
        # The new branch should have forked_from_node_id = root_node_id
        self.assertEqual(branches[new_branch_name]["forked_from_node_id"], root_node_id)
        
if __name__ == '__main__':
    unittest.main()
