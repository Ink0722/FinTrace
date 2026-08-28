import json

with open("financial_risk_label_input.jsonl", encoding="utf-8") as f:
    lines = f.readlines()

print("total lines:", len(lines))
obj = json.loads(lines[0])
print(json.dumps(obj, ensure_ascii=False, indent=2)[:4000])
