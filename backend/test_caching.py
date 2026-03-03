import unittest
from unittest.mock import patch
from backend.models import Project, Node, Operation
from backend.engine import ExecutionEngine
import polars as pl
import os

class TestCaching(unittest.TestCase):
    def setUp(self):
        self.csv_path = "test_caching.csv"
        # We need a dataframe that won't fail operations. We will use simple filter/rename ops.
        pl.DataFrame({"A": [1, 2, 3], "B": ["x", "y", "z"]}).write_csv(self.csv_path)

    def tearDown(self):
        if os.path.exists(self.csv_path):
            os.remove(self.csv_path)

    def test_caching_policy_and_redundancy(self):
        engine = ExecutionEngine()
        
        # Build a project with a long chain of 50 nodes
        nodes = {}
        parent_id = None
        for i in range(50):
            node_id = f"node_{i}"
            # Simple operation
            op = Operation(id=f"op_{i}", label=f"op_{i}", type="rename_column", params={"mapping": {"A": "A"}})
            
            nodes[node_id] = Node(
                id=node_id,
                operation=op,
                parent_id=parent_id,
                schema_hash="unknown"
            )
            parent_id = node_id
            
        project = Project(
            id="test_proj",
            source_path=self.csv_path,
            source_hash="unknown",
            head="node_49",
            nodes=nodes
        )
        
        # First call: build the whole plan for node_49
        with patch.object(engine, '_apply_operation', wraps=engine._apply_operation) as mock_apply:
            lf = engine.build_plan(project, "node_49")
            # Should have called _apply_operation 50 times
            self.assertEqual(mock_apply.call_count, 50)
            
        # At this point, thanks to caching policy (every 10 nodes), some nodes should be cached.
        self.assertIn("node_9", engine.cache)
        self.assertIn("node_19", engine.cache)
        self.assertIn("node_29", engine.cache)
        self.assertIn("node_39", engine.cache)
        self.assertIn("node_49", engine.cache)

        # Second call: rebuild for node_49
        # Since node_49 is cached, we scan backwards, find node_49 is cached, 
        # start from it, and remaining nodes is empty!
        with patch.object(engine, '_apply_operation', wraps=engine._apply_operation) as mock_apply:
            lf = engine.build_plan(project, "node_49")
            self.assertEqual(mock_apply.call_count, 0)
            
        # Third call: let's build for a node down the chain but not exactly on the cache boundary, e.g. node_45
        # node_39 is cached.
        # scanning backwards from node_45: node_45, 44, 43, 42, 41, 40, 39(cached)
        # So it starts at node_39, and applies ops for 40, 41, 42, 43, 44, 45 -> 6 operations.
        with patch.object(engine, '_apply_operation', wraps=engine._apply_operation) as mock_apply:
            lf = engine.build_plan(project, "node_45")
            self.assertEqual(mock_apply.call_count, 6)

if __name__ == "__main__":
    unittest.main()
