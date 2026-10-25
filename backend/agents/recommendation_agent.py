"""
backend.agents.recommendation_agent - deterministic agent selection for Talent Acquisition.

Agent selection is DETERMINISTIC and version-controlled in this module. No model picks an agent
id, and no model computes a KPI. The maps below are the whole contract:

    Tier 1 KPI  x  Tier 2 diagnostic KPI  x  Gap reference   ->   specialist agent

An LLM layer may later use the selected agent, its intervention focus and the supplied evidence to
draft the wording of a recommendation. It may not change which agent was selected, invent an agent
id, or restate a KPI value - that is what keeps a recommendation auditable.

Selection priority (specification section 7)
--------------------------------------------
    1. Gap-level routing     a validated gap selects its primary agent from GAP_AGENT_MAP
    2. Tier 2 routing        no confirmed gap: the diagnostic KPI selects its specialist
    3. Tier 1 routing        only a headline deviation: its primary agent investigates which
                             Tier 2 drivers to look at. A Tier 1 KPI alone never produces a
                             definitive root cause.

A gap route only fires when the gap was actually DETECTED in the event log. Association in the
library is not a trigger - gap_detection is (BRD section 10).

Two documented departures from the supplied specification, both deliberate
--------------------------------------------------------------------------
1. Section 4 of the spec gives a primary AND supporting agent for every gap, while the
   illustrative GAP_AGENT_MAP in section 6 drops the supporting agent for GAP-01 to GAP-04.
   Section 4 is implemented here, because losing "GAP-02 -> supporting HR_REC_14" would silently
   remove governance from every approval-delay recommendation.

2. Section 5 lists T2-17 as evidence for GAP-01, while the BRD gap library records T2-01. Both
   are kept and kept separate: GAP_TIER2_MAP is the agent-routing evidence from the spec, and the
   library value stays in gap_mapping.tier2_evidence where the KPI engine reads it. They answer
   different questions and should not be merged.
"""
from __future__ import annotations

from typing import Dict, List, Optional

# ============================================================================
# 1. AGENT CATALOGUE
# ============================================================================
# Stable ids. A gap or KPI may route to the same agent without duplicating logic.

DOMAIN_RECRUITMENT = "Recruitment Operations"
DOMAIN_GOVERNANCE = "Governance & Controls"
DOMAIN_CANDIDATE = "Candidate Experience"
DOMAIN_PROCESS = "Process & Integration"

AGENT_CATALOGUE: Dict[str, Dict[str, str]] = {
    "HR_REC_01": {
        "name": "Demand Quality Agent", "domain": DOMAIN_RECRUITMENT,
        "responsibility": "Incomplete demand, requisition data and job-description rework",
        "owner": "Business Manager / Workforce Planning",
    },
    "HR_REC_02": {
        "name": "Approval Optimization Agent", "domain": DOMAIN_GOVERNANCE,
        "responsibility": "Budget, headcount and requisition approval delays",
        "owner": "Approving Business Leader",
    },
    "HR_REC_03": {
        "name": "Recruiter Capacity Agent", "domain": DOMAIN_RECRUITMENT,
        "responsibility": "Recruiter allocation, workload and queue aging",
        "owner": "TA Operations",
    },
    "HR_REC_04": {
        "name": "Sourcing Effectiveness Agent", "domain": DOMAIN_RECRUITMENT,
        "responsibility": "Sourcing launch delays, channel performance and shortlist quality",
        "owner": "Sourcing Lead",
    },
    "HR_REC_05": {
        "name": "Screening Optimization Agent", "domain": DOMAIN_RECRUITMENT,
        "responsibility": "Screening backlogs, screening turnaround and repeated reviews",
        "owner": "Recruiter / TA Operations",
    },
    "HR_REC_06": {
        "name": "Interview Coordination Agent", "domain": DOMAIN_RECRUITMENT,
        "responsibility": "Interview scheduling, rescheduling and panel availability",
        "owner": "Recruiting Coordination",
    },
    "HR_REC_07": {
        "name": "Interview Feedback Agent", "domain": DOMAIN_RECRUITMENT,
        "responsibility": "Late or incomplete interview feedback",
        "owner": "Interview Panel / Hiring Manager",
    },
    "HR_REC_08": {
        "name": "Assessment Management Agent", "domain": DOMAIN_RECRUITMENT,
        "responsibility": "Assessment delays, expiries and result synchronization",
        "owner": "Assessment Operations",
    },
    "HR_REC_09": {
        "name": "Selection Decision Agent", "domain": DOMAIN_RECRUITMENT,
        "responsibility": "Delayed decisions, additional rounds and decision rework",
        "owner": "Hiring Manager",
    },
    "HR_REC_10": {
        "name": "Offer & Compensation Agent", "domain": DOMAIN_GOVERNANCE,
        "responsibility": "Compensation revisions, approval loops and offer turnaround",
        "owner": "Compensation / HR",
    },
    "HR_REC_11": {
        "name": "Candidate Engagement Agent", "domain": DOMAIN_CANDIDATE,
        "responsibility": "Candidate inactivity, withdrawal and communication delays",
        "owner": "Recruiter / TA Operations",
    },
    "HR_REC_12": {
        "name": "Pre-joining & Onboarding Agent", "domain": DOMAIN_CANDIDATE,
        "responsibility": "Background checks, documentation and joining readiness",
        "owner": "HR Operations",
    },
    "HR_REC_13": {
        "name": "Process Integration Agent", "domain": DOMAIN_PROCESS,
        "responsibility": "System synchronization failures, retries and duplicate updates",
        "owner": "HR Technology / IT",
    },
    "HR_REC_14": {
        "name": "Governance & Compliance Agent", "domain": DOMAIN_GOVERNANCE,
        "responsibility": "Mandatory controls, policy adherence and documented exceptions",
        "owner": "TA Operations / HR Governance",
    },
    "HR_REC_15": {
        "name": "Recruitment Effort & Cost Agent", "domain": DOMAIN_PROCESS,
        "responsibility": "Manual effort, coordination overhead and cost-per-hire visibility",
        "owner": "TA Operations Lead",
    },
    "HR_REC_16": {
        "name": "Process Bottleneck & Rework Agent", "domain": DOMAIN_PROCESS,
        "responsibility": "Identify the bottleneck stage and route the intervention to the "
                          "relevant specialist; stage elapsed time, waiting patterns and rework "
                          "loops span multiple recruitment functions",
        "owner": "TA Operations / Process Owner",
    },
}

# ============================================================================
# 2. TIER 1 KPI ROUTING  (specification section 3, "Tier 1 KPI ownership")
# ============================================================================
# Routing DEFAULTS, not exclusive ownership. A confirmed gap overrides these.
# "stage_specialist" resolves through STAGE_AGENT_MAP once the bottleneck stage is known.

TIER1_AGENT_MAP: Dict[str, str] = {
    "T1-01": "HR_REC_16", "T1-02": "HR_REC_02", "T1-03": "HR_REC_16", "T1-04": "HR_REC_16",
    "T1-05": "HR_REC_11", "T1-06": "HR_REC_10", "T1-07": "HR_REC_12", "T1-08": "HR_REC_11",
    "T1-09": "HR_REC_16", "T1-10": "HR_REC_14", "T1-11": "HR_REC_03", "T1-12": "HR_REC_15",
}

TIER1_SUPPORTING_MAP: Dict[str, List[str]] = {
    "T1-01": ["HR_REC_02", "HR_REC_03", "HR_REC_06", "HR_REC_10", "HR_REC_12"],
    "T1-02": ["HR_REC_14"],
    "T1-03": ["stage_specialist", "HR_REC_14"],
    "T1-04": ["stage_specialist", "HR_REC_13"],
    "T1-05": ["HR_REC_04", "HR_REC_05", "HR_REC_08"],
    "T1-06": ["HR_REC_11"],
    "T1-07": ["HR_REC_10", "HR_REC_11"],
    "T1-08": ["stage_specialist"],
    "T1-09": ["HR_REC_01", "HR_REC_07", "HR_REC_10"],
    "T1-10": ["HR_REC_01", "HR_REC_13"],
    "T1-11": ["HR_REC_15"],
    "T1-12": ["HR_REC_03", "HR_REC_13"],
}

# ============================================================================
# 3. TIER 2 KPI ROUTING  (specification section 3)
# ============================================================================

TIER2_AGENT_MAP: Dict[str, str] = {
    "T2-01": "HR_REC_02", "T2-02": "HR_REC_16", "T2-03": "HR_REC_16", "T2-04": "HR_REC_03",
    "T2-05": "HR_REC_04", "T2-06": "HR_REC_06", "T2-07": "HR_REC_14", "T2-08": "HR_REC_11",
    "T2-09": "HR_REC_05", "T2-10": "HR_REC_11", "T2-11": "HR_REC_08", "T2-12": "HR_REC_10",
    "T2-13": "HR_REC_10", "T2-14": "HR_REC_10", "T2-15": "HR_REC_12", "T2-16": "HR_REC_11",
    "T2-17": "HR_REC_16", "T2-18": "HR_REC_01", "T2-19": "HR_REC_07", "T2-20": "HR_REC_14",
    "T2-21": "HR_REC_14", "T2-22": "HR_REC_03", "T2-23": "HR_REC_15", "T2-24": "HR_REC_15",
    "T2-25": "HR_REC_15", "T2-26": "HR_REC_13",
}

# Which Tier 1 outcomes each diagnostic supports (specification section 3).
TIER1_TIER2_MAP: Dict[str, List[str]] = {
    "T2-01": ["T1-01", "T1-02"],
    "T2-02": ["T1-01", "T1-03"],
    "T2-03": ["T1-01", "T1-03", "T1-04"],
    "T2-04": ["T1-01", "T1-03", "T1-04"],
    "T2-05": ["T1-01", "T1-03", "T1-04"],
    "T2-06": ["T1-01", "T1-03", "T1-04"],
    "T2-07": ["T1-02", "T1-03"],
    "T2-08": ["T1-05", "T1-08"],
    "T2-09": ["T1-05", "T1-08"],
    "T2-10": ["T1-05", "T1-08"],
    "T2-11": ["T1-05"],
    "T2-12": ["T1-06"],
    "T2-13": ["T1-06", "T1-08"],
    "T2-14": ["T1-06", "T1-07", "T1-08"],
    "T2-15": ["T1-07", "T1-08"],
    "T2-16": ["T1-07"],
    "T2-17": ["T1-09"],
    "T2-18": ["T1-09", "T1-10"],
    "T2-19": ["T1-09", "T1-10"],
    "T2-20": ["T1-10"],
    "T2-21": ["T1-10"],
    "T2-22": ["T1-11", "T1-12"],
    "T2-23": ["T1-11", "T1-12"],
    "T2-24": ["T1-11", "T1-12"],
    "T2-25": ["T1-12"],
    "T2-26": ["T1-12", "T1-01", "T1-03"],
}

# ============================================================================
# 4. GAP ROUTING  (specification section 4) - takes priority over KPI routing
# ============================================================================

GAP_AGENT_MAP: Dict[str, Dict[str, object]] = {
    "GAP-01": {"primary": "HR_REC_01", "supporting": ["HR_REC_02"],
               "issue": "Incomplete hiring demand or requisition information",
               "focus": "Mandatory intake fields, job profile templates, completeness checks"},
    "GAP-02": {"primary": "HR_REC_02", "supporting": ["HR_REC_14"],
               "issue": "Delayed budget or headcount validation",
               "focus": "Approval queue ownership, aging alerts, escalation rules"},
    "GAP-03": {"primary": "HR_REC_02", "supporting": ["HR_REC_14"],
               "issue": "Slow or repeated requisition approvals",
               "focus": "Approval routing, delegation, validation before submission"},
    "GAP-04": {"primary": "HR_REC_03", "supporting": ["HR_REC_16"],
               "issue": "Delayed recruiter assignment",
               "focus": "Workload balancing, priority-based assignment, assignment SLA"},
    "GAP-05": {"primary": "HR_REC_01", "supporting": ["HR_REC_04"],
               "issue": "Job description or sourcing readiness delays",
               "focus": "Role clarification, standardized job descriptions, sourcing readiness"},
    "GAP-06": {"primary": "HR_REC_04", "supporting": ["HR_REC_09"],
               "issue": "Hiring manager shortlist review delay",
               "focus": "Shortlist review SLA, decision ownership, escalation"},
    "GAP-07": {"primary": "HR_REC_05", "supporting": ["HR_REC_03"],
               "issue": "Screening backlog or repeated screening",
               "focus": "Screening queues, structured criteria, workload allocation"},
    "GAP-08": {"primary": "HR_REC_06", "supporting": ["HR_REC_11"],
               "issue": "Interview scheduling and rescheduling delays",
               "focus": "Panel availability, scheduling coordination, candidate updates"},
    "GAP-09": {"primary": "HR_REC_07", "supporting": ["HR_REC_14"],
               "issue": "Late or incomplete interview feedback",
               "focus": "Feedback scorecards, reminders, mandatory feedback fields"},
    "GAP-10": {"primary": "HR_REC_08", "supporting": ["HR_REC_13"],
               "issue": "Assessment completion or integration delays",
               "focus": "Assessment reminders, provider SLA, result synchronization"},
    "GAP-11": {"primary": "HR_REC_09", "supporting": ["HR_REC_07"],
               "issue": "Delayed or repeated selection decisions",
               "focus": "Decision ownership, feedback consolidation, additional-round control"},
    "GAP-12": {"primary": "HR_REC_10", "supporting": ["HR_REC_14"],
               "issue": "Compensation approval loops and revisions",
               "focus": "Compensation-band validation, approval matrix, proposal completeness"},
    "GAP-13": {"primary": "HR_REC_10", "supporting": ["HR_REC_11"],
               "issue": "Delayed offers or repeated negotiation",
               "focus": "Offer turnaround, expectation alignment, timely communication"},
    "GAP-14": {"primary": "HR_REC_11", "supporting": ["stage_specialist"],
               "issue": "Candidate withdrawal associated with process delays",
               "focus": "Candidate communication, inactivity alerts, stage-specific intervention",
               "caution": "Do not conclude that the delay caused the withdrawal. Identify the "
                          "preceding events and ask the relevant stage specialist to investigate "
                          "the observed delay."},
    "GAP-15": {"primary": "HR_REC_12", "supporting": ["HR_REC_11"],
               "issue": "Pre-joining, verification or documentation delays",
               "focus": "Document checklist, exception ownership, joining-readiness tracking"},
    "GAP-16": {"primary": "HR_REC_13", "supporting": ["HR_REC_15"],
               "issue": "Manual handoffs or system integration failures",
               "focus": "Interface monitoring, retry handling, duplicate prevention, reconciliation"},
    "GAP-17": {"primary": "HR_REC_03", "supporting": ["HR_REC_15"],
               "issue": "Recruiter workload or capacity constraints",
               "focus": "Weighted workload model, portfolio balancing, capacity planning"},
    "GAP-18": {"primary": "HR_REC_14", "supporting": ["HR_REC_13"],
               "issue": "Process governance violations or missing mandatory controls",
               "focus": "Workflow controls, policy-aware validation, exception approval and "
                        "audit trail"},
}

# ============================================================================
# 5. GAP x TIER 2 EVIDENCE  (specification section 5)
# ============================================================================
# The diagnostic KPIs that corroborate each gap. Candidate evidence, not a trigger on their own.

GAP_TIER2_MAP: Dict[str, List[str]] = {
    "GAP-01": ["T2-18", "T2-17"],
    "GAP-02": ["T2-01", "T2-03", "T2-07"],
    "GAP-03": ["T2-01", "T2-07", "T2-18"],
    "GAP-04": ["T2-04", "T2-22"],
    "GAP-05": ["T2-02", "T2-05", "T2-18"],
    "GAP-06": ["T2-03", "T2-07", "T2-10"],
    "GAP-07": ["T2-02", "T2-09", "T2-17"],
    "GAP-08": ["T2-03", "T2-06", "T2-10"],
    "GAP-09": ["T2-07", "T2-19"],
    "GAP-10": ["T2-02", "T2-11", "T2-26"],
    "GAP-11": ["T2-03", "T2-07", "T2-17"],
    "GAP-12": ["T2-07", "T2-14", "T2-18"],
    "GAP-13": ["T2-12", "T2-13"],
    "GAP-14": ["T2-08", "T2-10", "T2-12", "T2-15"],
    "GAP-15": ["T2-07", "T2-15", "T2-16"],
    "GAP-16": ["T2-23", "T2-24", "T2-26"],
    "GAP-17": ["T2-22", "T2-23", "T2-24"],
    "GAP-18": ["T2-18", "T2-20", "T2-21"],
}

# Resolves "relevant stage specialist" in TIER1_SUPPORTING_MAP and GAP-14.
STAGE_AGENT_MAP: Dict[str, str] = {
    "S1": "HR_REC_01", "S2": "HR_REC_02", "S3": "HR_REC_03", "S4": "HR_REC_04",
    "S5": "HR_REC_05", "S6": "HR_REC_06", "S7": "HR_REC_09", "S8": "HR_REC_10",
    "S9": "HR_REC_12", "S0": "HR_REC_13",
}

STAGE_SPECIALIST = "stage_specialist"
UNHEALTHY = ("Critical", "Watch")


# ============================================================================
# 6. MAP SELF-VALIDATION
# ============================================================================

def validate_maps() -> List[str]:
    """Check the maps against each other. Returns a list of problems (empty = clean)."""
    problems = []
    known = set(AGENT_CATALOGUE)

    def check_agent(agent_id, where):
        if agent_id == STAGE_SPECIALIST:
            return
        if agent_id not in known:
            problems.append(f"{where}: unknown agent id {agent_id!r}")

    for kpi, agent in TIER1_AGENT_MAP.items():
        check_agent(agent, f"TIER1_AGENT_MAP[{kpi}]")
    for kpi, agents in TIER1_SUPPORTING_MAP.items():
        if kpi not in TIER1_AGENT_MAP:
            problems.append(f"TIER1_SUPPORTING_MAP[{kpi}]: no primary agent for this KPI")
        for a in agents:
            check_agent(a, f"TIER1_SUPPORTING_MAP[{kpi}]")
    for kpi, agent in TIER2_AGENT_MAP.items():
        check_agent(agent, f"TIER2_AGENT_MAP[{kpi}]")
    for gap, route in GAP_AGENT_MAP.items():
        check_agent(route["primary"], f"GAP_AGENT_MAP[{gap}].primary")
        for a in route.get("supporting") or []:
            check_agent(a, f"GAP_AGENT_MAP[{gap}].supporting")
    for stage, agent in STAGE_AGENT_MAP.items():
        check_agent(agent, f"STAGE_AGENT_MAP[{stage}]")

    # every Tier 2 must declare the Tier 1 KPIs it feeds, and those must exist
    for kpi in TIER2_AGENT_MAP:
        if kpi not in TIER1_TIER2_MAP:
            problems.append(f"TIER1_TIER2_MAP: {kpi} has no Tier 1 KPIs declared")
    for kpi, t1s in TIER1_TIER2_MAP.items():
        if kpi not in TIER2_AGENT_MAP:
            problems.append(f"TIER1_TIER2_MAP[{kpi}]: not in TIER2_AGENT_MAP")
        for t1 in t1s:
            if t1 not in TIER1_AGENT_MAP:
                problems.append(f"TIER1_TIER2_MAP[{kpi}]: unknown Tier 1 KPI {t1}")

    # every gap must have both a route and its evidence set, and the evidence must be real KPIs
    for gap in GAP_AGENT_MAP:
        if gap not in GAP_TIER2_MAP:
            problems.append(f"GAP_TIER2_MAP: {gap} has no evidence KPIs declared")
    for gap, kpis in GAP_TIER2_MAP.items():
        if gap not in GAP_AGENT_MAP:
            problems.append(f"GAP_TIER2_MAP[{gap}]: not in GAP_AGENT_MAP")
        for k in kpis:
            if k not in TIER2_AGENT_MAP:
                problems.append(f"GAP_TIER2_MAP[{gap}]: unknown Tier 2 KPI {k}")

    expected_gaps = {f"GAP-{i:02d}" for i in range(1, 19)}
    if set(GAP_AGENT_MAP) != expected_gaps:
        problems.append(f"GAP_AGENT_MAP covers {sorted(set(GAP_AGENT_MAP))}, expected GAP-01..GAP-18")
    expected_t1 = {f"T1-{i:02d}" for i in range(1, 13)}
    if set(TIER1_AGENT_MAP) != expected_t1:
        problems.append(f"TIER1_AGENT_MAP covers {sorted(set(TIER1_AGENT_MAP))}, expected T1-01..T1-12")
    expected_t2 = {f"T2-{i:02d}" for i in range(1, 27)}
    if set(TIER2_AGENT_MAP) != expected_t2:
        problems.append(f"TIER2_AGENT_MAP covers {sorted(set(TIER2_AGENT_MAP))}, expected T2-01..T2-26")

    unreachable = known - (set(TIER1_AGENT_MAP.values()) | set(TIER2_AGENT_MAP.values())
                           | {r["primary"] for r in GAP_AGENT_MAP.values()}
                           | {a for r in GAP_AGENT_MAP.values() for a in (r.get("supporting") or [])}
                           | {a for v in TIER1_SUPPORTING_MAP.values() for a in v})
    if unreachable:
        problems.append(f"agents defined but never routed to: {sorted(unreachable)}")
    return problems


_PROBLEMS = validate_maps()
if _PROBLEMS:                       # fail loudly at import - a broken map must never ship quietly
    raise RuntimeError("recommendation_agent mapping is inconsistent:\n  - "
                       + "\n  - ".join(_PROBLEMS))


# ============================================================================
# 7. SELECTION
# ============================================================================

def agent(agent_id: str, stage_code: Optional[str] = None) -> Optional[Dict[str, str]]:
    """Resolve an agent id to its catalogue entry, resolving the stage specialist placeholder."""
    if agent_id == STAGE_SPECIALIST:
        agent_id = STAGE_AGENT_MAP.get(stage_code or "", "")
        if not agent_id:
            return None
    entry = AGENT_CATALOGUE.get(agent_id)
    return {"agent_id": agent_id, **entry} if entry else None


def _unhealthy_tier2(tier2_rows: List[dict]) -> Dict[str, dict]:
    """Tier 2 KPIs currently breaching, keyed by id. The corroboration test."""
    return {r["kpi_id"]: r for r in tier2_rows or []
            if (r.get("health") in UNHEALTHY)}


def _confidence(gap_detected: bool, corroborating: List[str], tier1_critical: bool) -> str:
    if gap_detected and corroborating and tier1_critical:
        return "High"
    if gap_detected and (corroborating or tier1_critical):
        return "Medium"
    return "Low"


def _measure_after(gap_id: Optional[str], tier2_ids: List[str],
                   tier1_impact: Optional[str] = None) -> Dict[str, List[str]]:
    """The KPIs to re-measure after the intervention lands."""
    t1 = set()
    for t2 in tier2_ids:
        t1.update(TIER1_TIER2_MAP.get(t2, []))
    for part in str(tier1_impact or "").replace(";", ",").split(","):
        part = part.strip()
        if part in TIER1_AGENT_MAP:
            t1.add(part)
    return {"tier1_kpis": sorted(t1), "tier2_kpis": sorted(set(tier2_ids))}


def route_gap(gap_id: str, tier2_rows: List[dict] = None, tier1_row: dict = None,
              detections: dict = None, stage_code: str = None) -> Optional[dict]:
    """Priority 1 - gap-level routing. Returns None when the gap is not in the map."""
    route = GAP_AGENT_MAP.get(gap_id)
    if not route:
        return None

    evidence_ids = GAP_TIER2_MAP.get(gap_id, [])
    breaching = _unhealthy_tier2(tier2_rows or [])
    corroborating = [k for k in evidence_ids if k in breaching]
    detected_cases = int((detections or {}).get("cases") or 0)
    gap_detected = detected_cases > 0
    tier1_critical = (tier1_row or {}).get("health") == "Critical"

    primary = agent(route["primary"], stage_code)
    supporting = [a for a in (agent(s, stage_code) for s in route.get("supporting") or []) if a]

    return {
        "routing_basis": "gap",
        "gap_reference": gap_id,
        "issue": route["issue"],
        "triggered": gap_detected,
        "trigger_rule": "the gap is evidenced in gap_detection; corroborating Tier 2 KPIs raise "
                        "confidence but are not themselves the trigger",
        "detected_cases": detected_cases or None,
        "primary_agent": primary,
        "supporting_agents": supporting,
        "intervention_focus": route["focus"],
        "caution": route.get("caution"),
        "evidence_tier2_expected": evidence_ids,
        "evidence_tier2_breaching": corroborating,
        "confidence": _confidence(gap_detected, corroborating, tier1_critical),
        "measure_after": _measure_after(gap_id, evidence_ids),
    }


def route_tier2(kpi_id: str, tier2_row: dict = None, stage_code: str = None) -> Optional[dict]:
    """Priority 2 - diagnostic routing when no gap is confirmed."""
    agent_id = TIER2_AGENT_MAP.get(kpi_id)
    if not agent_id:
        return None
    health = (tier2_row or {}).get("health")
    return {
        "routing_basis": "tier2",
        "tier2_kpi_id": kpi_id,
        "tier2_kpi_name": (tier2_row or {}).get("kpi_name"),
        "triggered": health in UNHEALTHY,
        "trigger_rule": "the diagnostic KPI is Critical or Watch in this KPI run",
        "health": health,
        "primary_agent": agent(agent_id, stage_code),
        "supporting_agents": [],
        "intervention_focus": f"Investigate {(tier2_row or {}).get('kpi_name') or kpi_id} and "
                              f"confirm which documented gap, if any, it evidences.",
        "evidence_tier2_breaching": [kpi_id] if health in UNHEALTHY else [],
        "confidence": "Medium" if health == "Critical" else "Low",
        "measure_after": _measure_after(None, [kpi_id]),
    }


def route_tier1(kpi_id: str, tier1_row: dict = None, stage_code: str = None) -> Optional[dict]:
    """Priority 3 - headline routing. Investigates drivers; never concludes a root cause."""
    agent_id = TIER1_AGENT_MAP.get(kpi_id)
    if not agent_id:
        return None
    health = (tier1_row or {}).get("health")
    drivers = sorted(k for k, t1s in TIER1_TIER2_MAP.items() if kpi_id in t1s)
    supporting = [a for a in (agent(s, stage_code)
                              for s in TIER1_SUPPORTING_MAP.get(kpi_id, [])) if a]
    return {
        "routing_basis": "tier1",
        "tier1_kpi_id": kpi_id,
        "tier1_kpi_name": (tier1_row or {}).get("kpi_name"),
        "triggered": health in UNHEALTHY,
        "trigger_rule": "the headline KPI is Critical or Watch and no gap or diagnostic has been "
                        "confirmed",
        "health": health,
        "primary_agent": agent(agent_id, stage_code),
        "supporting_agents": supporting,
        "intervention_focus": "Identify which Tier 2 drivers explain this deviation before "
                              "recommending an intervention.",
        "candidate_tier2_drivers": drivers,
        "confidence": "Low",
        "limitation": "A Tier 1 KPI alone does not establish a root cause. This route exists to "
                      "direct investigation, not to produce a definitive finding.",
        "measure_after": {"tier1_kpis": [kpi_id], "tier2_kpis": drivers},
    }


# ============================================================================
# 8. THE RECOMMENDATION ENGINE
# ============================================================================

def recommend(tier1_rows: List[dict] = None, tier2_rows: List[dict] = None,
              gap_rows: List[dict] = None, detections: Dict[str, dict] = None,
              stage_code: str = None, focus_tier1: List[str] = None,
              include_untriggered: bool = False) -> dict:
    """Select agents for a KPI run, applying the documented priority order.

    Pure function: it takes plain dicts, so it can be unit-tested and run against a CSV extract
    just as well as against PostgreSQL.

        tier1_rows   kpi_tier1_health rows      (kpi_id, kpi_name, health, ...)
        tier2_rows   kpi_tier2_diagnostics rows (kpi_id, kpi_name, health, gap_reference, ...)
        gap_rows     gap_mapping rows           (gap_reference, tier1_impact, ...)
        detections   gap_reference -> {"cases": n}, from gap_detection
        focus_tier1  restrict to these Tier 1 KPIs (e.g. the Critical ones only)
    """
    tier1_rows = tier1_rows or []
    tier2_rows = tier2_rows or []
    detections = detections or {}
    gap_meta = {g["gap_reference"]: g for g in (gap_rows or [])}
    t1_by_id = {r["kpi_id"]: r for r in tier1_rows}
    t2_by_id = {r["kpi_id"]: r for r in tier2_rows}

    wanted_t1 = set(focus_tier1) if focus_tier1 else {
        r["kpi_id"] for r in tier1_rows if r.get("health") in UNHEALTHY}

    routes, covered_t2, covered_t1 = [], set(), set()

    # ---- priority 1: every detected gap
    for gap_id in sorted(GAP_AGENT_MAP):
        route = route_gap(gap_id, tier2_rows, None, detections.get(gap_id), stage_code)
        if not route:
            continue
        impacted_t1 = _measure_after(gap_id, GAP_TIER2_MAP.get(gap_id, []),
                                     (gap_meta.get(gap_id) or {}).get("tier1_impact"))
        route["measure_after"] = impacted_t1
        route["tier1_in_scope"] = sorted(set(impacted_t1["tier1_kpis"]) & wanted_t1) or None
        # confidence is raised when a Tier 1 KPI this gap impacts is itself Critical
        t1_critical = any((t1_by_id.get(k) or {}).get("health") == "Critical"
                          for k in impacted_t1["tier1_kpis"])
        route["confidence"] = _confidence(route["triggered"],
                                          route["evidence_tier2_breaching"], t1_critical)
        if route["triggered"] or include_untriggered:
            routes.append(route)
            covered_t2.update(route["evidence_tier2_breaching"])
            covered_t1.update(route["tier1_in_scope"] or [])

    # ---- priority 2: breaching diagnostics no detected gap already explains
    for kpi_id in sorted(TIER2_AGENT_MAP):
        row = t2_by_id.get(kpi_id)
        if kpi_id in covered_t2:
            continue
        if not row or row.get("health") not in UNHEALTHY:
            continue
        if focus_tier1 and not (set(TIER1_TIER2_MAP.get(kpi_id, [])) & wanted_t1):
            continue
        route = route_tier2(kpi_id, row, stage_code)
        if route and (route["triggered"] or include_untriggered):
            routes.append(route)
            covered_t1.update(set(TIER1_TIER2_MAP.get(kpi_id, [])) & wanted_t1)

    # ---- priority 3: headline KPIs nothing above has reached
    for kpi_id in sorted(wanted_t1 - covered_t1):
        row = t1_by_id.get(kpi_id)
        route = route_tier1(kpi_id, row, stage_code)
        if route and (route["triggered"] or include_untriggered):
            routes.append(route)

    by_agent: Dict[str, dict] = {}
    for r in routes:
        a = r.get("primary_agent") or {}
        if not a:
            continue
        entry = by_agent.setdefault(a["agent_id"], {**a, "routes": [], "max_confidence": "Low"})
        entry["routes"].append(r.get("gap_reference") or r.get("tier2_kpi_id")
                               or r.get("tier1_kpi_id"))
        order = {"Low": 0, "Medium": 1, "High": 2}
        if order[r["confidence"]] > order[entry["max_confidence"]]:
            entry["max_confidence"] = r["confidence"]

    return {
        "routing_priority": ["gap", "tier2", "tier1"],
        "tier1_in_scope": sorted(wanted_t1),
        "routes": routes,
        "agents_invoked": sorted(by_agent.values(),
                                 key=lambda a: (-len(a["routes"]), a["agent_id"])),
        "deterministic": True,
        "note": "Agent selection is deterministic and version-controlled. An LLM may draft the "
                "wording of each recommendation from the evidence supplied, but may not select "
                "an agent or restate a KPI value.",
    }


def recommend_for_finding(finding: dict, tier1_rows: List[dict] = None,
                          tier2_rows: List[dict] = None, gap_rows: List[dict] = None,
                          detections: Dict[str, dict] = None,
                          stage_code: str = None) -> dict:
    """Route one stored root cause (a root_cause_finding row) to its agents.

    Uses the gap and Tier 2 ids the agent itself cited, so the recommendation addresses the
    finding rather than re-diagnosing the run.
    """
    from ..services import fix_selectors

    selectors = fix_selectors.extract(finding)
    gap_ids = selectors.get("gap_references") or []
    t2_ids = selectors.get("tier2_kpi_ids") or []
    t1_id = selectors.get("tier1_kpi_id")
    stage_code = stage_code or next(iter(selectors.get("stage_codes") or []), None)

    tier2_rows = tier2_rows or []
    t1_row = next((r for r in (tier1_rows or []) if r.get("kpi_id") == t1_id), None)
    gap_meta = {g["gap_reference"]: g for g in (gap_rows or [])}
    detections = detections or {}

    routes = []
    for gap_id in gap_ids:
        route = route_gap(gap_id, tier2_rows, t1_row, detections.get(gap_id), stage_code)
        if route:
            route["measure_after"] = _measure_after(
                gap_id, GAP_TIER2_MAP.get(gap_id, []),
                (gap_meta.get(gap_id) or {}).get("tier1_impact"))
            routes.append(route)

    if not routes:                       # no gap cited: fall back to the diagnostics, then the KPI
        for kpi_id in t2_ids:
            route = route_tier2(kpi_id, next((r for r in tier2_rows
                                              if r.get("kpi_id") == kpi_id), None), stage_code)
            if route:
                routes.append(route)
    if not routes and t1_id:
        route = route_tier1(t1_id, t1_row, stage_code)
        if route:
            routes.append(route)

    primary = routes[0] if routes else None
    return {
        "finding_id": finding.get("finding_id"),
        "root_cause": finding.get("root_cause"),
        "root_cause_type": finding.get("root_cause_type"),
        "finding_confidence": finding.get("confidence"),
        "tier1_kpi_id": t1_id,
        "tier1_kpi_name": finding.get("tier1_kpi_name"),
        "gap_references": gap_ids,
        "tier2_kpi_ids": t2_ids,
        "routing_basis": (primary or {}).get("routing_basis"),
        "primary_agent": (primary or {}).get("primary_agent"),
        "accountable_owner": ((primary or {}).get("primary_agent") or {}).get("owner"),
        "supporting_agents": (primary or {}).get("supporting_agents") or [],
        "intervention_focus": (primary or {}).get("intervention_focus"),
        "caution": (primary or {}).get("caution"),
        "measure_after": (primary or {}).get("measure_after"),
        "confidence": (primary or {}).get("confidence"),
        "routes": routes,
        "deterministic": True,
    }


# ============================================================================
# 9. DATABASE ADAPTER
# ============================================================================

def _run_context(conn, run_id: Optional[int] = None) -> dict:
    """Read the KPI run and gap evidence the engine needs."""
    from .. import db
    s = db.schema()
    if run_id is None:
        run_id = db.latest_run_id(conn)
    if run_id is None:
        raise RuntimeError("No KPI run found. Run 04_compute_kpis_to_postgres.py first.")
    tier1 = db.rows(conn, f"""SELECT kpi_id, kpi_name, current_value, target, unit, health,
                                     variance_vs_target_pct, cohort_rule
                              FROM {s}.kpi_tier1_health WHERE run_id = %s ORDER BY kpi_id;""",
                    [run_id])
    tier2 = db.rows(conn, f"""SELECT kpi_id, kpi_name, current_value, target, unit, health,
                                     variance_vs_target_pct, feeds_tier1_kpi, gap_reference
                              FROM {s}.kpi_tier2_diagnostics WHERE run_id = %s ORDER BY kpi_id;""",
                    [run_id])
    gaps = db.rows(conn, f"""SELECT gap_reference, stage_or_domain, gap_definition, tier1_impact,
                                    tier2_evidence, intervention_options
                             FROM {s}.gap_mapping ORDER BY gap_reference;""")
    detections = {d["gap_reference"]: d for d in db.rows(conn, f"""
        SELECT gap_reference, COUNT(*) AS detections,
               COUNT(DISTINCT process_instance_id) AS cases
        FROM {s}.gap_detection GROUP BY gap_reference;""")}
    return {"run_id": run_id, "tier1": tier1, "tier2": tier2, "gaps": gaps,
            "detections": detections}


def recommend_from_db(conn=None, run_id: Optional[int] = None, finding_id: str = None,
                      focus_tier1: List[str] = None, include_untriggered: bool = False) -> dict:
    """Run the engine against PostgreSQL, for a whole run or for one stored root cause."""
    from .. import db
    if conn is None:
        with db.connection() as c:
            return recommend_from_db(c, run_id, finding_id, focus_tier1, include_untriggered)

    ctx = _run_context(conn, run_id)
    if finding_id:
        from ..services import root_cause_store
        finding = root_cause_store.get_finding(conn, finding_id.upper())
        if not finding:
            raise LookupError(f"No root cause {finding_id.upper()} in the stored analysis.")
        out = recommend_for_finding(finding, ctx["tier1"], ctx["tier2"], ctx["gaps"],
                                    ctx["detections"])
        out["run_id"] = ctx["run_id"]
        return out

    out = recommend(ctx["tier1"], ctx["tier2"], ctx["gaps"], ctx["detections"],
                    focus_tier1=focus_tier1, include_untriggered=include_untriggered)
    out["run_id"] = ctx["run_id"]
    return out


# ============================================================================
# 10. CLI
# ============================================================================

def _print_mapping() -> None:
    print(f"AGENT CATALOGUE ({len(AGENT_CATALOGUE)} agents)")
    for aid, a in AGENT_CATALOGUE.items():
        print(f"  {aid}  {a['name']:<34} {a['domain']:<24} owner: {a['owner']}")
    print("\nGAP ROUTING (takes priority)")
    for gap, r in GAP_AGENT_MAP.items():
        sup = ", ".join(r.get("supporting") or []) or "-"
        print(f"  {gap}  primary {r['primary']}  supporting {sup:<26} "
              f"evidence {', '.join(GAP_TIER2_MAP.get(gap, []))}")
    print("\nTIER 2 ROUTING (fallback)")
    for kpi, aid in TIER2_AGENT_MAP.items():
        print(f"  {kpi} -> {aid}  {AGENT_CATALOGUE[aid]['name']:<34} "
              f"feeds {', '.join(TIER1_TIER2_MAP.get(kpi, []))}")
    print("\nTIER 1 ROUTING (last fallback)")
    for kpi, aid in TIER1_AGENT_MAP.items():
        sup = ", ".join(TIER1_SUPPORTING_MAP.get(kpi, [])) or "-"
        print(f"  {kpi} -> {aid}  {AGENT_CATALOGUE[aid]['name']:<34} supporting {sup}")


def main() -> None:
    import argparse
    import json

    p = argparse.ArgumentParser(
        description="Deterministic recommendation-agent selection for Talent Acquisition")
    p.add_argument("--run-id", type=int, default=None, help="KPI run to route (default: latest)")
    p.add_argument("--finding-id", default=None, help="route one stored root cause, e.g. RC-T1-01-1")
    p.add_argument("--focus-tier1", nargs="*", default=None, help="restrict to these Tier 1 KPIs")
    p.add_argument("--include-untriggered", action="store_true",
                   help="also show routes whose trigger condition is not met")
    p.add_argument("--show-mapping", action="store_true",
                   help="print the full deterministic mapping and exit")
    p.add_argument("--validate", action="store_true", help="validate the maps and exit")
    p.add_argument("--json", action="store_true", help="emit JSON instead of a readable summary")
    a = p.parse_args()

    if a.validate:
        problems = validate_maps()
        print("\n".join(f"  - {x}" for x in problems) if problems
              else f"Mapping is consistent: {len(AGENT_CATALOGUE)} agents, "
                   f"{len(GAP_AGENT_MAP)} gaps, {len(TIER1_AGENT_MAP)} Tier 1, "
                   f"{len(TIER2_AGENT_MAP)} Tier 2 KPIs, all agents reachable.")
        raise SystemExit(1 if problems else 0)

    if a.show_mapping:
        _print_mapping()
        return

    try:
        out = recommend_from_db(run_id=a.run_id, finding_id=a.finding_id,
                                focus_tier1=a.focus_tier1,
                                include_untriggered=a.include_untriggered)
    except ModuleNotFoundError as exc:
        raise SystemExit(f"A PostgreSQL driver is required: {exc}. Install psycopg2-binary, or "
                         f"use --show-mapping / --validate which need no database.")
    except Exception as exc:
        raise SystemExit(f"Could not route this KPI run: {exc}")

    if a.json:
        print(json.dumps(out, indent=2, default=str))
        return

    if a.finding_id:
        print(f"{out['finding_id']}  {out['root_cause']}")
        print(f"  routing basis    : {out['routing_basis']}")
        pa = out.get("primary_agent") or {}
        print(f"  primary agent    : {pa.get('agent_id')} {pa.get('name')}")
        print(f"  accountable owner: {out.get('accountable_owner')}")
        print(f"  supporting       : "
              f"{', '.join(s['agent_id'] for s in out['supporting_agents']) or '-'}")
        print(f"  intervention     : {out.get('intervention_focus')}")
        print(f"  confidence       : {out.get('confidence')}")
        m = out.get("measure_after") or {}
        print(f"  measure after    : {', '.join(m.get('tier1_kpis') or [])} | "
              f"{', '.join(m.get('tier2_kpis') or [])}")
        if out.get("caution"):
            print(f"  caution          : {out['caution']}")
        return

    print(f"KPI run {out['run_id']}  -  Tier 1 in scope: {', '.join(out['tier1_in_scope']) or 'none'}")
    print(f"{len(out['routes'])} route(s), {len(out['agents_invoked'])} agent(s) invoked\n")
    for r in out["routes"]:
        key = r.get("gap_reference") or r.get("tier2_kpi_id") or r.get("tier1_kpi_id")
        pa = r.get("primary_agent") or {}
        print(f"  [{r['routing_basis']:<6}] {key:<8} -> {pa.get('agent_id')} {pa.get('name')}")
        print(f"            confidence {r['confidence']}"
              + (f", {r['detected_cases']} cases detected" if r.get("detected_cases") else "")
              + (f", corroborated by {', '.join(r['evidence_tier2_breaching'])}"
                 if r.get("evidence_tier2_breaching") else ", no Tier 2 corroboration"))
        print(f"            focus: {r.get('intervention_focus')}")
        print(f"            owner: {pa.get('owner')}")
    print("\nagents invoked")
    for a_ in out["agents_invoked"]:
        print(f"  {a_['agent_id']}  {a_['name']:<34} {len(a_['routes'])} route(s) "
              f"({', '.join(a_['routes'])}), max confidence {a_['max_confidence']}")


if __name__ == "__main__":
    main()
