import ast
from backend.models import Operation
from backend.codegen import generate_script

def test_generate_script_escaping():
    weird_col = 'col"with\\quotes\nand spaces'
    weird_val = 'val"with\\quotes'
    weird_freq = '1"h\\m'
    weird_method = 'method"with\\'

    operations = [
        Operation(id="1", label="op1", type="filter_rows", params={"column": weird_col, "operator": "eq", "value": weird_val}),
        Operation(id="2", label="op2", type="drop_column", params={"columns": [weird_col]}),
        Operation(id="3", label="op3", type="rename_column", params={"mapping": {weird_col: "new_name"}}),
        Operation(id="4", label="op4", type="resample", params={"time_col": weird_col, "freq": weird_freq, "val_col": weird_col, "agg": "sum"}),
        Operation(id="5", label="op5", type="rolling", params={"time_col": weird_col, "window": weird_freq, "val_col": weird_col, "agg": "mean"}),
        Operation(id="6", label="op6", type="fill_gaps", params={"time_col": weird_col, "val_col": weird_col, "method": weird_method}),
    ]

    # Generate the script
    script = generate_script(operations, "polars", "dummy.csv")

    # Assert it is valid Python syntax by parsing it
    try:
        ast.parse(script)
    except SyntaxError as e:
        assert False, f"Generated script is not valid Python: {e}\n\nScript:\n{script}"
