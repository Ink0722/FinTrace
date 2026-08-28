import json

VALID = {"positive", "negative", "not_evaluable"}
errors = []

inputs = []
with open("financial_risk_label_input.jsonl", encoding="utf-8") as f:
    for line in f:
        o = json.loads(line)
        inputs.append((o["case_id"], o["rule_id"], o["review_packet_id"], {m["metric_key"] for m in o["metrics"]}))

results = []
with open("financial_risk_label_result.jsonl", encoding="utf-8") as f:
    for i, line in enumerate(f, 1):
        o = json.loads(line)
        results.append(o)
        if list(o.keys()) != ["case_id", "rule_id", "review_packet_id", "reference_label", "supporting_metric_keys", "reason"]:
            errors.append(f"line {i}: key order/content {list(o.keys())}")
        if o["reference_label"] not in VALID:
            errors.append(f"line {i}: bad label {o['reference_label']}")
        if not o["supporting_metric_keys"]:
            errors.append(f"line {i}: empty supporting keys")
        if len(o["supporting_metric_keys"]) != len(set(o["supporting_metric_keys"])):
            errors.append(f"line {i}: duplicate supporting keys")
        if not o["reason"].strip():
            errors.append(f"line {i}: empty reason")

if len(results) != len(inputs):
    errors.append(f"count mismatch: {len(results)} vs {len(inputs)}")

seen = set()
for i, (o, (cid, rid, pid, avail)) in enumerate(zip(results, inputs), 1):
    if (o["case_id"], o["rule_id"], o["review_packet_id"]) != (cid, rid, pid):
        errors.append(f"line {i}: id mismatch")
    for key in o["supporting_metric_keys"]:
        if key not in avail:
            errors.append(f"line {i}: key {key} not in that input line's metrics")
    sig = (o["case_id"], o["rule_id"], o["review_packet_id"])
    if sig in seen:
        errors.append(f"line {i}: duplicate case {sig}")
    seen.add(sig)

from collections import Counter
by_rule = {}
for o, (cid, rid, pid, avail) in zip(results, inputs):
    by_rule.setdefault(rid, Counter())[o["reference_label"]] += 1

print(f"lines: {len(results)}")
for rid in sorted(by_rule):
    print(f"  {rid}: {dict(by_rule[rid])}")
print("ERRORS:", errors if errors else "none — all checks passed")
