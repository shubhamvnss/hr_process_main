"""Integrity and plausibility checks for the synthetic Talent Acquisition extract.

Run after generate_ta_event_log.py. Every check prints PASS / FAIL with counts;
the script exits non-zero if any hard check fails.
"""
import csv, os, sys
from collections import Counter, defaultdict
from datetime import datetime

D = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
CUTOFF = datetime(2026, 10, 19, 9, 0)
FAILS = []


def load(name):
    with open(os.path.join(D, name), encoding="utf-8") as f:
        return list(csv.DictReader(f))


def check(label, ok, detail=""):
    print(f"  [{'PASS' if ok else 'FAIL'}] {label}" + (f"  -  {detail}" if detail else ""))
    if not ok:
        FAILS.append(label)


def T(x):
    return datetime.strptime(x, "%Y-%m-%d %H:%M:%S")


print("loading extract ...")
ev = load("ta_event_log.csv")
rq = load("ta_requisition_master.csv")
ap = load("ta_application_master.csv")
st = load("ta_stage_transitions.csv")
act = load("ta_activity_master.csv")
sla = load("ta_sla_definitions.csv")
gaps = load("ta_gap_detection.csv")
gt = load("ta_scenario_ground_truth.csv")
gmap = load("ta_gap_mapping.csv")
print(f"  {len(ev)} events, {len(rq)} requisitions, {len(ap)} applications, "
      f"{len(st)} stage transitions, {len(gaps)} gap findings\n")

req_ids = {r["requisition_id"] for r in rq}
app_ids = {r["application_id"] for r in ap}
act_names = {r["activity_name"] for r in act}
sla_codes = {r["sla_code"] for r in sla}

print("1. structure and referential integrity")
check("event_id is unique", len({e["event_id"] for e in ev}) == len(ev))
check("source_event_id is unique", len({e["source_event_id"] for e in ev}) == len(ev))
bad = [e for e in ev if e["requisition_id"] not in req_ids]
check("every event resolves to a requisition in the master", not bad, f"{len(bad)} orphans")
bad = [e for e in ev if e["application_id"] and e["application_id"] not in app_ids]
check("every application-level event resolves to an application", not bad, f"{len(bad)} orphans")
bad = [r for r in ap if r["requisition_id"] not in req_ids]
check("every application links to one requisition", not bad, f"{len(bad)} orphans")
bad = [e for e in ev if e["activity_name"] not in act_names]
check("every activity exists in the activity dictionary", not bad, f"{len(bad)} unknown")
bad = [e for e in ev if e["case_type"] == "REQUISITION" and e["application_id"]]
check("requisition-level events carry no application_id", not bad, f"{len(bad)} rows")
bad = [e for e in ev if e["case_type"] == "APPLICATION" and not e["application_id"]]
check("application-level events always carry an application_id", not bad, f"{len(bad)} rows")
missing = [e for e in ev if not (e["event_id"] and e["activity_name"] and e["event_timestamp"]
                                 and e["actor_persona"] and e["source_system"] and e["status_after"])]
check("mandatory event fields are populated", not missing, f"{len(missing)} rows")

print("\n2. one primary source system per activity (BRD section 5)")
multi = {a: s for a, s in ((a, {e["source_system"] for e in ev if e["activity_name"] == a})
                           for a in act_names) if len(s) > 1}
check("each activity has exactly one source system", not multi, f"{len(multi)} activities with more")

print("\n3. time and censoring")
late = [e for e in ev if T(e["event_timestamp"]) > CUTOFF]
check("no event after the reporting cut-off", not late, f"{len(late)} rows")
by_case = defaultdict(list)
for e in ev:
    by_case[e["process_instance_id"]].append(e)
nonmono = 0
for pid, evs in by_case.items():
    evs.sort(key=lambda x: (x["event_timestamp"], x["event_id"]))
    for a, b in zip(evs, evs[1:]):
        if T(b["event_timestamp"]) < T(a["event_timestamp"]):
            nonmono += 1
check("event timestamps are non-decreasing within every case", nonmono == 0, f"{nonmono} inversions")
off_hours = [e for e in ev if not (9 <= T(e["event_timestamp"]).hour < 18)
             or T(e["event_timestamp"]).weekday() >= 5]
check("all events fall inside the declared business calendar", not off_hours, f"{len(off_hours)} rows")
open_req = [r for r in rq if r["is_open_censored"] == "Y"]
check("open requisitions carry no close timestamp",
      all(not r["requisition_close_timestamp"] for r in open_req), f"{len(open_req)} open cases")
check("open requisitions are not labelled as completed outcomes",
      all(r["requisition_outcome"] in ("Open / In Progress", "On Hold") for r in open_req))

print("\n4. gap_reference must NOT be pre-labelled in the source log")
check("gap_reference is empty in every event row",
      all(not e["gap_reference"] for e in ev))
check("gap findings exist only in the derived detection file", len(gaps) > 0)
check("every GAP-01..GAP-18 reference is defined in ta_gap_mapping",
      {g["gap_reference"] for g in gaps} <= {g["gap_reference"] for g in gmap})
check("all 18 gap references are exercised by the data",
      len({g["gap_reference"] for g in gaps}) == 18,
      f"{len({g['gap_reference'] for g in gaps})} of 18")
ev_by_id = {e["event_id"]: e for e in ev}
badev = [g for g in gaps for i in g["evidence_event_ids"].split("|") if i and i not in ev_by_id]
check("every cited evidence event_id exists in the event log", not badev, f"{len(badev)} dangling")

print("\n5. SLA bookkeeping")
check("every sla_code is defined", {e["sla_code"] for e in ev if e["sla_code"]} <= sla_codes)
bad = [e for e in ev if e["sla_code"] and
       (int(e["sla_measured_minutes"]) > int(e["sla_target_minutes"])) != (e["sla_breach_flag"] == "Y")]
check("sla_breach_flag agrees with measured vs target", not bad, f"{len(bad)} rows")
bad = [e for e in ev if e["sla_breach_flag"] == "Y" and not e["sla_code"]]
check("no breach is flagged without an SLA definition", not bad, f"{len(bad)} rows")

print("\n6. outcome consistency")
joined = {r["application_id"] for r in ap if r["application_outcome"] == "Joined"}
has_join_ev = {e["application_id"] for e in ev if e["activity_name"] == "Candidate Joined"}
check("'Joined' applications all have a Candidate Joined event", joined <= has_join_ev,
      f"{len(joined - has_join_ev)} missing")
check("every Candidate Joined event belongs to a 'Joined' application", has_join_ev <= joined,
      f"{len(has_join_ev - joined)} extra")
wd = {r["application_id"] for r in ap if r["application_outcome"] == "Withdrawn"}
has_wd = {e["application_id"] for e in ev if e["activity_name"] == "Candidate Withdrew"}
check("'Withdrawn' applications all have a Candidate Withdrew event", wd == has_wd,
      f"{len(wd ^ has_wd)} mismatches")
filled = [r for r in rq if r["requisition_outcome"] == "Filled"]
check("every filled requisition has at least one hire",
      all(int(r["hires_count"]) >= 1 for r in filled), f"{len(filled)} filled")
check("no requisition records more hires than positions",
      all(int(r["hires_count"]) <= int(r["positions_count"]) for r in rq))
nohire = [r for r in rq if r["requisition_outcome"] == "Closed Without Hire"]
check("requisitions closed without hire record zero hires",
      all(int(r["hires_count"]) == 0 for r in nohire), f"{len(nohire)} cases")

print("\n7. derived masters reconcile with the event log")
bad = [r for r in rq if int(r["event_count"]) != len(by_case.get(r["requisition_id"], []))]
check("requisition event_count matches the log", not bad, f"{len(bad)} rows")
bad = [r for r in ap if int(r["event_count"]) != len(by_case.get(r["application_id"], []))]
check("application event_count matches the log", not bad, f"{len(bad)} rows")
cnt = Counter(e["requisition_id"] for e in ev if e["case_type"] == "APPLICATION")
bad = [r for r in rq if sum(int(a["event_count"]) for a in ap
                            if a["requisition_id"] == r["requisition_id"]) != cnt[r["requisition_id"]]]
check("pipeline events roll up to the right requisition", not bad, f"{len(bad)} rows")
st_cases = {r["process_instance_id"] for r in st}
check("every case appears in the stage-transition table",
      st_cases == set(by_case), f"{len(st_cases ^ set(by_case))} mismatches")

print("\n8. detector agreement with generator intent (sanity, not a target)")
gt_by_req = {r["requisition_id"]: r for r in gt}
det = defaultdict(set)
for g in gaps:
    det[g["gap_reference"]].add(g["requisition_id"])


def agreement(gid, predicate, label):
    truth = {k for k, v in gt_by_req.items() if predicate(v)}
    found = det[gid] & set(gt_by_req)
    tp = len(truth & found)
    rec = tp / len(truth) if truth else 0
    print(f"  {gid} {label:<44} intended {len(truth):>5}  recall {100 * rec:5.1f}%")
    return rec


r1 = agreement("GAP-01", lambda v: v.get("gt_demand_returns") not in ("", "0", "1"),
               ">=2 demand returns designed")
r3 = agreement("GAP-03", lambda v: v.get("gt_approval_slow") == "True"
               or v.get("gt_approval_returns") not in ("", "0", "1"),
               "slow approver or repeated returns designed")
r2 = agreement("GAP-02", lambda v: v.get("gt_budget_delay") == "True",
               "budget validation delay designed")
check("GAP-01 rule recovers the designed cases", r1 > 0.90, f"recall {100 * r1:.1f}%")
check("GAP-03 rule recovers the designed cases", r3 > 0.60, f"recall {100 * r3:.1f}%")
check("GAP-02 rule recovers the designed cases", r2 > 0.70, f"recall {100 * r2:.1f}%")

print("\n9. segmentation and contrast coverage")
for col in ("business_unit", "job_family", "location", "job_level", "priority"):
    check(f"{col} has >=3 distinct values", len({r[col] for r in rq}) >= 3,
          f"{len({r[col] for r in rq})} values")
check("source_channel has >=5 distinct values", len({r["source_channel"] for r in ap}) >= 5)
check("every recruitment stage S1..S9 is present in the log",
      {e["stage_code"] for e in ev} >= {f"S{i}" for i in range(1, 10)})
check("healthy cases exist alongside delayed ones (requisitions with no finding)",
      len(req_ids - {g["requisition_id"] for g in gaps}) > 0,
      f"{len(req_ids - {g['requisition_id'] for g in gaps})} requisitions with no finding")

print("\n" + ("ALL CHECKS PASSED" if not FAILS else f"{len(FAILS)} CHECK(S) FAILED: " + "; ".join(FAILS)))
sys.exit(1 if FAILS else 0)
