import json
import os

os.makedirs("_label", exist_ok=True)

rows = []
with open("financial_risk_label_input.jsonl", encoding="utf-8") as f:
    for i, line in enumerate(f, 1):
        obj = json.loads(line)
        rows.append((i, obj))

# distinct rules
rules = {}
for i, obj in rows:
    rid = obj["rule_id"]
    rules.setdefault(rid, {"count": 0, "formulas": set(), "applics": set(), "thresholds": set()})
    rules[rid]["count"] += 1
    rules[rid]["formulas"].add(obj["rule"]["formula"])
    rules[rid]["applics"].add(obj["rule"].get("applicability", ""))
    rules[rid]["thresholds"].add(json.dumps(obj["rule"].get("thresholds"), ensure_ascii=False, sort_keys=True))

summary = []
for rid, info in rules.items():
    summary.append(f"=== {rid} x{info['count']}")
    summary.append(f"  formula: {list(info['formulas'])[0]}")
    for t in info["thresholds"]:
        summary.append(f"  thresholds: {t}")
    for a in info["applics"]:
        summary.append(f"  applicability: {a}")
with open("_label/_rule_summary.txt", "w", encoding="utf-8") as g:
    g.write("\n".join(summary))
print("rules:", {rid: info["count"] for rid, info in rules.items()})


def val(metrics, code, period):
    for m in metrics:
        if m["metric_code"] == code and m["report_period"] == period:
            return m["value"]
    return None


def growth(cur, prev):
    if prev is None or cur is None or prev == 0:
        return None
    return (cur - prev) / abs(prev)


def fmt(x):
    if x is None:
        return "NA"
    return f"{x:.4f}"


def compute_helpers(obj):
    """Compute per-rule quantities strictly from the documented formulas."""
    rid = obj["rule_id"]
    ms = obj["metrics"]
    periods = obj["periods"]
    out = []
    if rid == "CASH_PROFIT_DIVERGENCE":
        for a, b in zip(periods, periods[1:]):
            p1, p2 = val(ms, "NET_PROFIT_PARENT", a), val(ms, "NET_PROFIT_PARENT", b)
            c1, c2 = val(ms, "OPERATING_CASHFLOW", a), val(ms, "OPERATING_CASHFLOW", b)
            pg = growth(p2, p1) if p1 is not None and p2 is not None else None
            cg = growth(c2, c1) if c1 is not None and c2 is not None else None
            cp = (c2 / p2) if (p2 not in (None, 0)) else None
            out.append(f"    pair {a}->{b}: profit {fmt(p1)}->{fmt(p2)} (growth {fmt(pg)}), ocf {fmt(c1)}->{fmt(c2)} (growth {fmt(cg)}), ocf/profit(cur) {fmt(cp)}")
    elif rid in ("RECEIVABLE_REVENUE_DIVERGENCE", "INVENTORY_REVENUE_DIVERGENCE"):
        code = "ACCOUNTS_RECEIVABLE" if rid.startswith("RECEIVABLE") else "INVENTORY"
        for a, b in zip(periods, periods[1:]):
            x1, x2 = val(ms, code, a), val(ms, code, b)
            r1, r2 = val(ms, "REVENUE", a), val(ms, "REVENUE", b)
            xg = growth(x2, x1) if x1 is not None and x2 is not None else None
            rg = growth(r2, r1) if r1 is not None and r2 is not None else None
            gap = (xg - rg) if (xg is not None and rg is not None) else None
            out.append(f"    pair {a}->{b}: {code} {fmt(x1)}->{fmt(x2)} (growth {fmt(xg)}), revenue {fmt(r1)}->{fmt(r2)} (growth {fmt(rg)}), gap {fmt(gap)}")
    elif rid == "LIQUIDITY_PRESSURE":
        for p in periods:
            ca, cl, mc = val(ms, "CURRENT_ASSETS", p), val(ms, "CURRENT_LIABILITIES", p), val(ms, "MONETARY_CAPITAL", p)
            cr = (ca / cl) if (ca is not None and cl not in (None, 0)) else None
            cc = (mc / cl) if (mc is not None and cl not in (None, 0)) else None
            out.append(f"    {p}: current_ratio {fmt(cr)}, cash_to_cl {fmt(cc)} (CA {fmt(ca)}, CL {fmt(cl)}, MC {fmt(mc)})")
    elif rid == "MARGIN_VOLATILITY":
        for a, b in zip(periods, periods[1:]):
            r1, r2 = val(ms, "REVENUE", a), val(ms, "REVENUE", b)
            c1, c2 = val(ms, "OPERATING_COST", a), val(ms, "OPERATING_COST", b)
            o1, o2 = val(ms, "OPERATING_PROFIT", a), val(ms, "OPERATING_PROFIT", b)
            gm1 = (r1 - c1) / r1 if (r1 not in (None, 0) and c1 is not None) else None
            gm2 = (r2 - c2) / r2 if (r2 not in (None, 0) and c2 is not None) else None
            om1 = o1 / r1 if (r1 not in (None, 0) and o1 is not None) else None
            om2 = o2 / r2 if (r2 not in (None, 0) and o2 is not None) else None
            gmc = (gm2 - gm1) if (gm1 is not None and gm2 is not None) else None
            omc = (om2 - om1) if (om1 is not None and om2 is not None) else None
            out.append(f"    pair {a}->{b}: gm {fmt(gm1)}->{fmt(gm2)} (chg {fmt(gmc)}), om {fmt(om1)}->{fmt(om2)} (chg {fmt(omc)})")
    elif rid == "NEGATIVE_OPERATING_CASHFLOW_PERSISTENCE":
        for p in periods:
            c = val(ms, "OPERATING_CASHFLOW", p)
            out.append(f"    {p}: ocf {fmt(c)} ({'neg' if (c is not None and c < 0) else 'pos/zero' if c is not None else 'NA'})")
    elif rid == "SALES_CASH_REVENUE_DIVERGENCE":
        prev_ratio = None
        for p in periods:
            s, r = val(ms, "CASH_RECEIVED_FROM_SALES", p), val(ms, "REVENUE", p)
            ratio = (s / r) if (s is not None and r not in (None, 0)) else None
            chg = (ratio - prev_ratio) if (ratio is not None and prev_ratio is not None) else None
            out.append(f"    {p}: cash/revenue {fmt(ratio)} (chg {fmt(chg)})")
            prev_ratio = ratio
    elif rid == "LEVERAGE_PRESSURE":
        prev_d = None
        for p in periods:
            tl, ta = val(ms, "TOTAL_LIABILITIES", p), val(ms, "TOTAL_ASSETS", p)
            d = (tl / ta) if (tl is not None and ta not in (None, 0)) else None
            chg = (d - prev_d) if (d is not None and prev_d is not None) else None
            out.append(f"    {p}: debt_to_assets {fmt(d)} (chg {fmt(chg)})")
            prev_d = d
    else:
        out.append("    !! UNKNOWN RULE - manual")
    return out


BATCH = 45
batch_no = 0
buf = []
for idx, (i, obj) in enumerate(rows):
    if idx % BATCH == 0:
        if buf:
            with open(f"_label/batch_{batch_no:02d}.txt", "w", encoding="utf-8") as g:
                g.write("\n".join(buf))
        batch_no += 1
        buf = [f"########## BATCH {batch_no} (lines {i}-{min(i + BATCH - 1, len(rows))}) ##########"]
    r = obj["rule"]
    buf.append(f"[{i}] {obj['case_id']} | {obj['rule_id']} | packet={obj['review_packet_id']}")
    buf.append(f"  thresholds: {json.dumps(r.get('thresholds'), ensure_ascii=False)}")
    buf.append(f"  applicability: {r.get('applicability', '')}")
    missing = []
    for code in r.get("required_metrics", []):
        have = [m["report_period"] for m in obj["metrics"] if m["metric_code"] == code]
        missp = [p for p in obj["periods"] if p not in have]
        if missp:
            missing.append(f"{code} missing {missp}")
    buf.append(f"  metrics: " + "; ".join(f"{m['metric_key']}={m['value']:,.2f}" for m in obj["metrics"]))
    if missing:
        buf.append(f"  !! MISSING: {missing}")
    buf.extend(compute_helpers(obj))
if buf:
    with open(f"_label/batch_{batch_no:02d}.txt", "w", encoding="utf-8") as g:
        g.write("\n".join(buf))
print("batches:", batch_no)
