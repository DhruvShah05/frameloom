import polars as pl
import duckdb

df_a = pl.DataFrame({"id": [1, 2], "val": [10, 20]})
df_b = pl.DataFrame({"id": [1, 2], "val": [10, 30]})

query = "SELECT a.*, b.* FROM df_a AS a FULL OUTER JOIN df_b AS b ON a.id = b.id"
try:
    print("Executing query...")
    res = duckdb.sql(query).pl()
    print(res)
except Exception as e:
    print("Error:", e)
