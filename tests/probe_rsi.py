"""Inspect celestial_objects nested in the RSI starmap system row."""

import requests

BASE = "https://robertsspaceindustries.com/api/starmap"
H = {"Content-Type": "application/json"}

r = requests.post(f"{BASE}/star-systems/STANTON", headers=H, timeout=30)
row = r.json()["data"]["resultset"][0]
objs = row["celestial_objects"]
print("celestial_objects container type:", type(objs).__name__)

items = objs.values() if isinstance(objs, dict) else objs
items = list(items)
print("count:", len(items))
print("sample object keys:", sorted(items[0].keys()))
print()
for o in sorted(items, key=lambda x: (str(x.get("type")), str(x.get("name")))):
    print(f"  {o.get('code'):<22} {str(o.get('name')):<26} "
          f"type={o.get('type'):<14} parent={o.get('parent_id')} "
          f"subtype={ (o.get('subtype') or {}).get('name') if isinstance(o.get('subtype'), dict) else o.get('subtype')}")
