import urllib.request
import json

url = "http://127.0.0.1:8000/project/d285e4ca-0cd3-4f91-bc79-6a176a54eb04/audit/compare"
data = {
    "node_a": "3329fe33-920d-46ad-8f79-240649c9ac9a",
    "node_b": "91e9e3ed-319d-4c00-815e-7d68563a6217",
    "key_column": "name"
}
req = urllib.request.Request(url, json.dumps(data).encode(), {"Content-Type": "application/json"}, method="POST")
try:
    with urllib.request.urlopen(req) as response:
        print(response.read().decode())
except Exception as e:
    print("Error:", e)
    if hasattr(e, 'read'):
        print(e.read().decode())
