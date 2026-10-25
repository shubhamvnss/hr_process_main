"""
Synthetic Talent Acquisition event-log generator for Deloitte Kinetic.

Source specification : Deloitte_Kinetic_Talent_Acquisition_BRD.pages
                       (Prototype baseline, 19 October 2026)
Case perspective     : requisition_id is the primary lifecycle case; candidate
                       applications are a second, linked case type (BRD 6.1).

Outputs (data/):
  ta_event_log.csv              one row per event, BRD 6.2 canonical schema,
                                case_type = REQUISITION | APPLICATION
  ta_requisition_master.csv     one row per requisition, derived BRD 6.3 attrs
  ta_application_master.csv     one row per candidate application
  ta_stage_transitions.csv      stage entry/exit per case occurrence  (T2-02/T2-03)
  ta_recruiter_load_snapshot.csv weekly open + weighted load per recruiter (T2-22)
  ta_activity_master.csv        canonical activity dictionary (BRD section 4)
  ta_sla_definitions.csv        illustrative SLA profile used by the generator
  ta_gap_mapping.csv            GAP-01..GAP-18 reference library (BRD section 10)
  ta_kpi_tier1.csv              Tier 1 KPI catalogue (BRD section 8)
  ta_kpi_tier2.csv              Tier 2 diagnostic KPI catalogue (BRD section 9)
  ta_gap_detection.csv          gaps DERIVED from the event log by deterministic
                                rules - no generation label is used
  ta_scenario_ground_truth.csv  what the generator intended, for validating the
                                detector only (never an analytical input)
  ta_data_dictionary.csv

Design notes
  * gap_reference is left EMPTY in ta_event_log.csv. Gaps must be discovered from
    event evidence + SLA rules (ta_gap_detection.csv), per BRD section 10.
  * Pain points are produced by causal chains (overloaded recruiter -> aged queue,
    panel unavailable -> reschedule -> late feedback -> long candidate wait ->
    withdrawal hazard), not by sprinkling random long durations.
  * Counterexamples are included: resilient candidates who stay through delays,
    impatient candidates who withdraw with no delay, and genuinely complex senior
    roles that legitimately take longer.
  * Open cases are right-censored at the extraction cut-off, never completed.
"""
import csv, math, os, random, statistics as stx
from collections import Counter, defaultdict
from datetime import datetime, timedelta

SEED = 20261019
random.seed(SEED)

GEN_VERSION     = "ta-synth-v1.0"
EXTRACTION_BATCH = "BATCH-2026-10-19-01"
CUTOFF          = datetime(2026, 10, 19, 9, 0)      # reporting cut-off / "today"
WINDOW_START    = datetime(2025, 4, 1, 9, 0)
WINDOW_END      = datetime(2026, 8, 7, 17, 0)        # last demand-raised date
N_REQUISITIONS  = 1200

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")

# ------------------------------------------------------------------ business time
WORK_START, WORK_END = 9 * 60, 17 * 60 + 30          # 09:00 - 17:30
WORK_MIN = WORK_END - WORK_START                     # 510 business minutes / day

HOLIDAYS = set()
for y in (2025, 2026):
    for m, d in [(1, 1), (1, 26), (3, 14), (5, 1), (8, 15), (10, 2), (12, 25)]:
        HOLIDAYS.add(datetime(y, m, d).date())


def is_workday(d):
    return d.weekday() < 5 and d not in HOLIDAYS


def next_workday_start(dt):
    d = dt.date()
    while not is_workday(d):
        d += timedelta(days=1)
    return datetime.combine(d, datetime.min.time()) + timedelta(minutes=WORK_START)


def normalize(dt):
    """Move dt forward into business hours."""
    if not is_workday(dt.date()):
        return next_workday_start(dt)
    m = dt.hour * 60 + dt.minute
    if m < WORK_START:
        return dt.replace(hour=9, minute=0, second=0, microsecond=0)
    if m >= WORK_END:
        return next_workday_start(dt + timedelta(days=1))
    return dt


def add_bmin(dt, minutes):
    """Add business minutes."""
    dt = normalize(dt)
    minutes = int(round(max(0, minutes)))
    while minutes > 0:
        m = dt.hour * 60 + dt.minute
        left = WORK_END - m
        if minutes < left:
            return dt + timedelta(minutes=minutes)
        minutes -= left
        dt = next_workday_start(dt + timedelta(days=1))
    return dt


def bmin_between(a, b):
    """Business minutes between a and b."""
    if a is None or b is None or b <= a:
        return 0
    total, cur = 0.0, normalize(a)
    while cur < b:
        day_end = cur.replace(hour=17, minute=30, second=0, microsecond=0)
        seg_end = min(day_end, b)
        if seg_end > cur:
            total += (seg_end - cur).total_seconds() / 60.0
        cur = next_workday_start(cur + timedelta(days=1))
    return int(round(total))


def bdays(n):
    return n * WORK_MIN


def mins_to_bdays(m):
    return round(m / WORK_MIN, 2)


def U(a, b):
    return random.randint(a, b)


def T(lo, mode, hi):
    return random.triangular(lo, hi, mode)


def LN(median_days, sigma):
    """Log-normal wait in business minutes with a median in business days."""
    return max(10.0, median_days * math.exp(random.gauss(0, sigma)) * WORK_MIN)


def chance(p):
    return random.random() < p


def pick(seq):
    return random.choice(seq)


def wpick(pairs):
    """pairs = [(value, weight), ...]"""
    vals = [p[0] for p in pairs]
    wts = [p[1] for p in pairs]
    return random.choices(vals, weights=wts, k=1)[0]


# ------------------------------------------------------------------ master data
STAGES = [
    ("S1", "1. Hiring Demand", 1),
    ("S2", "2. Requisition & Approval", 2),
    ("S3", "3. Recruitment Initiation", 3),
    ("S4", "4. Sourcing", 4),
    ("S5", "5. Screening & Shortlisting", 5),
    ("S6", "6. Interview & Assessment", 6),
    ("S7", "7. Selection & Decision", 7),
    ("S8", "8. Offer Management", 8),
    ("S9", "9. Pre-joining & Closure", 9),
]
STAGE_NAME = {c: n for c, n, _ in STAGES}
STAGE_SEQ = {c: s for c, _, s in STAGES}

SYS_HRIS = "HRIS / Workforce Planning"
SYS_ATS = "Applicant Tracking System"
SYS_JOB = "Job Board / Sourcing Platform"
SYS_ASM = "Assessment Platform"
SYS_CAL = "Calendar / Scheduling Platform"
SYS_CMP = "Compensation / Offer Management"
SYS_ESG = "E-signature Platform"
SYS_BGV = "Background Verification Platform"
SYS_ONB = "HR Onboarding"
SYS_INT = "Integration / Audit Logs"

SRC_PREFIX = {
    SYS_HRIS: "HRIS", SYS_ATS: "ATS", SYS_JOB: "JOB", SYS_ASM: "ASM",
    SYS_CAL: "CAL", SYS_CMP: "CMP", SYS_ESG: "ESG", SYS_BGV: "BGV",
    SYS_ONB: "ONB", SYS_INT: "INT",
}

P_BIZMGR = "Business Manager"
P_WFP = "Workforce Planning Analyst"
P_HM = "Hiring Manager"
P_APPROVER = "Approving Business Leader"
P_TAOPS = "TA Operations Coordinator"
P_RECRUITER = "Recruiter"
P_SOURCER = "Sourcer"
P_COORD = "Recruiting Coordinator"
P_PANEL = "Interview Panel"
P_ASMCOORD = "Assessment Coordinator"
P_COMP = "Compensation Analyst"
P_COMPAPP = "Compensation Approver"
P_HROPS = "HR Operations"
P_CAND = "Candidate"
P_SYS = "System / Integration"

# activity dictionary: name -> (stage, system, persona, manual_default,
#                               rework_activity, exception_activity, description)
ACT = {}


def A(name, stage, system, persona, manual=False, rework=False, exc=False, desc=""):
    ACT[name] = dict(stage=stage, system=system, persona=persona,
                     manual=manual, rework=rework, exception=exc, desc=desc)
    return name


# --- Stage 1 : hiring demand -------------------------------------------------
DEMAND_RAISED = A("Hiring Demand Identified", "S1", SYS_HRIS, P_BIZMGR, True, False, False,
                  "Business need or replacement need is raised")
DEMAND_RETURNED = A("Hiring Demand Returned for Clarification", "S1", SYS_HRIS, P_WFP, True, True, False,
                    "Demand returned for missing grade, location, budget or justification")
DEMAND_RESUBMIT = A("Hiring Demand Resubmitted", "S1", SYS_HRIS, P_BIZMGR, True, True, False,
                    "Business manager resubmits corrected demand")
BUDGET_REQ = A("Workforce Budget Validation Requested", "S1", SYS_HRIS, P_WFP, False, False, False,
               "Headcount plan, budget and position availability validation requested")
BUDGET_REMIND = A("Workforce Budget Validation Reminder Sent", "S1", SYS_HRIS, P_TAOPS, True, False, False,
                  "Manual follow-up on pending budget validation")
BUDGET_OK = A("Workforce Budget Validated", "S1", SYS_HRIS, P_WFP, False, False, False,
              "Headcount and budget confirmed available")
BUDGET_REJECT = A("Workforce Budget Validation Rejected", "S1", SYS_HRIS, P_WFP, False, False, True,
                  "Headcount or budget not available")

# --- Stage 2 : requisition & approval ---------------------------------------
REQ_CREATED = A("Requisition Created", "S2", SYS_ATS, P_HM, True, False, False,
                "Requisition created with role, grade, location and justification")
REQ_SUBMIT = A("Requisition Submitted for Approval", "S2", SYS_ATS, P_HM, True, False, False,
               "Requisition submitted into the approval queue")
REQ_REMIND = A("Requisition Approval Reminder Sent", "S2", SYS_ATS, P_TAOPS, True, False, False,
               "Manual chase on a pending approval")
REQ_RETURNED = A("Requisition Returned for Clarification", "S2", SYS_ATS, P_APPROVER, True, True, False,
                 "Approver returns an incomplete approval packet")
REQ_RESUBMIT = A("Requisition Resubmitted", "S2", SYS_ATS, P_HM, True, True, False,
                 "Hiring manager resubmits the corrected requisition")
REQ_APPROVED = A("Requisition Approved", "S2", SYS_ATS, P_APPROVER, False, False, False,
                 "Requisition approved by the business leader")
REQ_REJECTED = A("Requisition Rejected", "S2", SYS_ATS, P_APPROVER, False, False, True,
                 "Requisition rejected")
REQ_HOLD = A("Requisition Put On Hold", "S2", SYS_ATS, P_APPROVER, True, False, True,
             "Requisition suspended pending business decision")
REQ_REOPEN = A("Requisition Reopened", "S2", SYS_ATS, P_TAOPS, True, True, False,
               "Requisition reactivated after hold or failed hire")

# --- Stage 3 : recruitment initiation ---------------------------------------
RECRUITER_ASSIGNED = A("Recruiter Assigned", "S3", SYS_ATS, P_TAOPS, True, False, False,
                       "Approved requisition assigned to a recruiter")
RECRUITER_REASSIGNED = A("Recruiter Reassigned", "S3", SYS_ATS, P_TAOPS, True, True, True,
                         "Requisition moved to another recruiter")
ASSIGN_REMIND = A("Recruiter Assignment Follow-up", "S3", SYS_ATS, P_HM, True, False, False,
                  "Hiring manager chases an unassigned approved requisition")
JD_FINAL = A("Job Description Finalized", "S3", SYS_ATS, P_RECRUITER, False, False, False,
             "Role profile and selection criteria finalized")
JD_REVISED = A("Job Description Revised", "S3", SYS_ATS, P_RECRUITER, True, True, False,
               "Role profile reworked after review or sourcing feedback")

# --- Stage 4 : sourcing ------------------------------------------------------
JOB_POSTED = A("Job Posted / Sourcing Launched", "S4", SYS_JOB, P_SOURCER, False, False, False,
               "Vacancy published and sourcing campaign launched")
SOURCING_EXTENDED = A("Sourcing Campaign Extended", "S4", SYS_JOB, P_SOURCER, True, True, False,
                      "Campaign extended or re-opened because the pipeline was insufficient")
AGENCY_ENGAGED = A("Recruitment Agency Engaged", "S4", SYS_JOB, P_SOURCER, True, False, False,
                   "External agency engaged for a hard-to-fill role")
MORE_PROFILES = A("Additional Profiles Requested", "S4", SYS_ATS, P_HM, True, True, False,
                  "Hiring manager rejects the shortlist and asks for more profiles")

# --- Stage 5 : screening & shortlisting (application level) -----------------
APP_RECEIVED = A("Application Received", "S5", SYS_ATS, P_SYS, False, False, False,
                 "Candidate application record created")
SCREEN_REMIND = A("Screening Follow-up Logged", "S5", SYS_ATS, P_TAOPS, True, False, False,
                  "Manual follow-up on an aged screening queue")
SCREEN_DONE = A("Resume Screening Completed", "S5", SYS_ATS, P_RECRUITER, True, False, False,
                "Minimum qualifications reviewed; shortlist or reject")
RESCREEN = A("Application Rescreened", "S5", SYS_ATS, P_RECRUITER, True, True, False,
             "Application screened again after criteria change or error")
SHORTLISTED = A("Candidate Shortlisted", "S5", SYS_ATS, P_RECRUITER, False, False, False,
                "Candidate meets shortlist criteria")
SHORTLIST_SUBMIT = A("Shortlist Submitted to Hiring Manager", "S5", SYS_ATS, P_RECRUITER, False, False, False,
                     "Shortlist handed to the hiring manager for review")
SHORTLIST_REMIND = A("Shortlist Review Reminder Sent", "S5", SYS_ATS, P_RECRUITER, True, False, False,
                     "Manual reminder on a pending hiring-manager review")
SHORTLIST_DECISION = A("Shortlist Review Decision Recorded", "S5", SYS_ATS, P_HM, True, False, False,
                       "Hiring manager accepts, rejects or asks for more profiles")

# --- Stage 6 : interview & assessment ---------------------------------------
ASM_ASSIGNED = A("Assessment Assigned", "S6", SYS_ASM, P_ASMCOORD, False, False, False,
                 "Technical, aptitude or role-specific assessment assigned")
ASM_REMIND = A("Assessment Reminder Sent", "S6", SYS_ASM, P_ASMCOORD, True, False, False,
               "Reminder on an overdue assessment")
ASM_DONE = A("Assessment Completed", "S6", SYS_ASM, P_CAND, False, False, False,
             "Candidate completes the assessment")
ASM_EXPIRED = A("Assessment Expired", "S6", SYS_ASM, P_SYS, False, False, True,
                "Assessment window closed before completion")
ASM_REISSUED = A("Assessment Reissued", "S6", SYS_ASM, P_ASMCOORD, True, True, False,
                 "Assessment re-assigned after expiry")
ASM_SYNCED = A("Assessment Result Synchronized", "S6", SYS_ASM, P_SYS, False, False, False,
               "Assessment result transferred to the ATS")
INT_REQUESTED = A("Interview Requested", "S6", SYS_ATS, P_RECRUITER, False, False, False,
                  "Interview round requested for a shortlisted candidate")
INT_SCHED_FOLLOWUP = A("Interview Scheduling Follow-up", "S6", SYS_CAL, P_COORD, True, False, False,
                       "Coordinator chases panel or candidate availability")
INT_CONFIRMED = A("Interview Confirmed", "S6", SYS_CAL, P_COORD, False, False, False,
                  "Invite accepted by candidate and panel; slot confirmed")
INT_RESCHEDULED = A("Interview Rescheduled", "S6", SYS_CAL, P_PANEL, True, True, False,
                    "Confirmed slot moved")
INT_CANCELLED = A("Interview Cancelled", "S6", SYS_CAL, P_COORD, True, True, True,
                  "Scheduled interview cancelled")
INT_CONDUCTED = A("Interview Conducted", "S6", SYS_ATS, P_PANEL, False, False, False,
                  "Interview held and attendance recorded")
INT_NOSHOW = A("Interview No-Show Recorded", "S6", SYS_ATS, P_PANEL, True, False, True,
               "Candidate or panel did not attend")
FB_REMIND = A("Interview Feedback Reminder Sent", "S6", SYS_ATS, P_COORD, True, False, False,
              "Reminder on outstanding interview feedback")
FB_SUBMITTED = A("Interview Feedback Submitted", "S6", SYS_ATS, P_PANEL, True, False, False,
                 "Structured feedback and recommendation submitted")
FB_RETURNED = A("Interview Feedback Returned for Completion", "S6", SYS_ATS, P_RECRUITER, True, True, False,
                "Feedback returned because required fields were missing")

# --- Stage 7 : selection & decision -----------------------------------------
EXTRA_ROUND = A("Additional Interview Round Requested", "S7", SYS_ATS, P_HM, True, True, False,
                "Hiring manager requests a further interview round")
SEL_DECISION = A("Selection Decision Recorded", "S7", SYS_ATS, P_HM, True, False, False,
                 "Interview results consolidated and decision recorded")
SEL_REOPENED = A("Selection Decision Reopened", "S7", SYS_ATS, P_HM, True, True, True,
                 "A recorded selection decision is reopened")
SEL_REMIND = A("Selection Decision Reminder Sent", "S7", SYS_ATS, P_RECRUITER, True, False, False,
               "Reminder on a pending selection decision")
CAND_REJECTED = A("Candidate Rejected", "S7", SYS_ATS, P_RECRUITER, True, False, False,
                  "Candidate rejected with a reason code")
CAND_WITHDREW = A("Candidate Withdrew", "S7", SYS_ATS, P_CAND, True, False, True,
                  "Candidate withdraws from the process")

# --- Stage 8 : offer management ---------------------------------------------
COMP_PROPOSED = A("Compensation Proposal Created", "S8", SYS_CMP, P_COMP, False, False, False,
                  "Compensation proposal prepared against band and expectations")
COMP_REVISED = A("Compensation Proposal Revised", "S8", SYS_CMP, P_COMP, True, True, False,
                 "Compensation proposal revised")
OFFER_APPR_REQ = A("Offer Approval Requested", "S8", SYS_CMP, P_COMP, False, False, False,
                   "Compensation and business approvals requested")
OFFER_APPR_REMIND = A("Offer Approval Reminder Sent", "S8", SYS_CMP, P_RECRUITER, True, False, False,
                      "Reminder on a pending offer approval")
OFFER_RETURNED = A("Offer Approval Returned for Revision", "S8", SYS_CMP, P_COMPAPP, True, True, False,
                   "Approver returns the proposal for rework")
OFFER_APPROVED = A("Offer Approved", "S8", SYS_CMP, P_COMPAPP, False, False, False,
                   "Compensation and business approval granted")
OFFER_GENERATED = A("Offer Generated", "S8", SYS_ESG, P_HROPS, False, False, False,
                    "Offer document generated")
OFFER_SENT = A("Offer Sent to Candidate", "S8", SYS_ESG, P_RECRUITER, False, False, False,
               "Offer released to the candidate with a response due date")
OFFER_VIEWED = A("Offer Viewed by Candidate", "S8", SYS_ESG, P_CAND, False, False, False,
                 "Candidate opened the offer document")
OFFER_FOLLOWUP = A("Offer Follow-up Logged", "S8", SYS_ATS, P_RECRUITER, True, False, False,
                   "Recruiter chases an outstanding offer response")
OFFER_NEGOTIATE = A("Offer Negotiation Requested", "S8", SYS_CMP, P_CAND, True, True, False,
                    "Candidate requests a change to the offer")
OFFER_REVISED = A("Offer Revised and Resent", "S8", SYS_ESG, P_RECRUITER, True, True, False,
                  "Revised offer version issued")
OFFER_ACCEPTED = A("Offer Accepted", "S8", SYS_ESG, P_CAND, False, False, False,
                   "Candidate accepts the offer")
OFFER_DECLINED = A("Offer Declined", "S8", SYS_ESG, P_CAND, False, False, True,
                   "Candidate declines the offer")
OFFER_EXPIRED = A("Offer Expired", "S8", SYS_ESG, P_SYS, False, False, True,
                  "Offer lapsed without a response")

# --- Stage 9 : pre-joining & closure ----------------------------------------
BGV_INIT = A("Background Verification Initiated", "S9", SYS_BGV, P_HROPS, False, False, False,
             "Background and eligibility checks initiated")
BGV_INFO_REQ = A("Verification Information Requested", "S9", SYS_BGV, P_HROPS, True, False, False,
                 "Additional information requested from the candidate or vendor")
BGV_EXCEPTION = A("Verification Exception Raised", "S9", SYS_BGV, P_HROPS, True, False, True,
                  "Discrepancy or unresolved check raised as an exception")
BGV_CLEARED = A("Background Verification Cleared", "S9", SYS_BGV, P_HROPS, False, False, False,
                "All required checks cleared")
DOC_REQUESTED = A("Pre-joining Documents Requested", "S9", SYS_ONB, P_HROPS, False, False, False,
                  "Joining document checklist issued")
DOC_RECEIVED = A("Pre-joining Documents Received", "S9", SYS_ONB, P_CAND, True, False, False,
                 "Candidate submits the required documents")
DOC_RESUBMIT_REQ = A("Document Resubmission Requested", "S9", SYS_ONB, P_HROPS, True, True, False,
                     "Documents incomplete or invalid; resubmission requested")
JOIN_READY = A("Joining Readiness Confirmed", "S9", SYS_ONB, P_HROPS, False, False, False,
               "Pre-joining steps complete and joining date confirmed")
CAND_JOINED = A("Candidate Joined", "S9", SYS_HRIS, P_HROPS, False, False, False,
                "Joining recorded and employee record created")
CAND_NO_JOIN = A("Candidate Did Not Join", "S9", SYS_HRIS, P_HROPS, True, False, True,
                 "Accepted offer did not convert to a joining")
APP_CLOSED = A("Application Closed", "S9", SYS_ATS, P_RECRUITER, True, False, False,
               "Unsuccessful or cancelled application closed with a reason code")
REQ_FILLED = A("Requisition Filled", "S9", SYS_ATS, P_RECRUITER, False, False, False,
               "All positions filled; requisition closed as filled")
REQ_CANCELLED = A("Requisition Cancelled", "S9", SYS_ATS, P_HM, True, False, True,
                  "Requisition withdrawn by the business")
REQ_CLOSED_NOHIRE = A("Requisition Closed Without Hire", "S9", SYS_ATS, P_RECRUITER, True, False, True,
                      "Requisition closed with no hire made")

# --- cross-stage : integration & governance ---------------------------------
SYNC_FAILED = A("Integration Sync Failed", "S0", SYS_INT, P_SYS, False, False, True,
                "Cross-system status update failed")
SYNC_RETRIED = A("Integration Sync Retried", "S0", SYS_INT, P_SYS, False, True, True,
                 "Automatic retry of a failed interface call")
SYNC_COMPLETED = A("Integration Sync Completed", "S0", SYS_INT, P_SYS, False, False, False,
                   "Interface call completed after retry")
MANUAL_REENTRY = A("Manual Status Re-entry", "S0", SYS_ATS, P_TAOPS, True, True, True,
                   "Status re-entered manually after a failed sync")
DUP_UPDATE = A("Duplicate Status Update Detected", "S0", SYS_INT, P_SYS, False, False, True,
               "Duplicate status update received and reconciled")
POLICY_EXCEPTION = A("Policy Exception Documented", "S0", SYS_ATS, P_APPROVER, True, False, True,
                     "Approved deviation from the standard process path, documented")


# ------------------------------------------------------------------ segments
BUSINESS_UNITS = [
    ("Industrial Automation", 0.20), ("Energy Systems", 0.17),
    ("Rail & Mobility Systems", 0.14), ("Digital Engineering", 0.22),
    ("Manufacturing Operations", 0.17), ("Corporate Functions", 0.10),
]
BU_JOB_FAMILY = {
    "Industrial Automation": [("Engineering", .60), ("Project Delivery", .25), ("Digital Technology", .10), ("Corporate", .05)],
    "Energy Systems": [("Engineering", .55), ("Project Delivery", .30), ("Manufacturing", .10), ("Corporate", .05)],
    "Rail & Mobility Systems": [("Project Delivery", .45), ("Engineering", .35), ("Manufacturing", .15), ("Corporate", .05)],
    "Digital Engineering": [("Digital Technology", .75), ("Engineering", .15), ("Corporate", .10)],
    "Manufacturing Operations": [("Manufacturing", .70), ("Engineering", .20), ("Corporate", .10)],
    "Corporate Functions": [("Corporate", .85), ("Digital Technology", .15)],
}
LOCATIONS = [
    ("Bengaluru, IN", 0.22), ("Pune, IN", 0.15), ("Chennai, IN", 0.12),
    ("Hyderabad, IN", 0.10), ("Mumbai, IN", 0.08), ("Gurugram, IN", 0.07),
    ("Jamshedpur, IN", 0.06), ("Singapore, SG", 0.07), ("Munich, DE", 0.06),
    ("Houston, US", 0.04), ("Tokyo, JP", 0.03),
]
# locations with a slower verification vendor (drives GAP-15 clustering)
SLOW_BGV_LOCATIONS = {"Munich, DE", "Houston, US", "Tokyo, JP", "Jamshedpur, IN"}

JOB_LEVELS = [("L2", .10), ("L3", .22), ("L4", .26), ("L5", .22), ("L6", .14), ("L7", .06)]
LEVEL_COMPLEXITY = {"L2": 1.0, "L3": 1.15, "L4": 1.35, "L5": 1.7, "L6": 2.1, "L7": 2.6}
FAMILY_COMPLEXITY = {"Engineering": 1.15, "Manufacturing": 1.0, "Digital Technology": 1.25,
                     "Project Delivery": 1.1, "Corporate": 0.95}

PRIORITIES = [("Standard", .58), ("Urgent", .30), ("Critical", .12)]
PRIORITY_SLA_FACTOR = {"Standard": 1.0, "Urgent": 0.8, "Critical": 0.6}

SOURCE_CHANNELS = [
    ("Employee Referral", .18), ("Job Board", .26), ("Recruitment Agency", .12),
    ("Careers Site", .22), ("Internal Mobility", .09), ("Campus", .06),
    ("Professional Network", .07),
]
DEMAND_TYPES = [("Replacement", .45), ("New Position", .40), ("Project Ramp-up", .15)]


def w(pairs):
    return wpick([(a, b) for a, b in pairs])


# ------------------------------------------------------------------ actors
N_RECRUITERS, N_HM, N_APPROVERS, N_WFP, N_PANEL, N_COMP, N_COMPAPP, N_COORD = 45, 70, 16, 9, 54, 8, 6, 12

RECRUITERS = []
for i in range(1, N_RECRUITERS + 1):
    rid = f"REC-{i:03d}"
    # structural capacity design: 8 recruiters carry a deliberately heavy portfolio
    # and 10 a moderately heavy one, so capacity pressure is uneven by design
    cap_weight = 2.2 if i <= 8 else (1.4 if i <= 18 else 1.0)
    RECRUITERS.append(dict(id=rid, cap_weight=cap_weight,
                           bu=BUSINESS_UNITS[(i - 1) % len(BUSINESS_UNITS)][0]))
RECRUITER_BY_ID = {r["id"]: r for r in RECRUITERS}

HIRING_MANAGERS = []
for i in range(1, N_HM + 1):
    HIRING_MANAGERS.append(dict(
        id=f"HM-{i:03d}",
        bu=w(BUSINESS_UNITS),
        slow_reviewer=(i % 7 == 0 or i % 11 == 0),      # ~22% chronically slow reviewers
        weak_intake=(i % 5 == 0),                       # ~20% submit incomplete demand
    ))
HM_BY_BU = defaultdict(list)
for h in HIRING_MANAGERS:
    HM_BY_BU[h["bu"]].append(h)

APPROVERS = [dict(id=f"APR-{i:03d}", bottleneck=(i in (1, 2, 3, 7, 11)))
             for i in range(1, N_APPROVERS + 1)]
WFP_ANALYSTS = [dict(id=f"WFP-{i:03d}", slow=(i in (1, 2, 5)))
                for i in range(1, N_WFP + 1)]
PANELISTS = [dict(id=f"PNL-{i:03d}", late_feedback=(i % 9 == 1),
                  low_availability=(i % 7 == 0))
             for i in range(1, N_PANEL + 1)]
COMP_ANALYSTS = [f"CMA-{i:03d}" for i in range(1, N_COMP + 1)]
COMP_APPROVERS = [dict(id=f"CMP-{i:03d}", strict=(i in (1, 4)))
                  for i in range(1, N_COMPAPP + 1)]
COORDINATORS = [f"CRD-{i:03d}" for i in range(1, N_COORD + 1)]

# integration incident windows (drives discoverable GAP-16 clustering)
SYNC_INCIDENT_WINDOWS = [
    (datetime(2025, 11, 3), datetime(2025, 11, 15)),
    (datetime(2026, 3, 9), datetime(2026, 3, 21)),
    (datetime(2026, 7, 13), datetime(2026, 7, 20)),
]


def in_sync_incident(ts):
    return any(a <= ts <= b for a, b in SYNC_INCIDENT_WINDOWS)


# ------------------------------------------------------------------ SLA profile
# name -> (from_activity, to_activity, target business days (Standard), stage, scope)
SLA_DEFS = [
    ("SLA-01", "Budget validation turnaround", BUDGET_REQ, BUDGET_OK, 2.0, "S1", "REQUISITION"),
    ("SLA-02", "Requisition approval turnaround", REQ_SUBMIT, REQ_APPROVED, 3.0, "S2", "REQUISITION"),
    ("SLA-03", "Recruiter assignment turnaround", REQ_APPROVED, RECRUITER_ASSIGNED, 2.0, "S3", "REQUISITION"),
    ("SLA-04", "Job description finalization", RECRUITER_ASSIGNED, JD_FINAL, 2.0, "S3", "REQUISITION"),
    ("SLA-05", "Sourcing launch turnaround", RECRUITER_ASSIGNED, JOB_POSTED, 3.0, "S4", "REQUISITION"),
    ("SLA-06", "Screening turnaround", APP_RECEIVED, SCREEN_DONE, 3.0, "S5", "APPLICATION"),
    ("SLA-07", "Hiring manager shortlist review", SHORTLIST_SUBMIT, SHORTLIST_DECISION, 3.0, "S5", "APPLICATION"),
    ("SLA-08", "Interview scheduling lead time", INT_REQUESTED, INT_CONFIRMED, 2.0, "S6", "APPLICATION"),
    ("SLA-09", "Interview feedback turnaround", INT_CONDUCTED, FB_SUBMITTED, 2.0, "S6", "APPLICATION"),
    ("SLA-10", "Assessment completion window", ASM_ASSIGNED, ASM_DONE, 5.0, "S6", "APPLICATION"),
    ("SLA-11", "Selection decision turnaround", FB_SUBMITTED, SEL_DECISION, 3.0, "S7", "APPLICATION"),
    ("SLA-12", "Offer approval cycle", COMP_PROPOSED, OFFER_APPROVED, 3.0, "S8", "APPLICATION"),
    ("SLA-13", "Offer release after approval", OFFER_APPROVED, OFFER_SENT, 1.0, "S8", "APPLICATION"),
    ("SLA-14", "Offer response window", OFFER_SENT, OFFER_ACCEPTED, 5.0, "S8", "APPLICATION"),
    ("SLA-15", "Background verification clearance", BGV_INIT, BGV_CLEARED, 10.0, "S9", "APPLICATION"),
    ("SLA-16", "Pre-joining document completion", DOC_REQUESTED, DOC_RECEIVED, 7.0, "S9", "APPLICATION"),
    ("SLA-17", "Integration sync lag", SYNC_FAILED, SYNC_COMPLETED, 0.5, "S0", "BOTH"),
]
SLA_BY_CODE = {c: dict(code=c, name=n, frm=f, to=t, days=d, stage=s, scope=sc)
               for c, n, f, t, d, s, sc in SLA_DEFS}
SLA_FOR_CLOSING = defaultdict(list)
for c, n, f, t, d, s, sc in SLA_DEFS:
    SLA_FOR_CLOSING[t].append(c)


def sla_minutes(code, priority):
    return int(round(SLA_BY_CODE[code]["days"] * WORK_MIN * PRIORITY_SLA_FACTOR[priority]))


# ------------------------------------------------------------------ reason codes
R_MISSING_GRADE = "MISSING_JOB_GRADE"
R_MISSING_BUDGET = "MISSING_BUDGET_DETAIL"
R_MISSING_LOC = "MISSING_LOCATION"
R_MISSING_JUST = "MISSING_BUSINESS_JUSTIFICATION"
R_INCOMPLETE_PACKET = "INCOMPLETE_APPROVAL_PACKET"
DEMAND_RETURN_REASONS = [R_MISSING_GRADE, R_MISSING_BUDGET, R_MISSING_LOC, R_MISSING_JUST]

WAIT_PENDING_APPROVAL = "PENDING_APPROVAL"
WAIT_BUDGET = "PENDING_BUDGET_VALIDATION"
WAIT_UNASSIGNED = "RECRUITER_UNASSIGNED"
WAIT_JD = "JD_REVIEW_PENDING"
WAIT_SOURCING = "SOURCING_IN_PROGRESS"
WAIT_SCREENING = "SCREENING_QUEUE"
WAIT_HM_REVIEW = "HM_REVIEW_PENDING"
WAIT_PANEL = "PANEL_UNAVAILABLE"
WAIT_CAND_AVAIL = "CANDIDATE_UNAVAILABLE"
WAIT_FEEDBACK = "FEEDBACK_PENDING"
WAIT_ASSESSMENT = "ASSESSMENT_PENDING"
WAIT_DECISION = "SELECTION_DECISION_PENDING"
WAIT_COMP = "COMP_APPROVAL_PENDING"
WAIT_CAND_RESP = "CANDIDATE_RESPONSE_PENDING"
WAIT_VERIFY = "VERIFICATION_PENDING"
WAIT_DOCS = "DOCUMENTS_PENDING"
WAIT_NOTICE = "NOTICE_PERIOD"
WAIT_SYNC = "SYSTEM_SYNC_FAILURE"
WAIT_UNKNOWN = ""          # BRD: keep unknown rather than inventing a reason

REJECT_REASONS = ["SKILL_MISMATCH", "EXPERIENCE_BELOW_REQUIREMENT", "DOMAIN_MISMATCH",
                  "ASSESSMENT_BELOW_CUTOFF", "INTERVIEW_PERFORMANCE", "BETTER_PROFILE_SELECTED",
                  "COMPENSATION_EXPECTATION_OUT_OF_BAND", "LOCATION_CONSTRAINT"]
WITHDRAW_REASONS = ["ACCEPTED_OTHER_OFFER", "PROCESS_TOO_SLOW", "COMPENSATION_EXPECTATION",
                    "PERSONAL_REASONS", "ROLE_EXPECTATION_MISMATCH", "COUNTER_OFFER_FROM_EMPLOYER",
                    "RELOCATION_DECLINED", "NO_RESPONSE"]
DECLINE_REASONS = ["ACCEPTED_OTHER_OFFER", "COMPENSATION_BELOW_EXPECTATION",
                   "COUNTER_OFFER_FROM_EMPLOYER", "ROLE_EXPECTATION_MISMATCH", "LOCATION_CONSTRAINT"]
CANCEL_REASONS = ["BUDGET_WITHDRAWN", "BUSINESS_PRIORITY_CHANGE", "ROLE_RESCOPED",
                  "HEADCOUNT_FREEZE", "FILLED_BY_INTERNAL_MOBILITY", "PROJECT_DEFERRED"]
NOJOIN_REASONS = ["ACCEPTED_OTHER_OFFER", "COUNTER_OFFER_FROM_EMPLOYER", "VERIFICATION_EXCEPTION",
                  "PERSONAL_REASONS", "RELOCATION_DECLINED"]


SLA_ANCHOR = {
    "SLA-01": "first", "SLA-02": "first", "SLA-03": "last", "SLA-04": "last",
    "SLA-05": "last", "SLA-06": "first", "SLA-07": "last", "SLA-08": "last",
    "SLA-09": "last", "SLA-10": "last", "SLA-11": "last", "SLA-12": "first",
    "SLA-13": "last", "SLA-14": "last", "SLA-15": "last", "SLA-16": "last",
    "SLA-17": "last",
}

# ------------------------------------------------------------------ event emitter
EVENTS = []
_src_counter = Counter()
_event_seq = [0]


def next_source_event_id(system):
    pfx = SRC_PREFIX[system]
    _src_counter[pfx] += 1
    return f"{pfx}-E-{_src_counter[pfx]:07d}"


class Case:
    """Event-emitting container for one process instance."""

    def __init__(self, pid, case_type, req, application=None):
        self.pid = pid
        self.case_type = case_type
        self.req = req
        self.app = application
        self.status = "Draft"
        self.last_ts = None
        self.last_persona = None
        self.first_ts = {}
        self.last_occurrence = {}
        self.counts = Counter()
        self.events = []

    # ---- helpers -----------------------------------------------------------
    def seen(self, activity):
        return self.counts[activity] > 0

    def ts_of(self, activity, which="last"):
        if which == "first":
            return self.first_ts.get(activity)
        return self.last_occurrence.get(activity)

    def _auto_sla(self, activity):
        for code in SLA_FOR_CLOSING.get(activity, []):
            anchor = SLA_ANCHOR[code]
            frm = SLA_BY_CODE[code]["frm"]
            t0 = self.ts_of(frm, anchor)
            if t0 is not None:
                return code, t0
        return None, None

    # ---- emit --------------------------------------------------------------
    def emit(self, ts, activity, status_after=None, actor=None, persona=None,
             system=None, outcome="", reason="", wait_reason=None, duration=None,
             manual=None, rework=None, exception=None, dq=False,
             sla_code=None, sla_from_ts=None, stage=None):
        meta = ACT[activity]
        persona = persona or meta["persona"]
        system = system or meta["system"]
        stage = stage or meta["stage"]
        ts = normalize(ts)
        if self.last_ts is not None and ts < self.last_ts:
            ts = self.last_ts + timedelta(minutes=1)
            ts = normalize(ts)

        self.counts[activity] += 1
        rep = self.counts[activity]
        if activity not in self.first_ts:
            self.first_ts[activity] = ts
        self.last_occurrence[activity] = ts

        wait = bmin_between(self.last_ts, ts) if self.last_ts is not None else ""
        handoff = (self.last_persona is not None and persona != self.last_persona)

        if sla_code is None:
            sla_code, sla_from_ts = self._auto_sla(activity)
        sla_target, breach, overrun, measured = "", "N", "", ""
        if sla_code and sla_from_ts is not None:
            sla_target = sla_minutes(sla_code, self.req["priority"])
            measured = bmin_between(sla_from_ts, ts)
            breach = "Y" if measured > sla_target else "N"
            overrun = max(0, measured - sla_target)

        rework_flag = meta["rework"] if rework is None else rework
        exc_flag = meta["exception"] if exception is None else exception
        manual_flag = meta["manual"] if manual is None else manual

        _event_seq[0] += 1
        req = self.req
        row = {
            "event_id": f"EVT-{_event_seq[0]:08d}",
            "process_instance_id": self.pid,
            "case_type": self.case_type,
            "requisition_id": req["requisition_id"],
            "application_id": (self.app or {}).get("application_id", ""),
            "candidate_surrogate_id": (self.app or {}).get("candidate_surrogate_id", ""),
            "activity_name": activity,
            "activity_stage": STAGE_NAME.get(stage, "Cross-stage (System / Governance)"),
            "stage_code": stage,
            "stage_sequence": STAGE_SEQ.get(stage, 0),
            "event_timestamp": ts.strftime("%Y-%m-%d %H:%M:%S"),
            "event_end_timestamp": (add_bmin(ts, duration).strftime("%Y-%m-%d %H:%M:%S")
                                    if duration else ""),
            "actor_id_pseudonymous": actor or "",
            "actor_persona": persona,
            "source_system": system,
            "source_event_id": next_source_event_id(system),
            "status_before": self.status,
            "status_after": status_after or self.status,
            "outcome_code": outcome,
            "reason_code": reason,
            "duration_minutes": int(round(duration)) if duration else "",
            "waiting_time_minutes": wait,
            "waiting_reason_code": (wait_reason if wait_reason is not None else WAIT_UNKNOWN),
            "sla_code": sla_code or "",
            "sla_target_minutes": sla_target,
            "sla_measured_minutes": measured,
            "sla_breach_flag": breach,
            "sla_overrun_minutes": overrun,
            "rework_flag": "Y" if rework_flag else "N",
            "repeat_sequence_number": rep,
            "manual_touch_flag": "Y" if manual_flag else "N",
            "handoff_flag": "Y" if handoff else "N",
            "exception_flag": "Y" if exc_flag else "N",
            "gap_reference": "",            # derived downstream, never generated here
            "business_unit": req["business_unit"],
            "job_family": req["job_family"],
            "location": req["location"],
            "job_level": req["job_level"],
            "priority": req["priority"],
            "recruiter_id_pseudonymous": req.get("recruiter_id") or "",
            "hiring_manager_id_pseudonymous": req["hiring_manager_id"],
            "source_channel": (self.app or {}).get("source_channel", ""),
            "data_quality_flag": "Y" if dq else "N",
            "extraction_batch_id": EXTRACTION_BATCH,
        }
        if status_after:
            self.status = status_after
        self.last_ts = ts
        self.last_persona = persona
        self.events.append(row)
        EVENTS.append(row)
        return ts

    # ---- integration failure injection ------------------------------------
    def sync_hiccup(self, ts, target_system, p_base=0.040):
        """Cross-system handoff failure chain (GAP-16 evidence). Returns new ts."""
        p = p_base * (4.0 if in_sync_incident(ts) else 1.0)
        if target_system == SYS_ASM:
            p *= 2.2
        if not chance(p):
            return ts
        t = add_bmin(ts, U(2, 25))
        self.emit(t, SYNC_FAILED, outcome="Failed", reason="INTERFACE_TIMEOUT",
                  wait_reason=WAIT_SYNC, system=SYS_INT)
        retries = U(1, 3)
        for _ in range(retries):
            t = add_bmin(t, U(10, 240))
            self.emit(t, SYNC_RETRIED, outcome="Retried", reason="INTERFACE_TIMEOUT",
                      wait_reason=WAIT_SYNC, system=SYS_INT)
        if chance(0.22):
            t = add_bmin(t, U(30, 120))
            self.emit(t, DUP_UPDATE, outcome="Reconciled", reason="DUPLICATE_PAYLOAD",
                      dq=True, system=SYS_INT)
        if chance(0.45):
            t = add_bmin(t, LN(0.9, 0.7))
            self.emit(t, MANUAL_REENTRY, outcome="Corrected", reason="MANUAL_WORKAROUND",
                      wait_reason=WAIT_SYNC, actor=pick(COORDINATORS))
            t = add_bmin(t, U(5, 40))
            self.emit(t, SYNC_COMPLETED, outcome="Completed", system=SYS_INT)
        else:
            t = add_bmin(t, LN(0.4, 0.8))
            self.emit(t, SYNC_COMPLETED, outcome="Completed", system=SYS_INT)
        return t


# ------------------------------------------------------------------ recruiter load model
RECRUITER_WINDOWS = defaultdict(list)       # rid -> [(start, end, weight)]
PRIORITY_LOAD_WEIGHT = {"Standard": 1.0, "Urgent": 1.3, "Critical": 1.7}
# reference weighted load that a recruiter is designed to absorb comfortably;
# load_factor = weighted_load / LOAD_REF drives queue ageing (GAP-04, GAP-07, GAP-17)
LOAD_REF = 11.0


def recruiter_load(rid, ts):
    """(weighted load, open requisition count) carried by rid at ts."""
    wsum, n = 0.0, 0
    for s, e, wt in RECRUITER_WINDOWS[rid]:
        if s <= ts and (e is None or e >= ts):
            wsum += wt
            n += 1
    return round(wsum, 2), n


def register_requisition_window(rid, start, end, weight):
    RECRUITER_WINDOWS[rid].append((start, end, weight))


# ------------------------------------------------------------------ requisition attributes
GROUND_TRUTH = []       # generator intent, for detector validation only


def make_requisition(idx, demand_ts):
    bu = w(BUSINESS_UNITS)
    jf = w(BU_JOB_FAMILY[bu])
    loc = w(LOCATIONS)
    lvl = w(JOB_LEVELS)
    prio = w(PRIORITIES)
    # ~18% of requisitions are deliberately executed as a healthy benchmark cohort:
    # responsive stakeholders, complete intake, no optional pain-point draws. Kinetic
    # needs these contrast cases to explain differences, not just rank slow cases.
    clean_run = chance(0.18)
    hm_pool = HM_BY_BU[bu] or HIRING_MANAGERS
    if clean_run:
        hm = pick([h for h in hm_pool if not h["slow_reviewer"] and not h["weak_intake"]] or hm_pool)
        approver = pick([a for a in APPROVERS if not a["bottleneck"]])
        wfp = pick([x for x in WFP_ANALYSTS if not x["slow"]])
    else:
        hm = pick(hm_pool)
        approver = pick(APPROVERS)
        wfp = pick(WFP_ANALYSTS)

    complexity = LEVEL_COMPLEXITY[lvl] * FAMILY_COMPLEXITY[jf]
    if loc.split(", ")[-1] not in ("IN",):
        complexity *= 1.15
    positions = 1 if not chance(0.08) else U(2, 4)

    intent = wpick([("FILL", 0.74), ("NOHIRE", 0.11), ("CANCEL", 0.15)])

    req = dict(
        requisition_id=f"REQ-{10000 + idx}",
        business_unit=bu, job_family=jf, location=loc, job_level=lvl,
        priority=prio, positions_count=positions, demand_type=w(DEMAND_TYPES),
        hiring_manager_id=hm["id"], approver_id=approver["id"],
        workforce_analyst_id=wfp["id"], recruiter_id=None,
        role_complexity_score=round(complexity, 2),
        demand_ts=demand_ts, intent=intent,
        sla_profile=f"TA-{prio.upper()}-ILLUSTRATIVE",
        process_version="TA-ASIS-1.0", policy_version="TA-POLICY-2026.1",
        business_calendar="IN-STD-09:00-17:30-MONFRI",
    )

    # ---------------- scenario design (ground truth, not written to the log)
    g = {}
    # GAP-01 incomplete hiring demand
    p01 = 0.30 if hm["weak_intake"] else 0.11
    if jf == "Project Delivery":
        p01 += 0.05
    g["demand_returns"] = 0
    if chance(p01):
        g["demand_returns"] = wpick([(1, .45), (2, .35), (3, .20)])

    # GAP-02 budget / headcount validation delay
    budget_cycle = demand_ts.month in (3, 4, 9, 10)
    p02 = 0.14 + (0.26 if wfp["slow"] else 0.0) + (0.14 if budget_cycle else 0.0)
    g["budget_delay"] = chance(min(0.62, p02))

    # GAP-03 approval bottleneck / repeated approval returns
    p03_wait = 0.55 if approver["bottleneck"] else 0.16
    g["approval_slow"] = chance(p03_wait)
    g["approval_returns"] = 0
    p03_ret = 0.26 if g["demand_returns"] else 0.11
    if chance(p03_ret):
        g["approval_returns"] = wpick([(1, .6), (2, .3), (3, .1)])

    # GAP-05 JD revision loop / sourcing readiness
    p05 = 0.13 + (0.16 if jf == "Digital Technology" else 0.0) + (0.12 if lvl in ("L5", "L6", "L7") else 0.0)
    g["jd_revisions"] = wpick([(2, .4), (3, .25), (1, .35)]) if chance(p05) else 0
    g["posting_delay"] = chance(0.16 + (0.14 if lvl in ("L6", "L7") else 0.0))

    # GAP-18 governance deviations (with a documented-exception counterexample set)
    g["skip_budget_validation"] = chance(0.035)
    g["skip_requisition_approval"] = chance(0.032)
    g["documented_exception"] = chance(0.030)

    g["hold_reopen"] = chance(0.05)
    g["agency"] = chance(0.10 + (0.22 if lvl in ("L6", "L7") else 0.0))
    g["high_volume_pipeline"] = chance(0.12)
    if clean_run:
        g.update(demand_returns=0, budget_delay=False, approval_slow=False,
                 approval_returns=0, jd_revisions=0, posting_delay=False,
                 skip_budget_validation=False, skip_requisition_approval=False,
                 hold_reopen=False)
    g["clean_run"] = clean_run
    req["gt"] = g
    return req


def app_volume(req):
    g = req["gt"]
    if g["high_volume_pipeline"]:
        n = int(round(T(18, 26, 42)))
    else:
        base = T(2, 7, 18)
        if req["job_level"] in ("L6", "L7"):
            base *= 0.55
        if req["priority"] == "Critical":
            base *= 1.15
        n = max(1, int(round(base)))
    return n * req["positions_count"] if req["positions_count"] > 1 else n


# ------------------------------------------------------------------ candidate profile
_cand_seq = [0]
_app_seq = [0]


def make_application(req, arrival_ts):
    _app_seq[0] += 1
    _cand_seq[0] += 1
    temperament = wpick([("resilient", .22), ("normal", .66), ("impatient", .12)])
    channel = w(SOURCE_CHANNELS)
    if req["gt"]["agency"] and chance(0.45):
        channel = "Recruitment Agency"
    return dict(
        application_id=f"APP-{100000 + _app_seq[0]}",
        candidate_surrogate_id=f"CAND-{500000 + _cand_seq[0]}",
        requisition_id=req["requisition_id"],
        source_channel=channel,
        arrival_ts=arrival_ts,
        temperament=temperament,
        comp_expectation_out_of_band=chance(0.20 if req["job_level"] in ("L5", "L6", "L7") else 0.12),
        notice_period_days=wpick([(30, .35), (60, .34), (90, .26), (15, .05)]),
        gt={},
    )


TEMPERAMENT_FACTOR = {"resilient": 0.30, "normal": 1.0, "impatient": 2.2}


def wrn(code, wait_minutes, threshold_days=1.0):
    """Record a waiting reason only when a material wait actually occurred."""
    return code if wait_minutes > bdays(threshold_days) else None


# ------------------------------------------------------------------ withdrawal hazard
def try_withdraw(case, app, t, stage, inactivity_bmin, base_p):
    """Candidate disengagement hazard. Delay raises it, temperament modulates it."""
    idays = inactivity_bmin / WORK_MIN
    f = TEMPERAMENT_FACTOR[app["temperament"]]
    p = (base_p + 0.028 * max(0.0, idays - 4.0)) * f
    if app["temperament"] == "impatient":
        p += 0.022                                  # withdraws even without delay
    if chance(min(0.55, p)):
        if idays >= 7:
            reason = wpick([("ACCEPTED_OTHER_OFFER", .40), ("PROCESS_TOO_SLOW", .34),
                            ("COMPENSATION_EXPECTATION", .10), ("PERSONAL_REASONS", .08),
                            ("NO_RESPONSE", .08)])
        else:
            reason = wpick([("ACCEPTED_OTHER_OFFER", .34), ("PERSONAL_REASONS", .20),
                            ("COMPENSATION_EXPECTATION", .16), ("ROLE_EXPECTATION_MISMATCH", .14),
                            ("COUNTER_OFFER_FROM_EMPLOYER", .10), ("RELOCATION_DECLINED", .06)])
        tw = add_bmin(t, U(10, 300))
        case.emit(tw, CAND_WITHDREW, status_after="Withdrawn", outcome="Withdrawn",
                  reason=reason, stage=stage)
        case.emit(add_bmin(tw, U(20, 600)), APP_CLOSED, status_after="Closed",
                  outcome="Withdrawn", reason=reason, stage="S9")
        return dict(outcome="Withdrawn", stage=stage, reason=reason,
                    inactivity_bdays=round(idays, 2))
    return None


def reject_candidate(case, app, t, stage, reason, label):
    tr = add_bmin(t, U(20, 420))
    case.emit(tr, CAND_REJECTED, status_after="Rejected", outcome="Rejected",
              reason=reason, stage=stage)
    case.emit(add_bmin(tr, U(10, 400)), APP_CLOSED, status_after="Closed",
              outcome="Rejected", reason=reason, stage="S9")
    return dict(outcome=label, stage=stage, reason=reason)


# ------------------------------------------------------------------ application lifecycle
def run_application(req, app, reqcase, designated, cancel_ts=None):
    """Generate one candidate application journey. Returns an outcome dict."""
    g = app["gt"]
    case = Case(app["application_id"], "APPLICATION", req, app)
    app["case"] = case
    rid = req["recruiter_id"]
    hm = next(h for h in HIRING_MANAGERS if h["id"] == req["hiring_manager_id"])
    cplx = req["role_complexity_score"]

    t = case.emit(app["arrival_ts"], APP_RECEIVED, status_after="Applied",
                  outcome="Received", wait_reason=None)
    t = case.sync_hiccup(t, SYS_ATS, p_base=0.012)

    def cancelled_here(ts):
        return cancel_ts is not None and ts >= cancel_ts

    # ---------------- Stage 5 : screening -----------------------------------
    load, nopen = recruiter_load(rid, t)
    g["recruiter_load_at_screening"] = load
    lf = load / LOAD_REF
    screen_wait = LN(1.0, 0.55) * (1 + 0.40 * max(0.0, lf - 1.0))
    if req["gt"]["high_volume_pipeline"]:
        screen_wait *= 1.4
    g["screening_backlog"] = screen_wait > bdays(4)

    tt = t
    n_follow = int(screen_wait / bdays(3.5))
    for k in range(min(n_follow, 3)):
        tt = add_bmin(tt, screen_wait / (n_follow + 1))
        case.emit(tt, SCREEN_REMIND, outcome="Follow-up", wait_reason=WAIT_SCREENING,
                  actor=pick(COORDINATORS))
    t = add_bmin(t, screen_wait)

    if cancelled_here(t):
        return close_cancelled(case, app, cancel_ts, "S5")

    rescreen = chance(0.05 + (0.09 if lf > 1.2 else 0.0))
    p_short = 0.42 + {"Employee Referral": .17, "Internal Mobility": .14, "Professional Network": .05,
                      "Careers Site": 0.0, "Recruitment Agency": .06, "Job Board": -.10,
                      "Campus": -.07}.get(app["source_channel"], 0.0)
    shortlisted = designated or chance(max(0.12, min(0.80, p_short)))

    case.emit(t, SCREEN_DONE, status_after="Screened",
              outcome="Shortlisted" if shortlisted else "Rejected",
              reason="" if shortlisted else pick(REJECT_REASONS[:4]),
              wait_reason=wrn(WAIT_SCREENING, screen_wait),
              duration=U(8, 25))
    if rescreen:
        t = add_bmin(t, LN(0.8, 0.6))
        case.emit(t, RESCREEN, outcome="Re-reviewed", reason="CRITERIA_CLARIFIED",
                  duration=U(8, 25))
        g["rescreened"] = True

    if not shortlisted:
        return reject_candidate(case, app, t, "S5", pick(REJECT_REASONS[:4]), "Rejected - Screening")

    t = add_bmin(t, U(15, 240))
    case.emit(t, SHORTLISTED, status_after="Shortlisted", outcome="Shortlisted")
    t = add_bmin(t, U(20, 480))
    case.emit(t, SHORTLIST_SUBMIT, status_after="In Hiring Manager Review",
              outcome="Submitted")

    # GAP-06 : hiring manager review queue
    hm_wait = LN(4.6, 0.55) if hm["slow_reviewer"] else LN(1.2, 0.6)
    if req["priority"] == "Critical":
        hm_wait *= 0.7
    g["hm_review_wait_bdays"] = round(hm_wait / WORK_MIN, 2)
    tt = t
    n_rem = int(hm_wait / bdays(3))
    for k in range(min(n_rem, 4)):
        tt = add_bmin(tt, hm_wait / (n_rem + 1))
        case.emit(tt, SHORTLIST_REMIND, outcome="Reminder", wait_reason=WAIT_HM_REVIEW)
    wd = try_withdraw(case, app, tt, "S5", hm_wait, 0.010)
    if wd:
        return wd
    t = add_bmin(t, hm_wait)
    if cancelled_here(t):
        return close_cancelled(case, app, cancel_ts, "S5")

    hm_decision = "Accepted" if designated else wpick([("Accepted", .62), ("Rejected", .30),
                                                       ("More Profiles Requested", .08)])
    case.emit(t, SHORTLIST_DECISION, status_after="Shortlist Reviewed", outcome=hm_decision,
              reason="" if hm_decision == "Accepted" else pick(REJECT_REASONS),
              wait_reason=wrn(WAIT_HM_REVIEW, hm_wait),
              actor=hm["id"])
    if hm_decision == "More Profiles Requested":
        reqcase.emit(add_bmin(t, U(20, 300)), MORE_PROFILES, outcome="Rework requested",
                     reason="SHORTLIST_NOT_SUITABLE", actor=hm["id"], stage="S4")
        return reject_candidate(case, app, t, "S5", "BETTER_PROFILE_SELECTED",
                                "Rejected - Shortlist Review")
    if hm_decision == "Rejected":
        return reject_candidate(case, app, t, "S5", pick(REJECT_REASONS), "Rejected - Shortlist Review")

    # ---------------- Stage 6 : assessment ----------------------------------
    needs_assessment = (req["job_family"] in ("Engineering", "Digital Technology", "Manufacturing")
                        and req["job_level"] in ("L2", "L3", "L4", "L5") and chance(0.62))
    if needs_assessment:
        t = add_bmin(t, U(30, 600))
        case.emit(t, ASM_ASSIGNED, status_after="Assessment Assigned", outcome="Assigned",
                  actor=pick([f"ASM-{i:03d}" for i in range(1, 5)]))
        slow_asm = chance(0.30)
        asm_wait = LN(6.5, 0.55) if slow_asm else LN(2.0, 0.6)
        g["assessment_wait_bdays"] = round(asm_wait / WORK_MIN, 2)
        tt = t
        for k in range(min(int(asm_wait / bdays(3)), 3)):
            tt = add_bmin(tt, asm_wait / (int(asm_wait / bdays(3)) + 1))
            case.emit(tt, ASM_REMIND, outcome="Reminder", wait_reason=WAIT_ASSESSMENT)
        expired = chance(0.09 if slow_asm else 0.02) and not designated
        if expired:
            t = add_bmin(t, bdays(SLA_BY_CODE["SLA-10"]["days"]) + LN(1.0, 0.4))
            case.emit(t, ASM_EXPIRED, status_after="Assessment Expired", outcome="Expired",
                      reason="WINDOW_LAPSED", wait_reason=WAIT_ASSESSMENT)
            g["assessment_expired"] = True
            if chance(0.55):
                t = add_bmin(t, LN(1.2, 0.6))
                case.emit(t, ASM_REISSUED, outcome="Reissued", reason="EXPIRY_REWORK")
                t = add_bmin(t, LN(2.2, 0.5))
            else:
                return reject_candidate(case, app, t, "S6", "ASSESSMENT_NOT_COMPLETED",
                                        "Rejected - Assessment")
        else:
            wd = try_withdraw(case, app, tt, "S6", asm_wait, 0.008)
            if wd:
                return wd
            t = add_bmin(t, asm_wait)
        case.emit(t, ASM_DONE, status_after="Assessment Completed", outcome="Completed",
                  duration=U(45, 150), wait_reason=wrn(WAIT_ASSESSMENT, asm_wait))
        tsync = add_bmin(t, U(5, 90))
        case.emit(tsync, ASM_SYNCED, outcome="Synchronized", system=SYS_ASM)
        t = case.sync_hiccup(tsync, SYS_ASM, p_base=0.040)
        if not designated and chance(0.22):
            return reject_candidate(case, app, t, "S6", "ASSESSMENT_BELOW_CUTOFF",
                                    "Rejected - Assessment")

    if cancelled_here(t):
        return close_cancelled(case, app, cancel_ts, "S6")

    # ---------------- Stage 6 : interview rounds ----------------------------
    n_rounds = 2 if req["job_level"] in ("L2", "L3", "L4") else 3
    if chance(0.12):
        n_rounds += 1
    rounds_done, reschedules, late_fb, feedback_returns, noshows = 0, 0, 0, 0, 0
    round_idx = 0
    extra_round_requested = False
    while round_idx < n_rounds:
        round_idx += 1
        panel_pool = ([q for q in PANELISTS
                       if not q["low_availability"] and not q["late_feedback"]]
                      if req["gt"]["clean_run"] else PANELISTS)
        panel = [pick(panel_pool) for _ in range(U(1, 3))]
        low_avail = any(p["low_availability"] for p in panel)
        t = add_bmin(t, U(20, 420))
        case.emit(t, INT_REQUESTED, status_after=f"Interview {round_idx} Requested",
                  outcome="Requested")
        # GAP-08 scheduling lead time
        sched = LN(2.6, 0.55) if low_avail else LN(0.7, 0.5)
        if req["job_level"] in ("L6", "L7"):
            sched *= 1.35
        if req["priority"] == "Critical":
            sched *= 0.8
        tt = t
        for k in range(min(int(sched / bdays(2)), 3)):
            tt = add_bmin(tt, sched / (int(sched / bdays(2)) + 1))
            case.emit(tt, INT_SCHED_FOLLOWUP, outcome="Follow-up",
                      wait_reason=WAIT_PANEL if low_avail else WAIT_CAND_AVAIL,
                      actor=pick(COORDINATORS))
        wd = try_withdraw(case, app, tt, "S6", sched, 0.009)
        if wd:
            wd["rounds"] = rounds_done
            return wd
        t = add_bmin(t, sched)
        case.emit(t, INT_CONFIRMED, status_after=f"Interview {round_idx} Scheduled",
                  outcome="Confirmed", actor=pick(COORDINATORS),
                  wait_reason=(wrn(WAIT_PANEL, sched) if low_avail else wrn(WAIT_CAND_AVAIL, sched)))

        n_resched = 0
        while chance(0.34 if low_avail else 0.12) and n_resched < 3:
            n_resched += 1
            reschedules += 1
            t = add_bmin(t, LN(1.4, 0.6))
            case.emit(t, INT_RESCHEDULED, outcome="Rescheduled",
                      reason="PANEL_UNAVAILABLE" if low_avail else
                             pick(["CANDIDATE_REQUEST", "PANEL_CONFLICT", "PRIORITY_CONFLICT"]),
                      wait_reason=WAIT_PANEL if low_avail else WAIT_CAND_AVAIL,
                      actor=panel[0]["id"])
            t = add_bmin(t, LN(1.6, 0.5))
            case.emit(t, INT_CONFIRMED, status_after=f"Interview {round_idx} Rescheduled",
                      outcome="Confirmed", actor=pick(COORDINATORS))
        if n_resched >= 2:
            wd = try_withdraw(case, app, t, "S6", bdays(n_resched * 2.5), 0.030)
            if wd:
                wd["rounds"] = rounds_done
                return wd

        t = add_bmin(t, LN(1.5, 0.5))
        if chance(0.05):
            noshows += 1
            case.emit(t, INT_NOSHOW, outcome="No Show",
                      reason=pick(["CANDIDATE_NO_SHOW", "PANEL_NO_SHOW"]))
            if chance(0.35):
                return dict(outcome="No Show", stage="S6", reason="CANDIDATE_NO_SHOW",
                            rounds=rounds_done, closed=close_simple(case, t, "No Show",
                                                                    "CANDIDATE_NO_SHOW"))
            t = add_bmin(t, LN(2.0, 0.5))
            case.emit(t, INT_CONFIRMED, status_after=f"Interview {round_idx} Rescheduled",
                      outcome="Confirmed", actor=pick(COORDINATORS))
            t = add_bmin(t, LN(1.4, 0.5))
        case.emit(t, INT_CONDUCTED, status_after=f"Interview {round_idx} Completed",
                  outcome="Completed", duration=U(45, 120), actor=panel[0]["id"])
        rounds_done += 1

        # GAP-09 feedback turnaround
        late_panel = any(p["late_feedback"] for p in panel)
        fb_wait = LN(2.5, 0.5) if late_panel else LN(0.6, 0.5)
        tt = t
        for k in range(min(int(fb_wait / bdays(2)), 3)):
            tt = add_bmin(tt, fb_wait / (int(fb_wait / bdays(2)) + 1))
            case.emit(tt, FB_REMIND, outcome="Reminder", wait_reason=WAIT_FEEDBACK,
                      actor=pick(COORDINATORS))
        t = add_bmin(t, fb_wait)
        case.emit(t, FB_SUBMITTED, status_after=f"Interview {round_idx} Feedback Received",
                  outcome="Submitted", actor=panel[0]["id"],
                  wait_reason=wrn(WAIT_FEEDBACK, fb_wait))
        if fb_wait > bdays(2):
            late_fb += 1
        if chance(0.07):
            feedback_returns += 1
            t = add_bmin(t, LN(0.7, 0.6))
            case.emit(t, FB_RETURNED, outcome="Returned", reason="FEEDBACK_FIELDS_INCOMPLETE")
            t = add_bmin(t, LN(1.3, 0.6))
            case.emit(t, FB_SUBMITTED, status_after=f"Interview {round_idx} Feedback Received",
                      outcome="Submitted", actor=panel[0]["id"], wait_reason=WAIT_FEEDBACK)

        if not designated and chance(0.30):
            return reject_candidate(case, app, t, "S6", pick(["INTERVIEW_PERFORMANCE",
                                                              "SKILL_MISMATCH", "DOMAIN_MISMATCH"]),
                                    "Rejected - Interview")
        if cancelled_here(t):
            return close_cancelled(case, app, cancel_ts, "S6")
        # GAP-11 additional round requested after feedback
        if round_idx == n_rounds and not extra_round_requested and chance(0.10):
            extra_round_requested = True
            t = add_bmin(t, LN(1.5, 0.6))
            case.emit(t, EXTRA_ROUND, outcome="Additional Round Requested",
                      reason="PANEL_DISAGREEMENT", actor=hm["id"], stage="S7")
            n_rounds += 1

    res = dict(rounds=rounds_done, reschedules=reschedules, late_feedback=late_fb,
               feedback_returns=feedback_returns, noshows=noshows)
    g.update(res)
    return run_selection_and_offer(req, app, reqcase, case, t, designated, hm, cancel_ts, res)


def close_simple(case, t, outcome, reason):
    case.emit(add_bmin(t, U(30, 600)), APP_CLOSED, status_after="Closed",
              outcome=outcome, reason=reason, stage="S9")
    return True


def close_cancelled(case, app, cancel_ts, stage):
    t = add_bmin(cancel_ts, U(10, 420))
    case.emit(t, APP_CLOSED, status_after="Closed", outcome="Requisition Cancelled",
              reason="REQUISITION_CANCELLED", stage="S9")
    return dict(outcome="Closed - Requisition Cancelled", stage=stage,
                reason="REQUISITION_CANCELLED")


# ------------------------------------------------------------------ stages 7-9
def run_selection_and_offer(req, app, reqcase, case, t, designated, hm, cancel_ts, res):
    g = app["gt"]
    prio = req["priority"]

    def cancelled_here(ts):
        return cancel_ts is not None and ts >= cancel_ts

    # ---------------- Stage 7 : selection decision --------------------------
    dec_wait = LN(4.2, 0.55) if hm["slow_reviewer"] else LN(1.3, 0.65)
    if prio == "Critical":
        dec_wait *= 0.75
    if res["reschedules"] >= 2 or res["late_feedback"] >= 2:
        dec_wait *= 1.25
    g["selection_wait_bdays"] = round(dec_wait / WORK_MIN, 2)
    tt = t
    for k in range(min(int(dec_wait / bdays(3)), 3)):
        tt = add_bmin(tt, dec_wait / (int(dec_wait / bdays(3)) + 1))
        case.emit(tt, SEL_REMIND, outcome="Reminder", wait_reason=WAIT_DECISION)
    wd = try_withdraw(case, app, tt, "S7", dec_wait, 0.012)
    if wd:
        wd.update(res)
        return wd
    t = add_bmin(t, dec_wait)
    if cancelled_here(t):
        return close_cancelled(case, app, cancel_ts, "S7")

    selected = designated or chance(0.30)
    case.emit(t, SEL_DECISION, status_after="Selected" if selected else "Not Selected",
              outcome="Selected" if selected else "Rejected",
              reason="" if selected else pick(["BETTER_PROFILE_SELECTED", "INTERVIEW_PERFORMANCE"]),
              wait_reason=wrn(WAIT_DECISION, dec_wait), actor=hm["id"])
    reopened = False
    if chance(0.05):
        reopened = True
        t = add_bmin(t, LN(2.0, 0.6))
        case.emit(t, SEL_REOPENED, outcome="Reopened", reason="DECISION_REVISITED", actor=hm["id"])
        t = add_bmin(t, LN(1.6, 0.6))
        case.emit(t, SEL_DECISION, status_after="Selected" if selected else "Not Selected",
                  outcome="Selected" if selected else "Rejected", actor=hm["id"])
    g["selection_reopened"] = reopened
    if not selected:
        out = reject_candidate(case, app, t, "S7", pick(["BETTER_PROFILE_SELECTED",
                                                         "INTERVIEW_PERFORMANCE"]),
                               "Rejected - Final Selection")
        out.update(res)
        return out

    # ---------------- Stage 8 : offer management ----------------------------
    gov_skip_offer_approval = chance(0.032)
    out_of_band = app["comp_expectation_out_of_band"]
    comp_analyst = pick(COMP_ANALYSTS)
    comp_approver = pick(COMP_APPROVERS)

    t = add_bmin(t, LN(0.9, 0.6))
    case.emit(t, COMP_PROPOSED, status_after="Compensation Proposed", outcome="Created",
              actor=comp_analyst, reason="OUT_OF_BAND_REQUEST" if out_of_band else "")
    revisions, returns = 0, 0
    if not gov_skip_offer_approval:
        t = add_bmin(t, U(20, 420))
        case.emit(t, OFFER_APPR_REQ, status_after="Offer Approval Pending",
                  outcome="Requested", actor=comp_analyst)
        # GAP-12 approval cycle + rework loops
        p_rework = 0.40 if out_of_band else 0.08
        if comp_approver["strict"]:
            p_rework += 0.14
        while chance(p_rework) and returns < 3:
            appr_wait = LN(2.0, 0.55) if comp_approver["strict"] else LN(1.0, 0.55)
            tt = t
            for k in range(min(int(appr_wait / bdays(3)), 2)):
                tt = add_bmin(tt, appr_wait / (int(appr_wait / bdays(3)) + 1))
                case.emit(tt, OFFER_APPR_REMIND, outcome="Reminder", wait_reason=WAIT_COMP)
            t = add_bmin(t, appr_wait)
            returns += 1
            case.emit(t, OFFER_RETURNED, outcome="Returned for Revision",
                      reason="OUTSIDE_APPROVED_BAND" if out_of_band else
                             pick(["MISSING_BENCHMARK_DATA", "INCOMPLETE_PROPOSAL"]),
                      actor=comp_approver["id"], wait_reason=WAIT_COMP)
            t = add_bmin(t, LN(1.2, 0.6))
            revisions += 1
            case.emit(t, COMP_REVISED, outcome="Revised", actor=comp_analyst)
            t = add_bmin(t, U(20, 300))
            case.emit(t, OFFER_APPR_REQ, status_after="Offer Approval Pending",
                      outcome="Requested", actor=comp_analyst)
            p_rework *= 0.5
        appr_wait = LN(2.2, 0.55) if comp_approver["strict"] else LN(1.0, 0.6)
        tt = t
        for k in range(min(int(appr_wait / bdays(3)), 3)):
            tt = add_bmin(tt, appr_wait / (int(appr_wait / bdays(3)) + 1))
            case.emit(tt, OFFER_APPR_REMIND, outcome="Reminder", wait_reason=WAIT_COMP)
        t = add_bmin(t, appr_wait)
        case.emit(t, OFFER_APPROVED, status_after="Offer Approved", outcome="Approved",
                  actor=comp_approver["id"],
                  wait_reason=wrn(WAIT_COMP, appr_wait))
        t = case.sync_hiccup(t, SYS_CMP, p_base=0.026)
    else:
        g["gov_skip_offer_approval"] = True
        if chance(0.40):
            case.emit(add_bmin(t, U(30, 240)), POLICY_EXCEPTION, outcome="Exception Documented",
                      reason="EXPEDITED_CRITICAL_HIRE", actor=pick(APPROVERS)["id"])
            g["documented_offer_exception"] = True
    g["offer_revisions"] = revisions
    g["offer_approval_returns"] = returns

    # GAP-13 offer release delay
    send_delay = LN(1.6, 0.55) if chance(0.20) else LN(0.3, 0.55)
    t = add_bmin(t, send_delay * 0.5)
    case.emit(t, OFFER_GENERATED, status_after="Offer Generated", outcome="Generated",
              actor=pick(COORDINATORS))
    t = add_bmin(t, send_delay * 0.5)
    offer_version = 1
    case.emit(t, OFFER_SENT, status_after="Offer Sent", outcome="Sent",
              reason=f"OFFER_VERSION_{offer_version}",
              wait_reason=wrn(WAIT_COMP, send_delay))
    g["offer_send_delay_bdays"] = round(send_delay / WORK_MIN, 2)
    offer_sent_ts = t
    if chance(0.88):
        t = add_bmin(t, LN(0.4, 0.8))
        case.emit(t, OFFER_VIEWED, outcome="Viewed")

    negotiations = 0
    p_negotiate = 0.26 if out_of_band else 0.13
    if send_delay > bdays(2):
        p_negotiate += 0.08
    while chance(p_negotiate) and negotiations < 2:
        negotiations += 1
        t = add_bmin(t, LN(1.6, 0.6))
        case.emit(t, OFFER_NEGOTIATE, outcome="Negotiation Requested",
                  reason=pick(["COMPENSATION_EXPECTATION", "JOINING_DATE_REQUEST",
                               "RELOCATION_SUPPORT_REQUEST"]),
                  wait_reason=WAIT_CAND_RESP)
        t = add_bmin(t, LN(2.1, 0.6))
        offer_version += 1
        case.emit(t, OFFER_REVISED, status_after="Offer Revised", outcome="Revised",
                  reason=f"OFFER_VERSION_{offer_version}")
        case.emit(add_bmin(t, U(10, 120)), OFFER_SENT, status_after="Offer Sent",
                  outcome="Sent", reason=f"OFFER_VERSION_{offer_version}")
        offer_sent_ts = case.last_ts
        t = offer_sent_ts
        p_negotiate *= 0.5
    g["offer_negotiations"] = negotiations

    resp_wait = LN(3.0, 0.65)
    tt = t
    for k in range(min(int(resp_wait / bdays(3)), 3)):
        tt = add_bmin(tt, resp_wait / (int(resp_wait / bdays(3)) + 1))
        case.emit(tt, OFFER_FOLLOWUP, outcome="Follow-up", wait_reason=WAIT_CAND_RESP)

    offer_friction = (send_delay / WORK_MIN) + 1.5 * negotiations + 0.6 * res["reschedules"]
    p_decline = min(0.52, 0.11 + 0.022 * max(0.0, offer_friction - 2.0)
                    + (0.10 if out_of_band else 0.0)) * TEMPERAMENT_FACTOR[app["temperament"]]
    p_expire = 0.045
    t = add_bmin(t, resp_wait)
    roll = random.random()
    if roll < p_expire and resp_wait > bdays(3):
        case.emit(t, OFFER_EXPIRED, status_after="Offer Expired", outcome="Expired",
                  reason="NO_RESPONSE", wait_reason=WAIT_CAND_RESP)
        close_simple(case, t, "Offer Expired", "NO_RESPONSE")
        out = dict(outcome="Offer Expired", stage="S8", reason="NO_RESPONSE")
        out.update(res)
        return out
    if roll < p_expire + p_decline:
        reason = ("COMPENSATION_BELOW_EXPECTATION" if out_of_band
                  else wpick([("ACCEPTED_OTHER_OFFER", .45), ("COUNTER_OFFER_FROM_EMPLOYER", .22),
                              ("COMPENSATION_BELOW_EXPECTATION", .18),
                              ("ROLE_EXPECTATION_MISMATCH", .09), ("LOCATION_CONSTRAINT", .06)]))
        case.emit(t, OFFER_DECLINED, status_after="Offer Declined", outcome="Declined",
                  reason=reason, wait_reason=WAIT_CAND_RESP,
                  sla_code="SLA-14", sla_from_ts=offer_sent_ts)
        close_simple(case, t, "Offer Declined", reason)
        out = dict(outcome="Offer Declined", stage="S8", reason=reason)
        out.update(res)
        return out

    case.emit(t, OFFER_ACCEPTED, status_after="Offer Accepted", outcome="Accepted",
              wait_reason=wrn(WAIT_CAND_RESP, resp_wait))
    accept_ts = t
    t = case.sync_hiccup(t, SYS_ESG, p_base=0.026)

    # ---------------- Stage 9 : pre-joining & closure -----------------------
    # Verification and document collection run in parallel after acceptance, so the
    # branch events are timestamped independently and then emitted in time order.
    slow_vendor = req["location"] in SLOW_BGV_LOCATIONS
    gov_skip_bgv = chance(0.030)
    queue = []

    def q(ts_, activity, **kw):
        queue.append((ts_, activity, kw))

    t_bgv0 = add_bmin(accept_ts, LN(0.8, 0.7))
    if not gov_skip_bgv:
        q(t_bgv0, BGV_INIT, status_after="Verification In Progress", outcome="Initiated")
        bgv_wait = LN(11.0, 0.45) if slow_vendor or chance(0.08) else LN(4.5, 0.45)
        n_info = 0
        tt = t_bgv0
        while chance(0.30) and n_info < 2:
            n_info += 1
            tt = add_bmin(tt, bgv_wait / 3)
            q(tt, BGV_INFO_REQ, outcome="Information Requested",
              reason="INCOMPLETE_EMPLOYMENT_HISTORY", wait_reason=WAIT_VERIFY)
        if chance(0.11 if slow_vendor else 0.05):
            q(add_bmin(t_bgv0, bgv_wait * 0.75), BGV_EXCEPTION, outcome="Exception",
              reason="DISCREPANCY_RAISED", wait_reason=WAIT_VERIFY)
            g["bgv_exception"] = True
            bgv_wait *= 1.5
        t_bgv_done = add_bmin(t_bgv0, bgv_wait)
        q(t_bgv_done, BGV_CLEARED, status_after="Verification Cleared", outcome="Cleared",
          wait_reason=wrn(WAIT_VERIFY, bgv_wait))
        g["bgv_wait_bdays"] = round(bgv_wait / WORK_MIN, 2)
    else:
        g["gov_skip_bgv"] = True
        if chance(0.35):
            q(add_bmin(t_bgv0, U(30, 240)), POLICY_EXCEPTION, outcome="Exception Documented",
              reason="INTERNAL_MOBILITY_CHECK_WAIVED", actor=pick(APPROVERS)["id"])
            g["documented_bgv_exception"] = True

    td = add_bmin(accept_ts, LN(1.0, 0.6))
    q(td, DOC_REQUESTED, status_after="Documents Requested", outcome="Requested")
    doc_wait = LN(7.5, 0.5) if chance(0.14) else LN(2.5, 0.5)
    resub = 0
    tt = td
    while chance(0.26) and resub < 2:
        resub += 1
        tt = add_bmin(tt, doc_wait / 2)
        q(tt, DOC_RESUBMIT_REQ, outcome="Resubmission Requested",
          reason="DOCUMENT_INCOMPLETE", wait_reason=WAIT_DOCS)
        doc_wait += LN(2.5, 0.5)
    g["doc_resubmissions"] = resub
    g["doc_wait_bdays"] = round(doc_wait / WORK_MIN, 2)
    tdoc = add_bmin(td, doc_wait)
    q(tdoc, DOC_RECEIVED, status_after="Documents Received", outcome="Received",
      wait_reason=wrn(WAIT_DOCS, doc_wait))

    # Emit in time order. Before each step, if the candidate has been waiting a long
    # time for verification or documents, the disengagement hazard is evaluated
    # INSIDE that wait - so a withdrawal is evidenced during the delay, not after it.
    for ts_, activity, kw in sorted(queue, key=lambda x: x[0]):
        gap = bmin_between(case.last_ts, ts_)
        if gap > bdays(5):
            wd = try_withdraw(case, app, add_bmin(case.last_ts, gap * 0.65), "S9",
                              gap, 0.010)
            if wd:
                wd.update(res)
                wd["offer_accepted"] = True
                return wd
        case.emit(ts_, activity, **kw)
        if activity in (BGV_CLEARED, DOC_RECEIVED):
            case.sync_hiccup(case.last_ts, SYS_BGV if activity == BGV_CLEARED else SYS_ONB,
                             p_base=0.022)

    t = case.last_ts
    prejoin_friction = max(g.get("bgv_wait_bdays", 0), g.get("doc_wait_bdays", 0))
    wd = try_withdraw(case, app, t, "S9", bdays(max(0, prejoin_friction - 6)), 0.006)
    if wd:
        wd.update(res)
        wd["offer_accepted"] = True
        return wd

    t = add_bmin(t, LN(1.2, 0.6))
    case.emit(t, JOIN_READY, status_after="Joining Confirmed", outcome="Ready")

    notice_min = app["notice_period_days"] * WORK_MIN * (5.0 / 7.0)
    join_ts = add_bmin(accept_ts, notice_min)
    if join_ts < t:
        join_ts = add_bmin(t, bdays(U(2, 10)))
    p_nojoin = min(0.40, 0.055 + 0.012 * max(0.0, prejoin_friction - 8)
                   + (0.09 if g.get("bgv_exception") else 0.0)) \
        * TEMPERAMENT_FACTOR[app["temperament"]]
    if chance(p_nojoin):
        reason = ("VERIFICATION_EXCEPTION" if g.get("bgv_exception") and chance(0.5)
                  else pick(NOJOIN_REASONS))
        case.emit(join_ts, CAND_NO_JOIN, status_after="Did Not Join",
                  outcome="Accepted - Did Not Join", reason=reason, wait_reason=WAIT_NOTICE)
        close_simple(case, join_ts, "Accepted - Did Not Join", reason)
        out = dict(outcome="Accepted - Did Not Join", stage="S9", reason=reason,
                   offer_accepted=True)
        out.update(res)
        return out

    case.emit(join_ts, CAND_JOINED, status_after="Joined", outcome="Joined",
              wait_reason=WAIT_NOTICE)
    case.sync_hiccup(join_ts, SYS_HRIS, p_base=0.022)
    out = dict(outcome="Joined", stage="S9", reason="", offer_accepted=True,
               join_ts=join_ts)
    out.update(res)
    return out


# ------------------------------------------------------------------ requisition lifecycle
def run_requisition(req):
    g = req["gt"]
    case = Case(req["requisition_id"], "REQUISITION", req)
    req["case"] = case
    hm = next(h for h in HIRING_MANAGERS if h["id"] == req["hiring_manager_id"])
    approver = next(a for a in APPROVERS if a["id"] == req["approver_id"])
    wfp = next(x for x in WFP_ANALYSTS if x["id"] == req["workforce_analyst_id"])
    prio = req["priority"]

    # ---------------- Stage 1 : hiring demand -------------------------------
    t = case.emit(req["demand_ts"], DEMAND_RAISED, status_after="Demand Raised",
                  outcome="Raised", reason=req["demand_type"].upper().replace(" ", "_"),
                  actor=hm["id"])
    for _ in range(g["demand_returns"]):
        t = add_bmin(t, LN(1.1, 0.6))
        case.emit(t, DEMAND_RETURNED, status_after="Demand Returned", outcome="Returned",
                  reason=pick(DEMAND_RETURN_REASONS), actor=wfp["id"])
        t = add_bmin(t, LN(1.6, 0.7))
        case.emit(t, DEMAND_RESUBMIT, status_after="Demand Raised", outcome="Resubmitted",
                  actor=hm["id"])

    t = add_bmin(t, U(20, 420))
    case.emit(t, BUDGET_REQ, status_after="Budget Validation Pending", outcome="Requested",
              actor=wfp["id"])
    if g["budget_delay"]:
        bwait = LN(5.5, 0.55)
    else:
        bwait = LN(0.9, 0.6)
    tt = t
    for k in range(min(int(bwait / bdays(2)), 4)):
        tt = add_bmin(tt, bwait / (int(bwait / bdays(2)) + 1))
        case.emit(tt, BUDGET_REMIND, outcome="Reminder", wait_reason=WAIT_BUDGET)
    t = add_bmin(t, bwait)
    g["budget_wait_bdays"] = round(bwait / WORK_MIN, 2)

    if not g["skip_budget_validation"]:
        case.emit(t, BUDGET_OK, status_after="Budget Validated", outcome="Validated",
                  actor=wfp["id"], wait_reason=wrn(WAIT_BUDGET, bwait))
    elif g["documented_exception"]:
        case.emit(t, POLICY_EXCEPTION, outcome="Exception Documented",
                  reason="PRE_APPROVED_HEADCOUNT_PLAN", actor=approver["id"])

    # ---------------- Stage 2 : requisition & approval ----------------------
    t = add_bmin(t, LN(0.6, 0.7))
    case.emit(t, REQ_CREATED, status_after="Requisition Draft", outcome="Created", actor=hm["id"])
    t = add_bmin(t, LN(0.4, 0.8))
    case.emit(t, REQ_SUBMIT, status_after="Pending Approval", outcome="Submitted", actor=hm["id"])

    for _ in range(g["approval_returns"]):
        rwait = LN(3.0, 0.6) if g["approval_slow"] else LN(1.1, 0.6)
        tt = t
        for k in range(min(int(rwait / bdays(3)), 2)):
            tt = add_bmin(tt, rwait / (int(rwait / bdays(3)) + 1))
            case.emit(tt, REQ_REMIND, outcome="Reminder", wait_reason=WAIT_PENDING_APPROVAL)
        t = add_bmin(t, rwait)
        case.emit(t, REQ_RETURNED, status_after="Returned to Hiring Manager", outcome="Returned",
                  reason=pick([R_INCOMPLETE_PACKET] + DEMAND_RETURN_REASONS),
                  actor=approver["id"], wait_reason=WAIT_PENDING_APPROVAL)
        t = add_bmin(t, LN(1.5, 0.7))
        case.emit(t, REQ_RESUBMIT, status_after="Pending Approval", outcome="Resubmitted",
                  actor=hm["id"])

    awa = LN(4.6, 0.55) if g["approval_slow"] else LN(1.1, 0.6)
    if prio == "Critical":
        awa *= 0.65
    tt = t
    for k in range(min(int(awa / bdays(3)), 4)):
        tt = add_bmin(tt, awa / (int(awa / bdays(3)) + 1))
        case.emit(tt, REQ_REMIND, outcome="Reminder", wait_reason=WAIT_PENDING_APPROVAL,
                  actor=pick(COORDINATORS))
    t = add_bmin(t, awa)
    g["approval_wait_bdays"] = round(awa / WORK_MIN, 2)

    if chance(0.035):
        case.emit(t, REQ_REJECTED, status_after="Rejected", outcome="Rejected",
                  reason=pick(["HEADCOUNT_NOT_APPROVED", "BUSINESS_CASE_NOT_ACCEPTED"]),
                  actor=approver["id"], wait_reason=WAIT_PENDING_APPROVAL)
        req["terminal"] = ("Rejected", t)
        return case, []

    if not g["skip_requisition_approval"]:
        case.emit(t, REQ_APPROVED, status_after="Approved", outcome="Approved",
                  actor=approver["id"],
                  wait_reason=wrn(WAIT_PENDING_APPROVAL, awa))
        t = case.sync_hiccup(t, SYS_ATS, p_base=0.018)
    elif g["documented_exception"]:
        case.emit(t, POLICY_EXCEPTION, outcome="Exception Documented",
                  reason="DELEGATED_AUTHORITY_APPROVAL", actor=approver["id"])

    approved_ts = t

    if g["hold_reopen"]:
        t = add_bmin(t, LN(2.0, 0.7))
        case.emit(t, REQ_HOLD, status_after="On Hold", outcome="On Hold",
                  reason=pick(["BUDGET_REVIEW", "BUSINESS_PRIORITY_REVIEW"]), actor=approver["id"])
        t = add_bmin(t, LN(14.0, 0.6))
        case.emit(t, REQ_REOPEN, status_after="Approved", outcome="Reopened", actor=pick(COORDINATORS))

    # ---------------- Stage 3 : recruiter assignment ------------------------
    primary = [r for r in RECRUITERS if r["bu"] == req["business_unit"]] or RECRUITERS
    others = [r for r in RECRUITERS if r not in primary]
    pool = primary + random.sample(others, min(6, len(others)))
    if g["clean_run"]:
        pool = [r for r in pool if r["cap_weight"] == 1.0] or pool
    rec = random.choices(pool, weights=[r["cap_weight"] for r in pool], k=1)[0]
    load, nopen = recruiter_load(rec["id"], t)
    req["recruiter_id"] = rec["id"]
    req["recruiter_weighted_load_at_assignment"] = load
    req["recruiter_open_reqs_at_assignment"] = nopen
    lf = load / LOAD_REF
    g["recruiter_load_factor"] = round(lf, 2)

    assign_wait = LN(0.8, 0.6) * (1 + 0.45 * max(0.0, lf - 1.0))
    if prio == "Critical":
        assign_wait *= 0.7
    g["assignment_wait_bdays"] = round(assign_wait / WORK_MIN, 2)
    tt = t
    for k in range(min(int(assign_wait / bdays(2)), 4)):
        tt = add_bmin(tt, assign_wait / (int(assign_wait / bdays(2)) + 1))
        case.emit(tt, ASSIGN_REMIND, outcome="Follow-up", wait_reason=WAIT_UNASSIGNED,
                  actor=hm["id"])
    t = add_bmin(t, assign_wait)
    case.emit(t, RECRUITER_ASSIGNED, status_after="Recruiter Assigned", outcome="Assigned",
              actor=rec["id"], wait_reason=wrn(WAIT_UNASSIGNED, assign_wait))
    if lf > 1.5 and chance(0.18):
        t = add_bmin(t, LN(6.0, 0.6))
        case.emit(t, RECRUITER_REASSIGNED, outcome="Reassigned", reason="CAPACITY_REBALANCE",
                  actor=pick(COORDINATORS))

    jd_wait = LN(0.8, 0.55) * (1 + 0.25 * max(0.0, lf - 1.0))
    t = add_bmin(t, jd_wait)
    case.emit(t, JD_FINAL, status_after="Job Description Finalized", outcome="Finalized",
              actor=rec["id"], wait_reason=wrn(WAIT_JD, jd_wait))
    for _ in range(g["jd_revisions"]):
        t = add_bmin(t, LN(1.1, 0.6))
        case.emit(t, JD_REVISED, outcome="Revised", reason="ROLE_CRITERIA_NOT_ALIGNED",
                  actor=rec["id"], wait_reason=WAIT_JD)

    # ---------------- Stage 4 : sourcing ------------------------------------
    post_wait = LN(2.2, 0.6) if g["posting_delay"] else LN(0.6, 0.55)
    post_wait *= (1 + 0.25 * max(0.0, lf - 1.0))
    t = add_bmin(t, post_wait)
    case.emit(t, JOB_POSTED, status_after="Sourcing In Progress", outcome="Posted",
              actor=rec["id"], wait_reason=wrn(WAIT_SOURCING, post_wait))
    posting_ts = t
    if g["agency"]:
        case.emit(add_bmin(t, LN(1.2, 0.6)), AGENCY_ENGAGED, outcome="Engaged",
                  reason="HARD_TO_FILL_ROLE", actor=rec["id"])

    # ---------------- candidate pipeline ------------------------------------
    n_apps = app_volume(req)
    arrival = posting_ts
    arrivals = []
    pipeline_slow = req["job_level"] in ("L6", "L7") or req["gt"]["agency"]
    for i in range(n_apps):
        gap = LN(1.1 if not pipeline_slow else 3.0, 0.9)
        arrival = add_bmin(arrival, gap)
        arrivals.append(arrival)

    # cancellation point for CANCEL-intent requisitions
    cancel_ts = None
    if req["intent"] == "CANCEL":
        span = wpick([("S2", .18), ("S4", .30), ("S5", .28), ("S6", .24)])
        if span == "S2":
            cancel_ts = add_bmin(approved_ts, LN(6.0, 0.7))
        elif span == "S4":
            cancel_ts = add_bmin(posting_ts, LN(10.0, 0.7))
        elif span == "S5":
            cancel_ts = add_bmin(posting_ts, LN(22.0, 0.6))
        else:
            cancel_ts = add_bmin(posting_ts, LN(38.0, 0.6))
        g["cancel_stage"] = span

    # designate which pipeline position gets carried to offer
    offer_attempts_allowed = 1 if req["intent"] == "FILL" else (1 if chance(0.55) else 0)
    if req["intent"] == "FILL":
        offer_attempts_allowed = 3
    designate_from = max(0, min(len(arrivals) - 1, int(T(0, max(1, n_apps * 0.25), n_apps * 0.8))))

    hires_needed = req["positions_count"]
    hires = 0
    offers_made = 0
    results = []
    apps = []
    for i, ar in enumerate(arrivals):
        if cancel_ts is not None and ar >= cancel_ts:
            break
        app = make_application(req, ar)
        designated = (hires < hires_needed and offers_made < offer_attempts_allowed
                      and i >= designate_from and req["intent"] in ("FILL", "NOHIRE"))
        if designated and req["intent"] == "NOHIRE":
            designated = offers_made < offer_attempts_allowed
        res = run_application(req, app, case, designated, cancel_ts)
        if req["intent"] == "NOHIRE" and res.get("outcome") == "Joined":
            res["outcome"] = "Joined"      # emergent; keep the evidence consistent
        app["result"] = res
        apps.append(app)
        results.append(res)
        if res.get("offer_accepted") or res.get("outcome") in (
                "Offer Declined", "Offer Expired", "Accepted - Did Not Join"):
            offers_made += 1
        if res.get("outcome") == "Joined":
            hires += 1
            if hires >= hires_needed:
                break
        if designated and res.get("outcome") != "Joined" and req["intent"] == "FILL":
            # sourcing restart after a failed hire attempt
            if chance(0.45):
                case.emit(add_bmin(case.last_ts or ar, LN(1.5, 0.6)), SOURCING_EXTENDED,
                          outcome="Extended", reason="PIPELINE_INSUFFICIENT", actor=rec["id"])
            designate_from = i + 1

    # ---------------- Stage 9 : requisition closure -------------------------
    last_app_ts = max([a["case"].last_ts for a in apps], default=posting_ts)
    if cancel_ts is not None:
        case.emit(cancel_ts, REQ_CANCELLED, status_after="Cancelled", outcome="Cancelled",
                  reason=pick(CANCEL_REASONS), actor=hm["id"])
        req["terminal"] = ("Cancelled", cancel_ts)
    elif hires > 0:
        tfill = add_bmin(max(last_app_ts, posting_ts), U(30, 900))
        case.emit(tfill, REQ_FILLED, status_after="Filled", outcome="Filled", actor=rec["id"])
        req["terminal"] = ("Filled", tfill)
    else:
        tclose = add_bmin(max(last_app_ts, posting_ts), LN(3.0, 0.7))
        reason = ("NO_SUITABLE_CANDIDATE" if offers_made == 0 else
                  pick(["OFFER_DECLINED_NO_ALTERNATIVE", "REQUISITION_SUPERSEDED",
                        "HIRING_PAUSED"]))
        case.emit(tclose, REQ_CLOSED_NOHIRE, status_after="Closed Without Hire",
                  outcome="Closed Without Hire", reason=reason, actor=rec["id"])
        req["terminal"] = ("Closed Without Hire", tclose)

    # register the recruiter's active window for the load model
    weight = PRIORITY_LOAD_WEIGHT[prio] * req["role_complexity_score"] / 1.4
    register_requisition_window(rec["id"], approved_ts, req["terminal"][1], weight)

    GROUND_TRUTH.append(dict(
        requisition_id=req["requisition_id"], intent=req["intent"],
        **{f"gt_{k}": v for k, v in g.items()}))
    return case, apps


# ------------------------------------------------------------------ generation run
REQS, APPS = [], []


def demand_dates(n):
    """Chronologically ordered demand dates with mild volume growth."""
    span = (WINDOW_END - WINDOW_START).days
    out = []
    for _ in range(n):
        u = random.random() ** 0.88           # slight tilt to later months
        d = WINDOW_START + timedelta(days=int(u * span))
        out.append(normalize(d.replace(hour=U(9, 16), minute=U(0, 59))))
    return sorted(out)


print(f"generating {N_REQUISITIONS} requisitions ...")
for i, dts in enumerate(demand_dates(N_REQUISITIONS), start=1):
    req = make_requisition(i, dts)
    case, apps = run_requisition(req)
    REQS.append(req)
    APPS.extend(apps)
    if i % 200 == 0:
        print(f"  {i} requisitions, {len(EVENTS)} events so far")

# ------------------------------------------------------------------ right-censoring
ALL_EVENTS = EVENTS
KEPT = [e for e in ALL_EVENTS if e["event_timestamp"] <= CUTOFF.strftime("%Y-%m-%d %H:%M:%S")]
DROPPED = len(ALL_EVENTS) - len(KEPT)
KEPT.sort(key=lambda e: (e["requisition_id"], e["case_type"] != "REQUISITION",
                         e["process_instance_id"], e["event_timestamp"], e["event_id"]))

BY_CASE = defaultdict(list)
for e in KEPT:
    BY_CASE[e["process_instance_id"]].append(e)
for v in BY_CASE.values():
    v.sort(key=lambda e: (e["event_timestamp"], e["event_id"]))

# waiting_time_minutes was computed on the full journey; recompute on kept events so
# the published log is internally consistent after censoring
for pid, evs in BY_CASE.items():
    prev = None
    for e in evs:
        e["waiting_time_minutes"] = "" if prev is None else bmin_between(
            datetime.strptime(prev["event_timestamp"], "%Y-%m-%d %H:%M:%S"),
            datetime.strptime(e["event_timestamp"], "%Y-%m-%d %H:%M:%S"))
        prev = e


def ts(e):
    return datetime.strptime(e["event_timestamp"], "%Y-%m-%d %H:%M:%S")


def first_of(evs, activity):
    for e in evs:
        if e["activity_name"] == activity:
            return e
    return None


def last_of(evs, activity):
    for e in reversed(evs):
        if e["activity_name"] == activity:
            return e
    return None


def count_of(evs, activity):
    return sum(1 for e in evs if e["activity_name"] == activity)


def fts(e):
    return e["event_timestamp"] if e else ""


def flag_rollup(evs):
    return dict(
        event_count=len(evs),
        rework_event_count=sum(1 for e in evs if e["rework_flag"] == "Y"),
        manual_touch_count=sum(1 for e in evs if e["manual_touch_flag"] == "Y"),
        handoff_count=sum(1 for e in evs if e["handoff_flag"] == "Y"),
        exception_count=sum(1 for e in evs if e["exception_flag"] == "Y"),
        data_quality_issue_count=sum(1 for e in evs if e["data_quality_flag"] == "Y"),
        sla_eligible_count=sum(1 for e in evs if e["sla_code"]),
        sla_breach_count=sum(1 for e in evs if e["sla_breach_flag"] == "Y"),
        sla_overrun_minutes_total=sum(int(e["sla_overrun_minutes"] or 0) for e in evs),
        follow_up_event_count=sum(1 for e in evs if e["activity_name"] in (
            BUDGET_REMIND, REQ_REMIND, ASSIGN_REMIND, SCREEN_REMIND, SHORTLIST_REMIND,
            ASM_REMIND, INT_SCHED_FOLLOWUP, FB_REMIND, SEL_REMIND, OFFER_APPR_REMIND,
            OFFER_FOLLOWUP)),
        waiting_minutes_total=sum(int(e["waiting_time_minutes"] or 0) for e in evs),
    )


# Activities that send a case back to an already-completed activity or stage.
# Their paired "resubmit / re-confirm" events are deliberately excluded so one
# return cycle counts as exactly one loop (BRD 6.4).
LOOP_ACTIVITIES = {
    DEMAND_RETURNED, REQ_RETURNED, REQ_REOPEN, RECRUITER_REASSIGNED, JD_REVISED,
    SOURCING_EXTENDED, MORE_PROFILES, RESCREEN, ASM_REISSUED, INT_RESCHEDULED,
    FB_RETURNED, EXTRA_ROUND, SEL_REOPENED, COMP_REVISED, OFFER_RETURNED,
    OFFER_NEGOTIATE, DOC_RESUBMIT_REQ, MANUAL_REENTRY,
}


def rework_loops(evs):
    """Count of returns to a previously completed activity or stage (BRD 6.4).

    Repeated interview rounds, extra applications and multi-position hiring are
    legitimate repetition and are NOT counted here.
    """
    return sum(1 for e in evs if e["activity_name"] in LOOP_ACTIVITIES)


FOLLOW_UP_ACTIVITIES = {
    BUDGET_REMIND, REQ_REMIND, ASSIGN_REMIND, SCREEN_REMIND, SHORTLIST_REMIND,
    ASM_REMIND, INT_SCHED_FOLLOWUP, FB_REMIND, SEL_REMIND, OFFER_APPR_REMIND,
    OFFER_FOLLOWUP, SYNC_RETRIED,
}


def max_gap_bdays(evs):
    """Longest gap between consecutive *progression* events (T2-10).

    Reminder and follow-up events are excluded: a chase email does not move the
    case forward, so it must not disguise a long inactivity interval.
    """
    core = [e for e in evs if e["activity_name"] not in FOLLOW_UP_ACTIVITIES]
    mx = 0
    for a, b in zip(core, core[1:]):
        mx = max(mx, bmin_between(ts(a), ts(b)))
    return round(mx / WORK_MIN, 2)


def max_raw_gap_bdays(evs):
    mx = 0
    for e in evs:
        mx = max(mx, int(e["waiting_time_minutes"] or 0))
    return round(mx / WORK_MIN, 2)


# ------------------------------------------------------------------ application master
APP_BY_ID = {a["application_id"]: a for a in APPS}
APPS_BY_REQ = defaultdict(list)
for a in APPS:
    APPS_BY_REQ[a["requisition_id"]].append(a)

APP_ROWS = []
for a in APPS:
    evs = BY_CASE.get(a["application_id"], [])
    if not evs:
        continue
    req = next(r for r in REQS if r["requisition_id"] == a["requisition_id"])
    closed = last_of(evs, APP_CLOSED)
    joined = last_of(evs, CAND_JOINED)
    withdrew = last_of(evs, CAND_WITHDREW)
    rejected = last_of(evs, CAND_REJECTED)
    declined = last_of(evs, OFFER_DECLINED)
    expired = last_of(evs, OFFER_EXPIRED)
    nojoin = last_of(evs, CAND_NO_JOIN)
    noshow = last_of(evs, INT_NOSHOW)

    if joined:
        outcome, out_stage, reason = "Joined", "S9", ""
    elif nojoin:
        outcome, out_stage, reason = "Accepted - Did Not Join", "S9", nojoin["reason_code"]
    elif declined:
        outcome, out_stage, reason = "Offer Declined", "S8", declined["reason_code"]
    elif expired:
        outcome, out_stage, reason = "Offer Expired", "S8", expired["reason_code"]
    elif withdrew:
        outcome, out_stage, reason = "Withdrawn", withdrew["stage_code"], withdrew["reason_code"]
    elif rejected:
        stg = rejected["stage_code"]
        label = {"S5": "Rejected - Screening/Shortlist", "S6": "Rejected - Interview/Assessment",
                 "S7": "Rejected - Final Selection"}.get(stg, "Rejected")
        outcome, out_stage, reason = label, stg, rejected["reason_code"]
    elif closed and closed["outcome_code"] == "Requisition Cancelled":
        outcome, out_stage, reason = "Closed - Requisition Cancelled", closed["stage_code"], \
            closed["reason_code"]
    elif noshow and closed:
        outcome, out_stage, reason = "No Show", "S6", noshow["reason_code"]
    else:
        outcome, out_stage, reason = "In Progress", evs[-1]["stage_code"], ""

    roll = flag_rollup(evs)
    first_ts, last_ts = ts(evs[0]), ts(evs[-1])
    offer_sent = last_of(evs, OFFER_SENT)
    accepted = last_of(evs, OFFER_ACCEPTED)
    row = dict(
        application_id=a["application_id"], requisition_id=a["requisition_id"],
        candidate_surrogate_id=a["candidate_surrogate_id"], source_channel=a["source_channel"],
        business_unit=req["business_unit"], job_family=req["job_family"],
        location=req["location"], job_level=req["job_level"], priority=req["priority"],
        recruiter_id_pseudonymous=req["recruiter_id"] or "",
        hiring_manager_id_pseudonymous=req["hiring_manager_id"],
        candidate_engagement_profile=a["temperament"],
        notice_period_days=a["notice_period_days"],
        application_created_timestamp=fts(evs[0]),
        screening_decision_timestamp=fts(last_of(evs, SCREEN_DONE)),
        shortlist_submitted_timestamp=fts(last_of(evs, SHORTLIST_SUBMIT)),
        hm_review_decision_timestamp=fts(last_of(evs, SHORTLIST_DECISION)),
        assessment_assigned_timestamp=fts(first_of(evs, ASM_ASSIGNED)),
        assessment_completed_timestamp=fts(last_of(evs, ASM_DONE)),
        first_interview_timestamp=fts(first_of(evs, INT_CONDUCTED)),
        last_feedback_timestamp=fts(last_of(evs, FB_SUBMITTED)),
        selection_decision_timestamp=fts(last_of(evs, SEL_DECISION)),
        offer_approved_timestamp=fts(last_of(evs, OFFER_APPROVED)),
        offer_sent_timestamp=fts(offer_sent),
        offer_response_timestamp=fts(accepted or declined or expired),
        bgv_cleared_timestamp=fts(last_of(evs, BGV_CLEARED)),
        documents_received_timestamp=fts(last_of(evs, DOC_RECEIVED)),
        join_timestamp=fts(joined),
        final_outcome_timestamp=fts(evs[-1]) if outcome != "In Progress" else "",
        application_outcome=outcome, outcome_stage=out_stage, outcome_reason_code=reason,
        withdrawal_stage=(withdrew["stage_code"] if withdrew else ""),
        offer_made_flag="Y" if offer_sent else "N",
        offer_acceptance_flag="Y" if accepted else ("N" if (declined or expired) else ""),
        joining_flag="Y" if joined else ("N" if (nojoin or declined) else ""),
        number_of_interview_rounds=count_of(evs, INT_CONDUCTED),
        interview_reschedule_count=count_of(evs, INT_RESCHEDULED),
        interview_noshow_count=count_of(evs, INT_NOSHOW),
        feedback_return_count=count_of(evs, FB_RETURNED),
        feedback_late_count=sum(1 for e in evs
                                if e["activity_name"] == FB_SUBMITTED and e["sla_breach_flag"] == "Y"),
        assessment_count=count_of(evs, ASM_ASSIGNED),
        assessment_expired_flag="Y" if count_of(evs, ASM_EXPIRED) else "N",
        comp_revision_count=count_of(evs, COMP_REVISED),
        offer_approval_return_count=count_of(evs, OFFER_RETURNED),
        offer_revision_count=count_of(evs, OFFER_REVISED),
        offer_negotiation_count=count_of(evs, OFFER_NEGOTIATE),
        selection_reopened_flag="Y" if count_of(evs, SEL_REOPENED) else "N",
        additional_round_requested_flag="Y" if count_of(evs, EXTRA_ROUND) else "N",
        verification_exception_flag="Y" if count_of(evs, BGV_EXCEPTION) else "N",
        document_resubmission_count=count_of(evs, DOC_RESUBMIT_REQ),
        integration_failure_count=count_of(evs, SYNC_FAILED),
        rework_loop_count=rework_loops(evs),
        max_inactivity_business_days=max_gap_bdays(evs),
        max_event_gap_business_days=max_raw_gap_bdays(evs),
        recorded_wait_business_days=round(sum(
            int(e["waiting_time_minutes"] or 0) for e in evs if e["waiting_reason_code"]) / WORK_MIN, 2),
        total_elapsed_business_minutes=bmin_between(first_ts, last_ts),
        total_elapsed_business_days=mins_to_bdays(bmin_between(first_ts, last_ts)),
        is_open_censored="Y" if outcome == "In Progress" else "N",
        extraction_batch_id=EXTRACTION_BATCH,
        **roll,
    )
    APP_ROWS.append(row)
APP_ROW_BY_ID = {r["application_id"]: r for r in APP_ROWS}


# ------------------------------------------------------------------ requisition master
REQ_ROWS = []
for req in REQS:
    rid = req["requisition_id"]
    evs = BY_CASE.get(rid, [])
    if not evs:
        continue
    app_rows = [APP_ROW_BY_ID[a["application_id"]] for a in APPS_BY_REQ[rid]
                if a["application_id"] in APP_ROW_BY_ID]
    filled = last_of(evs, REQ_FILLED)
    cancelled = last_of(evs, REQ_CANCELLED)
    nohire = last_of(evs, REQ_CLOSED_NOHIRE)
    rejected = last_of(evs, REQ_REJECTED)
    hold = last_of(evs, REQ_HOLD)
    reopen = last_of(evs, REQ_REOPEN)
    approved = first_of(evs, REQ_APPROVED)

    if filled:
        outcome = "Filled"
    elif cancelled:
        outcome = "Cancelled"
    elif nohire:
        outcome = "Closed Without Hire"
    elif rejected:
        outcome = "Rejected at Approval"
    elif hold and (not reopen or ts(hold) > ts(reopen)):
        outcome = "On Hold"
    else:
        outcome = "Open / In Progress"

    joined_apps = [r for r in app_rows if r["application_outcome"] == "Joined"]
    first_join = min([r["join_timestamp"] for r in joined_apps], default="")
    roll = flag_rollup(evs)
    first_ts, last_ts = ts(evs[0]), ts(evs[-1])

    def milestone(a, which="first"):
        e = first_of(evs, a) if which == "first" else last_of(evs, a)
        return ts(e) if e else None

    demand = first_ts
    submit = milestone(REQ_SUBMIT)
    appr = milestone(REQ_APPROVED)
    assign = milestone(RECRUITER_ASSIGNED)
    post = milestone(JOB_POSTED)
    close_ts = ts(filled or cancelled or nohire or rejected) if (filled or cancelled or nohire or rejected) else None
    first_shortlist = min([r["shortlist_submitted_timestamp"] for r in app_rows
                           if r["shortlist_submitted_timestamp"]], default="")
    first_interview = min([r["first_interview_timestamp"] for r in app_rows
                           if r["first_interview_timestamp"]], default="")
    first_offer = min([r["offer_sent_timestamp"] for r in app_rows
                       if r["offer_sent_timestamp"]], default="")

    agg_app = lambda k: sum(r[k] for r in app_rows) if app_rows else 0
    row = dict(
        requisition_id=rid, business_unit=req["business_unit"], job_family=req["job_family"],
        location=req["location"], job_level=req["job_level"], priority=req["priority"],
        demand_type=req["demand_type"], positions_count=req["positions_count"],
        role_complexity_score=req["role_complexity_score"],
        hiring_manager_id_pseudonymous=req["hiring_manager_id"],
        approver_id_pseudonymous=req["approver_id"],
        workforce_analyst_id_pseudonymous=req["workforce_analyst_id"],
        recruiter_id_pseudonymous=req["recruiter_id"] or "",
        recruiter_weighted_load_at_assignment=req.get("recruiter_weighted_load_at_assignment", ""),
        recruiter_open_reqs_at_assignment=req.get("recruiter_open_reqs_at_assignment", ""),
        requisition_open_timestamp=fts(evs[0]),
        demand_validated_timestamp=fts(last_of(evs, BUDGET_OK)),
        requisition_submit_timestamp=fts(first_of(evs, REQ_SUBMIT)),
        requisition_approved_timestamp=fts(approved),
        recruiter_assigned_timestamp=fts(first_of(evs, RECRUITER_ASSIGNED)),
        jd_finalized_timestamp=fts(first_of(evs, JD_FINAL)),
        first_posting_timestamp=fts(first_of(evs, JOB_POSTED)),
        first_shortlist_timestamp=first_shortlist,
        first_interview_timestamp=first_interview,
        first_offer_sent_timestamp=first_offer,
        first_join_timestamp=first_join,
        requisition_close_timestamp=fts(filled or cancelled or nohire or rejected),
        requisition_outcome=outcome,
        closure_reason_code=(filled or cancelled or nohire or rejected or {}).get("reason_code", ""),
        days_open=round(((close_ts or CUTOFF) - demand).total_seconds() / 86400, 2),
        business_days_open=mins_to_bdays(bmin_between(demand, close_ts or CUTOFF)),
        days_to_approval=mins_to_bdays(bmin_between(submit, appr)) if submit and appr else "",
        days_demand_to_approval=mins_to_bdays(bmin_between(demand, appr)) if appr else "",
        days_to_assignment=mins_to_bdays(bmin_between(appr, assign)) if appr and assign else "",
        days_to_posting=mins_to_bdays(bmin_between(assign, post)) if assign and post else "",
        days_to_first_shortlist=(mins_to_bdays(bmin_between(
            post, datetime.strptime(first_shortlist, "%Y-%m-%d %H:%M:%S")))
            if post and first_shortlist else ""),
        days_to_first_interview=(mins_to_bdays(bmin_between(
            post, datetime.strptime(first_interview, "%Y-%m-%d %H:%M:%S")))
            if post and first_interview else ""),
        days_to_first_offer=(mins_to_bdays(bmin_between(
            post, datetime.strptime(first_offer, "%Y-%m-%d %H:%M:%S")))
            if post and first_offer else ""),
        time_to_fill_business_days=(mins_to_bdays(bmin_between(
            appr, datetime.strptime(first_join, "%Y-%m-%d %H:%M:%S")))
            if appr and first_join else ""),
        applications_count=len(app_rows),
        screened_count=sum(1 for r in app_rows if r["screening_decision_timestamp"]),
        shortlisted_count=sum(1 for r in app_rows if r["shortlist_submitted_timestamp"]),
        interviewed_count=sum(1 for r in app_rows if r["number_of_interview_rounds"] > 0),
        offers_count=sum(1 for r in app_rows if r["offer_made_flag"] == "Y"),
        offers_accepted_count=sum(1 for r in app_rows if r["offer_acceptance_flag"] == "Y"),
        offers_declined_count=sum(1 for r in app_rows if r["application_outcome"] == "Offer Declined"),
        withdrawn_count=sum(1 for r in app_rows if r["application_outcome"] == "Withdrawn"),
        hires_count=len(joined_apps),
        approval_return_count=count_of(evs, REQ_RETURNED),
        demand_return_count=count_of(evs, DEMAND_RETURNED),
        jd_revision_count=count_of(evs, JD_REVISED),
        sourcing_extension_count=count_of(evs, SOURCING_EXTENDED),
        additional_profiles_requested_count=count_of(evs, MORE_PROFILES),
        recruiter_reassigned_flag="Y" if count_of(evs, RECRUITER_REASSIGNED) else "N",
        reopened_flag="Y" if count_of(evs, REQ_REOPEN) else "N",
        on_hold_flag="Y" if count_of(evs, REQ_HOLD) else "N",
        policy_exception_documented_flag="Y" if count_of(evs, POLICY_EXCEPTION) else "N",
        integration_failure_count=count_of(evs, SYNC_FAILED) + agg_app("integration_failure_count"),
        rework_loop_count=rework_loops(evs),
        total_elapsed_business_minutes=bmin_between(first_ts, last_ts),
        total_elapsed_business_days=mins_to_bdays(bmin_between(first_ts, last_ts)),
        pipeline_rework_event_count=agg_app("rework_event_count"),
        pipeline_manual_touch_count=agg_app("manual_touch_count"),
        pipeline_sla_breach_count=agg_app("sla_breach_count"),
        pipeline_follow_up_event_count=agg_app("follow_up_event_count"),
        max_inactivity_business_days=max_gap_bdays(evs),
        max_event_gap_business_days=max_raw_gap_bdays(evs),
        recorded_wait_business_days=round(sum(
            int(e["waiting_time_minutes"] or 0) for e in evs if e["waiting_reason_code"]) / WORK_MIN, 2),
        sla_profile=req["sla_profile"], process_version=req["process_version"],
        policy_version=req["policy_version"], business_calendar=req["business_calendar"],
        is_open_censored="Y" if outcome in ("Open / In Progress", "On Hold") else "N",
        extraction_batch_id=EXTRACTION_BATCH,
        **roll,
    )
    REQ_ROWS.append(row)


# ------------------------------------------------------------------ stage transitions
STAGE_SLA_DAYS = {"S1": 4.0, "S2": 4.0, "S3": 4.0, "S4": 10.0, "S5": 7.0,
                  "S6": 15.0, "S7": 4.0, "S8": 10.0, "S9": 20.0}
STAGE_ROWS = []
for pid, evs in BY_CASE.items():
    core = [e for e in evs if e["stage_code"] != "S0"]
    if not core:
        continue
    occ, cur, entry = [], None, None
    for e in core:
        if e["stage_code"] != cur:
            if cur is not None:
                occ.append((cur, entry, e))
            cur, entry = e["stage_code"], e
    occ.append((cur, entry, None))
    seen = Counter()
    for k, (stage, entry_e, next_e) in enumerate(occ):
        seen[stage] += 1
        exit_e = next_e if next_e is not None else core[-1]
        a, b = ts(entry_e), ts(exit_e)
        elapsed = bmin_between(a, b)
        target = int(round(STAGE_SLA_DAYS[stage] * WORK_MIN
                           * PRIORITY_SLA_FACTOR[entry_e["priority"]]))
        STAGE_ROWS.append(dict(
            process_instance_id=pid, case_type=entry_e["case_type"],
            requisition_id=entry_e["requisition_id"],
            application_id=entry_e["application_id"],
            stage_code=stage, stage_name=STAGE_NAME[stage],
            stage_sequence=STAGE_SEQ[stage], stage_occurrence=seen[stage],
            is_revisit="Y" if seen[stage] > 1 else "N",
            entry_activity=entry_e["activity_name"], exit_activity=exit_e["activity_name"],
            entry_timestamp=entry_e["event_timestamp"], exit_timestamp=exit_e["event_timestamp"],
            events_in_stage=sum(1 for e in core if e["stage_code"] == stage
                                and ts(entry_e) <= ts(e) <= b),
            elapsed_business_minutes=elapsed, elapsed_business_days=mins_to_bdays(elapsed),
            stage_sla_target_minutes=target,
            stage_sla_breach_flag="Y" if elapsed > target else "N",
            business_unit=entry_e["business_unit"], job_family=entry_e["job_family"],
            location=entry_e["location"], job_level=entry_e["job_level"],
            priority=entry_e["priority"],
            recruiter_id_pseudonymous=entry_e["recruiter_id_pseudonymous"],
            extraction_batch_id=EXTRACTION_BATCH))

# ------------------------------------------------------------------ recruiter load snapshots
LOAD_ROWS = []
snap = WINDOW_START
snap = snap - timedelta(days=snap.weekday())
while snap <= CUTOFF:
    for r in RECRUITERS:
        wl, n = recruiter_load(r["id"], snap)
        if n == 0:
            continue
        aged = 0
        crit = 0
        for s, e, wt in RECRUITER_WINDOWS[r["id"]]:
            if s <= snap and (e is None or e >= snap):
                if (snap - s).days > 60:
                    aged += 1
        LOAD_ROWS.append(dict(
            snapshot_date=snap.strftime("%Y-%m-%d"), recruiter_id_pseudonymous=r["id"],
            open_requisitions=n, weighted_load=wl,
            requisitions_open_over_60_days=aged,
            portfolio_design_band=("heavy" if r["cap_weight"] > 2 else
                                   "medium" if r["cap_weight"] > 1.2 else "standard"),
            extraction_batch_id=EXTRACTION_BATCH))
    snap += timedelta(days=7)

LOADS = [r["weighted_load"] for r in LOAD_ROWS]
LOAD_P80 = (stx.quantiles(LOADS, n=10)[7] if len(LOADS) > 20 else 0)


# ------------------------------------------------------------------ deterministic gap detection
# Rules read ONLY the published event log + SLA policy. No generator intent is used.
REQ_ROW_BY_ID = {r["requisition_id"]: r for r in REQ_ROWS}
GAP_ROWS = []


def add_gap(gap, case_type, pid, req_id, app_id, stage, rule, measured, threshold,
            unit, evidence, summary):
    GAP_ROWS.append(dict(
        gap_reference=gap, case_type=case_type, process_instance_id=pid,
        requisition_id=req_id, application_id=app_id or "", activity_stage=STAGE_NAME.get(stage, stage),
        stage_code=stage, detection_rule=rule,
        measured_value=measured, threshold_value=threshold, measure_unit=unit,
        evidence_event_ids="|".join(evidence[:6]), evidence_event_count=len(evidence),
        evidence_summary=summary, detected_at=CUTOFF.strftime("%Y-%m-%d %H:%M:%S"),
        extraction_batch_id=EXTRACTION_BATCH))


def interval(evs, a_from, a_to, anchor_from="last", anchor_to="last"):
    e_to = None
    for e in (evs if anchor_to == "first" else reversed(evs)):
        if e["activity_name"] == a_to:
            e_to = e
            break
    if e_to is None:
        return None, None, None
    cands = [e for e in evs if e["activity_name"] == a_from and ts(e) <= ts(e_to)]
    if not cands:
        return None, None, None
    e_from = cands[0] if anchor_from == "first" else cands[-1]
    return bmin_between(ts(e_from), ts(e_to)), e_from, e_to


def open_wait(evs, a_from, a_to):
    """Wait that is still running at the cut-off because a_to never happened."""
    cands = [e for e in evs if e["activity_name"] == a_from]
    if not cands or any(e["activity_name"] == a_to for e in evs):
        return None, None
    e = cands[-1]
    return bmin_between(ts(e), CUTOFF), e


def ids(*evs):
    return [e["event_id"] for e in evs if e]


TERMINAL_APP = {CAND_WITHDREW, CAND_REJECTED, OFFER_DECLINED, OFFER_EXPIRED,
                CAND_NO_JOIN, APP_CLOSED}

for req in REQS:
    rid = req["requisition_id"]
    evs = BY_CASE.get(rid, [])
    if not evs:
        continue
    prio = req["priority"]
    rrow = REQ_ROW_BY_ID.get(rid, {})

    # ---- GAP-01 incomplete hiring demand
    n_ret = count_of(evs, DEMAND_RETURNED)
    if n_ret >= 2:
        ev = [e for e in evs if e["activity_name"] in (DEMAND_RETURNED, DEMAND_RESUBMIT)]
        add_gap("GAP-01", "REQUISITION", rid, rid, "", "S1",
                "Demand return/resubmit events >= 2", n_ret, 2, "events", ids(*ev),
                f"{n_ret} returns for {', '.join(sorted({e['reason_code'] for e in ev if e['reason_code']}))}")

    # ---- GAP-02 budget / headcount validation beyond SLA
    m, ef, et = interval(evs, BUDGET_REQ, BUDGET_OK, "first", "last")
    tgt = sla_minutes("SLA-01", prio)
    if m is not None and m > tgt:
        add_gap("GAP-02", "REQUISITION", rid, rid, "", "S1",
                "Budget validation elapsed > SLA-01", mins_to_bdays(m), mins_to_bdays(tgt),
                "business days", ids(ef, et),
                f"validation pending {mins_to_bdays(m)} business days "
                f"({count_of(evs, BUDGET_REMIND)} reminders)")
    else:
        om, oe = open_wait(evs, BUDGET_REQ, BUDGET_OK)
        if om is not None and om > tgt:
            add_gap("GAP-02", "REQUISITION", rid, rid, "", "S1",
                    "Budget validation still pending at cut-off > SLA-01",
                    mins_to_bdays(om), mins_to_bdays(tgt), "business days", ids(oe),
                    "validation requested but never evidenced as validated")

    # ---- GAP-03 approval queue delay / repeated returns
    m, ef, et = interval(evs, REQ_SUBMIT, REQ_APPROVED, "first", "last")
    tgt = sla_minutes("SLA-02", prio)
    n_ar = count_of(evs, REQ_RETURNED)
    if (m is not None and m > tgt) or n_ar >= 2:
        ev = [e for e in evs if e["activity_name"] in (REQ_RETURNED, REQ_RESUBMIT, REQ_REMIND)]
        add_gap("GAP-03", "REQUISITION", rid, rid, "", "S2",
                "Submission-to-approval > SLA-02 or approval returns >= 2",
                mins_to_bdays(m) if m is not None else "", mins_to_bdays(tgt),
                "business days", ids(ef, et) + ids(*ev),
                f"approval lead time {mins_to_bdays(m) if m is not None else 'open'} business days, "
                f"{n_ar} returns, {count_of(evs, REQ_REMIND)} reminders")
    else:
        om, oe = open_wait(evs, REQ_SUBMIT, REQ_APPROVED)
        if om is not None and om > tgt and not count_of(evs, REQ_REJECTED):
            add_gap("GAP-03", "REQUISITION", rid, rid, "", "S2",
                    "Requisition pending approval at cut-off > SLA-02",
                    mins_to_bdays(om), mins_to_bdays(tgt), "business days", ids(oe),
                    "submitted but no approval action evidenced")

    # ---- GAP-04 delayed recruiter assignment
    m, ef, et = interval(evs, REQ_APPROVED, RECRUITER_ASSIGNED, "last", "first")
    tgt = sla_minutes("SLA-03", prio)
    if m is not None and m > tgt:
        add_gap("GAP-04", "REQUISITION", rid, rid, "", "S3",
                "Approval-to-assignment > SLA-03", mins_to_bdays(m), mins_to_bdays(tgt),
                "business days", ids(ef, et) + ids(*[e for e in evs if e["activity_name"] == ASSIGN_REMIND]),
                f"unassigned {mins_to_bdays(m)} business days; recruiter weighted load at "
                f"assignment {rrow.get('recruiter_weighted_load_at_assignment')}")

    # ---- GAP-05 JD revision loop / sourcing launch delay
    n_jd = count_of(evs, JD_REVISED)
    m, ef, et = interval(evs, RECRUITER_ASSIGNED, JOB_POSTED, "last", "first")
    tgt = sla_minutes("SLA-05", prio)
    if n_jd >= 2 or (m is not None and m > tgt):
        ev = [e for e in evs if e["activity_name"] == JD_REVISED]
        add_gap("GAP-05", "REQUISITION", rid, rid, "", "S4",
                "JD revisions >= 2 or assignment-to-posting > SLA-05",
                mins_to_bdays(m) if m is not None else "", mins_to_bdays(tgt),
                "business days", ids(ef, et) + ids(*ev),
                f"{n_jd} JD revisions; sourcing launched after "
                f"{mins_to_bdays(m) if m is not None else 'n/a'} business days")

    # ---- GAP-17 recruiter capacity constraint
    wl = rrow.get("recruiter_weighted_load_at_assignment") or 0
    assign_m, aef, aet = interval(evs, REQ_APPROVED, RECRUITER_ASSIGNED, "last", "first")
    follow = rrow.get("follow_up_event_count", 0) + rrow.get("pipeline_follow_up_event_count", 0)
    if wl and wl >= LOAD_P80 and ((assign_m or 0) > sla_minutes("SLA-03", prio) or follow >= 6):
        add_gap("GAP-17", "REQUISITION", rid, rid, "", "S3",
                "Weighted recruiter load >= P80 with aged assignment queue or >=6 follow-ups",
                wl, round(LOAD_P80, 2), "weighted open requisitions",
                ids(aef, aet), f"recruiter {req['recruiter_id']} carried {wl} weighted load "
                               f"({rrow.get('recruiter_open_reqs_at_assignment')} open requisitions); "
                               f"{follow} follow-up events on this requisition")

    # ---- GAP-18 governance / path deviation
    has_appr = count_of(evs, REQ_APPROVED) > 0
    has_budget = count_of(evs, BUDGET_OK) > 0
    has_assign = count_of(evs, RECRUITER_ASSIGNED) > 0
    documented = count_of(evs, POLICY_EXCEPTION) > 0
    viol = []
    if has_assign and not has_appr:
        viol.append("Recruiter Assigned without an evidenced Requisition Approved")
    if has_appr and not has_budget:
        viol.append("Requisition Approved without an evidenced Workforce Budget Validated")
    if viol and not documented:
        add_gap("GAP-18", "REQUISITION", rid, rid, "", "S2",
                "Mandatory control event missing and no documented exception",
                len(viol), 0, "missing controls",
                ids(first_of(evs, RECRUITER_ASSIGNED), first_of(evs, REQ_APPROVED)),
                "; ".join(viol))

    # ---- GAP-16 integration / manual handoff failures (requisition scope)
    fails = [e for e in evs if e["activity_name"] in (SYNC_FAILED, DUP_UPDATE, MANUAL_REENTRY)]
    if fails:
        lag, lf_, lt_ = interval(evs, SYNC_FAILED, SYNC_COMPLETED, "first", "last")
        add_gap("GAP-16", "REQUISITION", rid, rid, "", "S0",
                "Integration failure, retry, duplicate or manual re-entry evidenced",
                len(fails), 0, "events", ids(*fails),
                f"{count_of(evs, SYNC_FAILED)} sync failures, {count_of(evs, SYNC_RETRIED)} retries, "
                f"{count_of(evs, MANUAL_REENTRY)} manual re-entries; "
                f"recovery lag {mins_to_bdays(lag) if lag else 'n/a'} business days")

for arow in APP_ROWS:
    aid = arow["application_id"]
    rid = arow["requisition_id"]
    evs = BY_CASE.get(aid, [])
    prio = arow["priority"]

    # ---- GAP-06 hiring manager shortlist review delay
    m, ef, et = interval(evs, SHORTLIST_SUBMIT, SHORTLIST_DECISION, "last", "last")
    tgt = sla_minutes("SLA-07", prio)
    if m is not None and m > tgt:
        add_gap("GAP-06", "APPLICATION", aid, rid, aid, "S5",
                "Shortlist submission-to-decision > SLA-07", mins_to_bdays(m), mins_to_bdays(tgt),
                "business days", ids(ef, et) + ids(*[e for e in evs if e["activity_name"] == SHORTLIST_REMIND]),
                f"hiring manager decision after {mins_to_bdays(m)} business days, "
                f"{count_of(evs, SHORTLIST_REMIND)} reminders")
    else:
        om, oe = open_wait(evs, SHORTLIST_SUBMIT, SHORTLIST_DECISION)
        if om is not None and om > tgt and not any(e["activity_name"] in TERMINAL_APP for e in evs):
            add_gap("GAP-06", "APPLICATION", aid, rid, aid, "S5",
                    "Shortlist still awaiting hiring-manager decision at cut-off > SLA-07",
                    mins_to_bdays(om), mins_to_bdays(tgt), "business days", ids(oe),
                    "no decision event evidenced")

    # ---- GAP-07 screening turnaround / repeated screening
    m, ef, et = interval(evs, APP_RECEIVED, SCREEN_DONE, "first", "first")
    tgt = sla_minutes("SLA-06", prio)
    n_rescreen = count_of(evs, RESCREEN)
    if (m is not None and m > tgt) or n_rescreen >= 1:
        add_gap("GAP-07", "APPLICATION", aid, rid, aid, "S5",
                "Receipt-to-screening-decision > SLA-06 or repeated screening evidenced",
                mins_to_bdays(m) if m is not None else "", mins_to_bdays(tgt), "business days",
                ids(ef, et) + ids(*[e for e in evs if e["activity_name"] in (RESCREEN, SCREEN_REMIND)]),
                f"screening decision after {mins_to_bdays(m) if m is not None else 'open'} business days; "
                f"{n_rescreen} rescreen events")

    # ---- GAP-08 interview scheduling lead time / rescheduling
    tgt = sla_minutes("SLA-08", prio)
    n_res = count_of(evs, INT_RESCHEDULED)
    worst, wf, wt = None, None, None
    for e in [e for e in evs if e["activity_name"] == INT_REQUESTED]:
        conf = next((c for c in evs if c["activity_name"] == INT_CONFIRMED and ts(c) >= ts(e)), None)
        if conf:
            v = bmin_between(ts(e), ts(conf))
            if worst is None or v > worst:
                worst, wf, wt = v, e, conf
    if (worst is not None and worst > tgt) or n_res >= 2:
        ev = [e for e in evs if e["activity_name"] in (INT_RESCHEDULED, INT_SCHED_FOLLOWUP, INT_CANCELLED)]
        add_gap("GAP-08", "APPLICATION", aid, rid, aid, "S6",
                "Request-to-confirmation > SLA-08 or reschedule count >= 2",
                mins_to_bdays(worst) if worst is not None else "", mins_to_bdays(tgt),
                "business days", ids(wf, wt) + ids(*ev),
                f"worst scheduling lead time {mins_to_bdays(worst) if worst else 'n/a'} business days; "
                f"{n_res} reschedules; reasons: "
                f"{', '.join(sorted({e['reason_code'] for e in ev if e['reason_code']})) or 'not recorded'}")

    # ---- GAP-09 late / incomplete interview feedback
    tgt = sla_minutes("SLA-09", prio)
    late = [e for e in evs if e["activity_name"] == FB_SUBMITTED and e["sla_breach_flag"] == "Y"]
    n_fbret = count_of(evs, FB_RETURNED)
    pend_m, pend_e = open_wait(evs, INT_CONDUCTED, FB_SUBMITTED)
    if late or n_fbret >= 1 or (pend_m is not None and pend_m > tgt):
        worst_late = max([int(e["sla_measured_minutes"] or 0) for e in late], default=pend_m or 0)
        add_gap("GAP-09", "APPLICATION", aid, rid, aid, "S6",
                "Feedback submission > SLA-09 or feedback returned for completion",
                mins_to_bdays(worst_late), mins_to_bdays(tgt), "business days",
                ids(*late) + ids(*[e for e in evs if e["activity_name"] in (FB_RETURNED, FB_REMIND)]) + ids(pend_e),
                f"{len(late)} late feedback submissions, {n_fbret} returned for completion, "
                f"{count_of(evs, FB_REMIND)} reminders")

    # ---- GAP-10 assessment delay / expiry / sync lag
    m, ef, et = interval(evs, ASM_ASSIGNED, ASM_DONE, "last", "last")
    tgt = sla_minutes("SLA-10", prio)
    expired = count_of(evs, ASM_EXPIRED)
    asm_sync_fail = [e for e in evs if e["activity_name"] == SYNC_FAILED
                     and any(x["activity_name"] == ASM_SYNCED for x in evs)]
    if (m is not None and m > tgt) or expired or asm_sync_fail:
        add_gap("GAP-10", "APPLICATION", aid, rid, aid, "S6",
                "Assigned-to-complete > SLA-10, expiry, or result sync failure",
                mins_to_bdays(m) if m is not None else "", mins_to_bdays(tgt), "business days",
                ids(ef, et) + ids(*[e for e in evs if e["activity_name"] in (ASM_EXPIRED, ASM_REISSUED, ASM_REMIND)]),
                f"assessment turnaround {mins_to_bdays(m) if m is not None else 'open'} business days; "
                f"{expired} expiries; {len(asm_sync_fail)} result-sync failures")

    # ---- GAP-11 delayed or repeated selection decision
    m, ef, et = interval(evs, FB_SUBMITTED, SEL_DECISION, "last", "last")
    tgt = sla_minutes("SLA-11", prio)
    n_dec = count_of(evs, SEL_DECISION)
    n_reopen = count_of(evs, SEL_REOPENED)
    n_extra = count_of(evs, EXTRA_ROUND)
    pend_m, pend_e = open_wait(evs, FB_SUBMITTED, SEL_DECISION)
    if (m is not None and m > tgt) or n_reopen or n_dec >= 2 or n_extra or \
            (pend_m is not None and pend_m > tgt and not any(e["activity_name"] in TERMINAL_APP for e in evs)):
        add_gap("GAP-11", "APPLICATION", aid, rid, aid, "S7",
                "Last feedback-to-decision > SLA-11, repeated decision, or additional round",
                mins_to_bdays(m if m is not None else (pend_m or 0)), mins_to_bdays(tgt),
                "business days",
                ids(ef, et) + ids(*[e for e in evs if e["activity_name"] in (SEL_REOPENED, EXTRA_ROUND, SEL_REMIND)]) + ids(pend_e),
                f"decision lead time {mins_to_bdays(m if m is not None else (pend_m or 0))} business days; "
                f"{n_dec} decision events, {n_reopen} reopened, {n_extra} additional rounds")

    # ---- GAP-12 offer approval cycle and rework
    m, ef, et = interval(evs, COMP_PROPOSED, OFFER_APPROVED, "first", "last")
    tgt = sla_minutes("SLA-12", prio)
    n_rev = count_of(evs, COMP_REVISED)
    n_ret = count_of(evs, OFFER_RETURNED)
    if (m is not None and m > tgt) or (n_rev + n_ret) >= 1:
        add_gap("GAP-12", "APPLICATION", aid, rid, aid, "S8",
                "Proposal-to-approval > SLA-12 or revision/return events evidenced",
                mins_to_bdays(m) if m is not None else "", mins_to_bdays(tgt), "business days",
                ids(ef, et) + ids(*[e for e in evs if e["activity_name"] in (COMP_REVISED, OFFER_RETURNED, OFFER_APPR_REMIND)]),
                f"approval cycle {mins_to_bdays(m) if m is not None else 'open'} business days; "
                f"{n_ret} returns, {n_rev} proposal revisions; reasons: "
                f"{', '.join(sorted({e['reason_code'] for e in evs if e['activity_name'] == OFFER_RETURNED and e['reason_code']})) or 'not recorded'}")

    # ---- GAP-13 delayed offer release / negotiation loops
    m, ef, et = interval(evs, OFFER_APPROVED, OFFER_SENT, "last", "first")
    tgt = sla_minutes("SLA-13", prio)
    n_neg = count_of(evs, OFFER_NEGOTIATE)
    if (m is not None and m > tgt) or n_neg >= 1:
        add_gap("GAP-13", "APPLICATION", aid, rid, aid, "S8",
                "Approval-to-send > SLA-13 or negotiation/revision events evidenced",
                mins_to_bdays(m) if m is not None else "", mins_to_bdays(tgt), "business days",
                ids(ef, et) + ids(*[e for e in evs if e["activity_name"] in (OFFER_NEGOTIATE, OFFER_REVISED, OFFER_FOLLOWUP)]),
                f"offer released {mins_to_bdays(m) if m is not None else 'n/a'} business days after approval; "
                f"{n_neg} negotiation events, {count_of(evs, OFFER_REVISED)} offer versions")

    # ---- GAP-14 withdrawal after inactivity
    if arow["application_outcome"] == "Withdrawn":
        wev = last_of(evs, CAND_WITHDREW)
        pre = [e for e in evs if ts(e) <= ts(wev)]
        worst = bdays(max_gap_bdays(pre))
        if worst >= bdays(7):
            add_gap("GAP-14", "APPLICATION", aid, rid, aid, wev["stage_code"],
                    "Withdrawal preceded by an inactivity interval >= 7 business days",
                    mins_to_bdays(worst), 7.0, "business days", ids(wev),
                    f"withdrew at {STAGE_NAME[wev['stage_code']]} after a "
                    f"{mins_to_bdays(worst)} business-day wait; reason {wev['reason_code'] or 'not recorded'}")

    # ---- GAP-15 pre-joining verification / documentation delay
    hits = []
    m1, f1, t1 = interval(evs, BGV_INIT, BGV_CLEARED, "last", "last")
    tgt1 = sla_minutes("SLA-15", prio)
    if m1 is not None and m1 > tgt1:
        hits.append(f"verification clearance {mins_to_bdays(m1)} business days")
    m2, f2, t2 = interval(evs, DOC_REQUESTED, DOC_RECEIVED, "last", "last")
    tgt2 = sla_minutes("SLA-16", prio)
    if m2 is not None and m2 > tgt2:
        hits.append(f"document completion {mins_to_bdays(m2)} business days")
    if count_of(evs, BGV_EXCEPTION) and not count_of(evs, BGV_CLEARED):
        hits.append("verification exception unresolved")
    if count_of(evs, DOC_RESUBMIT_REQ) >= 2:
        hits.append(f"{count_of(evs, DOC_RESUBMIT_REQ)} document resubmission requests")
    if hits:
        add_gap("GAP-15", "APPLICATION", aid, rid, aid, "S9",
                "Acceptance-to-clearance/documents > SLA-15/16 or unresolved exception",
                mins_to_bdays(max(m1 or 0, m2 or 0)), mins_to_bdays(max(tgt1, tgt2)),
                "business days",
                ids(f1, t1, f2, t2) + ids(*[e for e in evs if e["activity_name"] in (BGV_EXCEPTION, BGV_INFO_REQ, DOC_RESUBMIT_REQ)]),
                "; ".join(hits))

    # ---- GAP-16 integration failures (application scope)
    fails = [e for e in evs if e["activity_name"] in (SYNC_FAILED, DUP_UPDATE, MANUAL_REENTRY)]
    if fails:
        lag, lf_, lt_ = interval(evs, SYNC_FAILED, SYNC_COMPLETED, "first", "last")
        add_gap("GAP-16", "APPLICATION", aid, rid, aid, "S0",
                "Integration failure, retry, duplicate or manual re-entry evidenced",
                len(fails), 0, "events", ids(*fails),
                f"{count_of(evs, SYNC_FAILED)} sync failures, {count_of(evs, SYNC_RETRIED)} retries, "
                f"{count_of(evs, MANUAL_REENTRY)} manual re-entries; recovery lag "
                f"{mins_to_bdays(lag) if lag else 'n/a'} business days")

    # ---- GAP-18 governance deviation (application scope)
    viol = []
    if count_of(evs, OFFER_SENT) and not count_of(evs, OFFER_APPROVED):
        viol.append("Offer Sent without an evidenced Offer Approved")
    if count_of(evs, CAND_JOINED) and not count_of(evs, BGV_CLEARED):
        viol.append("Candidate Joined without an evidenced Background Verification Cleared")
    if viol and not count_of(evs, POLICY_EXCEPTION):
        add_gap("GAP-18", "APPLICATION", aid, rid, aid, "S8",
                "Mandatory control event missing and no documented exception",
                len(viol), 0, "missing controls",
                ids(first_of(evs, OFFER_SENT), first_of(evs, CAND_JOINED)), "; ".join(viol))


# ------------------------------------------------------------------ reference tables
ACTIVITY_ROWS = []
for name, m in ACT.items():
    codes = SLA_FOR_CLOSING.get(name, [])
    ACTIVITY_ROWS.append(dict(
        activity_name=name, stage_code=m["stage"],
        activity_stage=STAGE_NAME.get(m["stage"], "Cross-stage (System / Governance)"),
        stage_sequence=STAGE_SEQ.get(m["stage"], 0),
        primary_source_system=m["system"], primary_persona=m["persona"],
        manual_touch_by_default="Y" if m["manual"] else "N",
        rework_activity="Y" if m["rework"] else "N",
        exception_activity="Y" if m["exception"] else "N",
        closes_sla_codes="|".join(codes), description=m["desc"]))

SLA_ROWS = [dict(sla_code=c, sla_name=n, from_activity=f, to_activity=t,
                 target_business_days_standard=d,
                 target_minutes_standard=int(d * WORK_MIN),
                 target_minutes_urgent=int(d * WORK_MIN * 0.8),
                 target_minutes_critical=int(d * WORK_MIN * 0.6),
                 stage_code=s, applies_to=sc,
                 basis="Business minutes on the IN-STD-09:00-17:30-MONFRI calendar "
                       "(510 productive minutes per business day)",
                 status="Illustrative prototype threshold - client approval required")
            for c, n, f, t, d, s, sc in SLA_DEFS]

GAP_REF_ROWS = [
    ("GAP-01", "Hiring demand / requisition", "Demand or requisition returned repeatedly for incomplete justification, grade, location or role details", "T2-01, T2-18", "T1-01, T1-02, T1-09, T1-10", "Return/re-submit events >= 2 or missing required fields followed by return", "Demand quality / intake checklist incomplete", "Mandatory intake validation; role template; business-owner checklist"),
    ("GAP-02", "Budget / headcount validation", "Budget or headcount validation remains pending beyond configured SLA", "T2-01, T2-03, T2-07", "T1-01, T1-02, T1-03, T1-04", "Validation pending duration > SLA; evidence of queue timestamps", "Budget owner capacity or unclear approval routing", "Escalation, delegated authority, queue ownership and planning integration"),
    ("GAP-03", "Requisition approval", "Approval queue delay or repeated approval returns", "T2-01, T2-07, T2-18", "T1-01, T1-02, T1-03, T1-09", "Submission-to-action > SLA or repeated return/resubmit", "Approver bottleneck or incomplete approval packet", "Approval routing rules, completeness gates, aging alerts"),
    ("GAP-04", "Recruitment assignment", "Approved requisition waits too long for recruiter assignment", "T2-04, T2-22", "T1-01, T1-03, T1-04, T1-11", "Approval-to-assignment > threshold; active load elevated", "Capacity imbalance or manual allocation", "Load-balanced assignment; prioritization by role criticality"),
    ("GAP-05", "Job description / sourcing readiness", "JD repeatedly revised or sourcing launch delayed after assignment", "T2-02, T2-05, T2-18", "T1-01, T1-03, T1-09, T1-10", "JD revision loop or assignment-to-posting delay above SLA", "Role requirements not aligned at intake", "Standard role profiles; intake calibration meeting"),
    ("GAP-06", "Hiring manager shortlist review", "Shortlist remains in hiring-manager review queue beyond SLA", "T2-03, T2-07, T2-10", "T1-01, T1-03, T1-04, T1-05", "Shortlist submitted, no decision event within SLA", "Competing priorities or unclear decision ownership", "Decision SLA, delegate reviewer, aging dashboard and escalation"),
    ("GAP-07", "Screening", "Screening decision delayed or applications repeatedly reviewed", "T2-02, T2-09, T2-17", "T1-01, T1-03, T1-05, T1-09", "Receipt-to-decision exceeds SLA; repeated screening activity", "High volume, unclear criteria or inconsistent triage", "Structured screening rubric; queue segmentation; assisted triage with human review"),
    ("GAP-08", "Interview scheduling", "Interview scheduling and rescheduling create excessive elapsed time", "T2-03, T2-06, T2-10", "T1-01, T1-03, T1-04, T1-08", "Request-to-confirmation > SLA or reschedule count >= configured threshold", "Panel availability constraints or fragmented coordination", "Panel capacity blocks; self-scheduling; consolidated interview windows"),
    ("GAP-09", "Interview feedback", "Feedback is late, incomplete or returned for clarification", "T2-07, T2-19", "T1-01, T1-03, T1-09, T1-10", "Feedback submission exceeds SLA or required fields missing", "Interviewers delay feedback or forms are unclear", "Structured scorecards, reminders, feedback completeness gate"),
    ("GAP-10", "Assessment", "Assessment completion or result transfer is delayed/expired", "T2-02, T2-11, T2-26", "T1-01, T1-03, T1-05", "Assigned-to-complete exceeds SLA, expiry or sync lag", "Candidate friction, provider delay or integration issue", "Clear due dates, reminders, provider SLA and integration monitoring"),
    ("GAP-11", "Selection decision", "Decision waits after all required interviews/assessments or repeats", "T2-03, T2-07, T2-17", "T1-01, T1-03, T1-04, T1-09", "Last required feedback available but no decision within SLA; repeated decision events", "Decision rights unclear or panel disagreement", "Decision owner, calibration protocol, decision meeting cadence"),
    ("GAP-12", "Offer compensation / approval", "Offer approval has long cycle time, revisions or repeated returns", "T2-14, T2-18, T2-07", "T1-01, T1-03, T1-06, T1-09, T1-10", "Proposal-to-approval > SLA or multiple revision/return events", "Compensation outside band, missing data or fragmented approval chain", "Pre-approved bands, approval matrix, complete proposal validation"),
    ("GAP-13", "Offer communication / negotiation", "Offer is delayed after approval or has repeated negotiation/revision", "T2-12, T2-13", "T1-01, T1-06, T1-08", "Approval-to-send delay or multiple negotiation events", "Expectation mismatch, slow communication or unclear offer positioning", "Expectation alignment before offer; offer-generation automation; timely recruiter follow-up"),
    ("GAP-14", "Candidate withdrawal", "Withdrawals cluster after long inactivity or specific stage delays", "T2-08, T2-10, T2-12, T2-15", "T1-05, T1-06, T1-08", "Withdrawal preceded by inactivity/long wait; compare matched cohorts", "Candidate disengagement associated with process friction; causality not assumed", "Stage-specific candidate communication and delay escalation"),
    ("GAP-15", "Pre-joining / verification", "Verification or document collection delays joining readiness", "T2-15, T2-16, T2-07", "T1-01, T1-03, T1-07, T1-08", "Acceptance-to-clearance/document completion > SLA or exception unresolved", "Late initiation, incomplete documentation or vendor delay", "Start checks earlier where permitted; checklist; exception queue ownership"),
    ("GAP-16", "Manual handoffs / integration", "Cross-system handoff failure, synchronization lag or duplicate/manual update", "T2-23, T2-24, T2-26", "T1-01, T1-03, T1-09, T1-11, T1-12", "Integration error/retry or source-target lag; duplicate events reconciled", "Fragmented systems or weak integration monitoring", "API/event integration, retry policy, reconciliation dashboard"),
    ("GAP-17", "Workload / capacity", "Delay and manual follow-up correlate with high weighted recruiter load", "T2-22, T2-23, T2-24", "T1-01, T1-03, T1-11, T1-12", "High weighted load plus aged queues; compare similar role/case mix", "Capacity imbalance or uneven allocation", "Weighted capacity planning, rebalance portfolio, surge support"),
    ("GAP-18", "Process governance / path deviation", "Mandatory steps skipped, approvals missing or non-standard paths recur", "T2-20, T2-21, T2-18", "T1-09, T1-10; potentially T1-01/T1-03", "Missing mandatory event, invalid transition or policy version mismatch", "Process configuration or governance not consistently embedded", "Workflow guardrails, exception approval, policy-aware audit trail"),
]
GAP_MAP_ROWS = [dict(gap_reference=a, stage_or_domain=b, gap_definition=c, tier2_evidence=d,
                     tier1_impact=e, illustrative_detection_rule=f, rca_hypothesis_not_fact=g,
                     intervention_options=h)
                for a, b, c, d, e, f, g, h in GAP_REF_ROWS]

T1_ROWS = [dict(kpi_id=a, kpi=b, definition=c, primary_data=d, direction=e, tier2_drivers=f)
           for a, b, c, d, e, f in [
    ("T1-01", "End-to-end time to fill", "Median business days from approved requisition to candidate joining; also report mean and P90", "Requisition log + joined application", "Lower is better", "T2-01, T2-02, T2-03, T2-04, T2-05, T2-06"),
    ("T1-02", "Requisition approval lead time", "Median business time from requisition submission to final approval", "Requisition log", "Lower is better", "T2-01, T2-02, T2-07"),
    ("T1-03", "Recruitment stage SLA attainment", "Completed in-scope stage transitions within applicable SLA / all eligible completed transitions", "Both logs", "Higher is better", "T2-02, T2-03, T2-04, T2-05, T2-06, T2-07"),
    ("T1-04", "Waiting-time share", "Measured waiting time / measured total elapsed time for eligible cases", "Both logs", "Lower is generally better", "T2-03, T2-04, T2-05, T2-06, T2-07"),
    ("T1-05", "Candidate conversion rate", "Candidates reaching defined milestone / eligible candidates entering prior milestone, by cohort", "Application log", "Higher may be better, interpreted by stage and quality", "T2-08, T2-09, T2-10, T2-11"),
    ("T1-06", "Offer acceptance rate", "Offers accepted / offers with a final response, with expired/pending separated", "Application log", "Higher is generally better", "T2-12, T2-13, T2-14"),
    ("T1-07", "Offer-to-join conversion", "Candidates joined / accepted offers eligible to have reached joining date", "Application log + HRIS", "Higher is better", "T2-14, T2-15, T2-16"),
    ("T1-08", "Candidate withdrawal rate", "Withdrawn applications / eligible applications, segmented by stage and cohort", "Application log", "Lower is generally better; context required", "T2-08, T2-10, T2-13, T2-14, T2-15"),
    ("T1-09", "Recruitment rework rate", "Eligible cases with one or more defined rework loops / eligible cases", "Both logs", "Lower is better", "T2-17, T2-18, T2-19"),
    ("T1-10", "Process conformance rate", "Eligible cases following required policy path and controls / eligible cases", "Both logs", "Higher is better", "T2-18, T2-19, T2-20, T2-21"),
    ("T1-11", "Recruiter workload / capacity pressure", "Weighted active requisition load per recruiter, adjusted for role complexity and age", "Requisition log + ATS snapshots", "Balanced, not simply minimized", "T2-22, T2-23, T2-24"),
    ("T1-12", "Recruitment effort / cost per hire", "Measured recruitment effort/cost for filled requisitions divided by hires, with data coverage disclosed", "ATS + approved cost/effort data", "Lower is better without harming quality", "T2-22, T2-23, T2-24, T2-25, T2-26"),
]]

T2_ROWS = [dict(kpi_id=a, diagnostic_kpi=b, definition=c, feeds_tier1=d)
           for a, b, c, d in [
    ("T2-01", "Approval queue time", "Time from requisition submitted to first approver action; separate total approval time", "T1-01, T1-02"),
    ("T2-02", "Stage elapsed time by stage", "Median/P90 elapsed time from stage entry to stage exit", "T1-01, T1-03"),
    ("T2-03", "Waiting-time minutes by handoff", "Elapsed time between outgoing event and next responsible actor's first action", "T1-01, T1-03, T1-04"),
    ("T2-04", "Time to recruiter assignment", "Approval timestamp to recruiter assignment", "T1-01, T1-03, T1-04"),
    ("T2-05", "Time to first qualified shortlist", "Sourcing start to first candidate meeting defined shortlist criteria", "T1-01, T1-03, T1-04"),
    ("T2-06", "Interview scheduling lead time", "Interview request to confirmed interview; report rescheduling separately", "T1-01, T1-03, T1-04"),
    ("T2-07", "SLA breach frequency and severity", "Count/rate of breaches and minutes beyond SLA by stage and owner group", "T1-02, T1-03"),
    ("T2-08", "Stage-to-stage candidate conversion", "Candidates progressing to next stage / eligible candidates at prior stage", "T1-05, T1-08"),
    ("T2-09", "Screening turnaround time", "Application received to screening decision", "T1-05, T1-08"),
    ("T2-10", "Candidate inactivity duration", "Time since last candidate-facing or internal progression event, by stage", "T1-05, T1-08"),
    ("T2-11", "Assessment completion and expiry rate", "Completed / assigned; expired / assigned, cohort-adjusted", "T1-05"),
    ("T2-12", "Offer decision turnaround", "Offer sent to accepted/declined/expired response; pending reported separately", "T1-06"),
    ("T2-13", "Offer revision / negotiation rate", "Offers with one or more revisions or negotiation events / offers sent", "T1-06, T1-08"),
    ("T2-14", "Offer approval cycle and rework", "Proposal created to final approval; number of returns/revisions", "T1-06, T1-07, T1-08"),
    ("T2-15", "Pre-joining completion time", "Offer acceptance to completion of required pre-joining steps", "T1-07, T1-08"),
    ("T2-16", "Accepted-offer no-show / non-joining rate", "Accepted offers not resulting in joining among eligible mature cohort", "T1-07"),
    ("T2-17", "Rework loop count", "Count of transitions back to a previously completed activity/stage per case", "T1-09"),
    ("T2-18", "Repeated approval / incomplete submission rate", "Cases returned or resubmitted due to missing or incorrect information", "T1-09, T1-10"),
    ("T2-19", "Interview feedback lateness / incompleteness", "Feedback submitted after policy SLA or returned for missing fields", "T1-09, T1-10"),
    ("T2-20", "Mandatory control completion rate", "Eligible cases with all required approvals/checks evidenced", "T1-10"),
    ("T2-21", "Process path deviation rate", "Cases with non-standard path, skipped mandatory step or unauthorized transition", "T1-10"),
    ("T2-22", "Active requisitions per recruiter", "Open requisition count and weighted load by priority/complexity/age", "T1-11, T1-12"),
    ("T2-23", "Manual touch count per case", "Count of events with evidenced manual_touch_flag=true", "T1-11, T1-12"),
    ("T2-24", "Follow-up / coordination event rate", "Manual reminders, chase events or scheduling coordination per case where captured", "T1-11, T1-12"),
    ("T2-25", "Measured recruitment effort per hire", "Recorded recruiter/coordination hours and approved agency/assessment costs per hire", "T1-12"),
    ("T2-26", "Integration failure / synchronization lag", "Failed handoffs, retry count and source-to-target event delay", "T1-12; may also explain T1-01/T1-03"),
]]

DD_ROWS = [dict(table_name=a, field=b, type=c, definition=d) for a, b, c, d in [
    ("ta_event_log", "event_id", "String", "Unique event record ID generated during event-log construction"),
    ("ta_event_log", "process_instance_id", "String", "Case ID: requisition_id for the requisition log, application_id for the application log"),
    ("ta_event_log", "case_type", "Enum", "REQUISITION or APPLICATION"),
    ("ta_event_log", "requisition_id", "String", "Stable link to the hiring requirement; present on every row (primary lifecycle perspective)"),
    ("ta_event_log", "application_id", "String / nullable", "Candidate-application key; empty for requisition-only events"),
    ("ta_event_log", "candidate_surrogate_id", "String / nullable", "Pseudonymous candidate reference; no direct identifiers are generated"),
    ("ta_event_log", "activity_name", "String", "Canonical activity name from ta_activity_master"),
    ("ta_event_log", "activity_stage", "Enum", "One of the nine recruitment stages, or the cross-stage system/governance bucket"),
    ("ta_event_log", "stage_code", "Enum", "S1..S9, or S0 for cross-stage system/governance events"),
    ("ta_event_log", "event_timestamp", "Datetime", "Canonical event time (documented canonical timezone; business calendar IN-STD-09:00-17:30-MONFRI)"),
    ("ta_event_log", "event_end_timestamp", "Datetime / nullable", "End time only where a reliable duration exists; never inferred from the next event"),
    ("ta_event_log", "actor_id_pseudonymous", "String / nullable", "Pseudonymous actor identifier"),
    ("ta_event_log", "actor_persona", "Enum", "Responsible stakeholder group for the event"),
    ("ta_event_log", "source_system", "String", "One authoritative source system per activity"),
    ("ta_event_log", "source_event_id", "String", "Source-system audit/event identifier"),
    ("ta_event_log", "status_before", "String", "Case status before the event"),
    ("ta_event_log", "status_after", "String", "Case status after the event"),
    ("ta_event_log", "outcome_code", "String / nullable", "Approved, rejected, selected, withdrawn, declined, joined, cancelled, etc."),
    ("ta_event_log", "reason_code", "String / nullable", "Standardized reason for rejection, withdrawal, cancellation or rework"),
    ("ta_event_log", "duration_minutes", "Number / nullable", "Observed activity duration; populated only where a reliable start/end exists"),
    ("ta_event_log", "waiting_time_minutes", "Number / nullable", "Business minutes since the previous event in the same case (milestone-to-milestone elapsed time, not active-time subtraction)"),
    ("ta_event_log", "waiting_reason_code", "String / nullable", "Recorded waiting reason; intentionally empty when the source does not record one"),
    ("ta_event_log", "sla_code", "String / nullable", "SLA definition closed by this event (see ta_sla_definitions)"),
    ("ta_event_log", "sla_target_minutes", "Number / nullable", "Applicable SLA target for this interval at this case priority"),
    ("ta_event_log", "sla_measured_minutes", "Number / nullable", "Measured business minutes for the SLA interval"),
    ("ta_event_log", "sla_breach_flag", "Boolean", "Y when the measured duration exceeds the applicable SLA"),
    ("ta_event_log", "sla_overrun_minutes", "Number / nullable", "Minutes beyond the SLA target"),
    ("ta_event_log", "rework_flag", "Boolean", "Y when the activity is a repeat, return or corrective execution"),
    ("ta_event_log", "repeat_sequence_number", "Number", "Nth occurrence of this activity within the case"),
    ("ta_event_log", "manual_touch_flag", "Boolean", "Y when a human manual action is evidenced; not assumed for every human-owned event"),
    ("ta_event_log", "handoff_flag", "Boolean", "Y when responsibility moves between personas"),
    ("ta_event_log", "exception_flag", "Boolean", "Y when the event represents an exception, failure or deviation"),
    ("ta_event_log", "gap_reference", "String / nullable", "Intentionally EMPTY in this dataset - gaps are derived by deterministic rules into ta_gap_detection.csv"),
    ("ta_event_log", "data_quality_flag", "Boolean", "Y when the event fails a defined quality check (e.g. duplicate payload)"),
    ("ta_event_log", "extraction_batch_id", "String", "Lineage to the source extraction batch"),
    ("ta_requisition_master", "requisition_outcome", "Enum", "Filled, Cancelled, Closed Without Hire, Rejected at Approval, On Hold, Open / In Progress"),
    ("ta_requisition_master", "is_open_censored", "Boolean", "Y when the case is right-censored at the reporting cut-off and must not be treated as completed"),
    ("ta_requisition_master", "recruiter_weighted_load_at_assignment", "Number", "Recruiter's weighted open-requisition load at the moment of assignment (priority x complexity)"),
    ("ta_requisition_master", "time_to_fill_business_days", "Number / nullable", "Approved requisition to first joining, in business days (T1-01 numerator)"),
    ("ta_application_master", "application_outcome", "Enum", "Joined, Accepted - Did Not Join, Offer Declined, Offer Expired, Withdrawn, Rejected - <stage>, No Show, Closed - Requisition Cancelled, In Progress"),
    ("ta_application_master", "candidate_engagement_profile", "Enum", "resilient / normal / impatient - synthetic design attribute that keeps counterexamples in the data"),
    ("ta_application_master", "max_inactivity_business_days", "Number", "Longest gap between consecutive events in the case"),
    ("ta_stage_transitions", "elapsed_business_minutes", "Number", "Stage entry to next-stage entry, on the business calendar (T2-02)"),
    ("ta_recruiter_load_snapshot", "weighted_load", "Number", "Sum of priority x complexity weights of requisitions open for that recruiter on the snapshot date (T2-22)"),
    ("ta_gap_detection", "detection_rule", "String", "The deterministic rule that fired, expressed against event evidence and the SLA policy"),
    ("ta_gap_detection", "evidence_event_ids", "String", "Pipe-separated event_id values that support the finding"),
    ("ta_scenario_ground_truth", "gt_*", "Mixed", "Generator intent. For validating the detector only - never an analytical input"),
]]


# ------------------------------------------------------------------ output
EVENT_COLS = ["event_id", "process_instance_id", "case_type", "requisition_id", "application_id",
              "candidate_surrogate_id", "activity_name", "activity_stage", "stage_code",
              "stage_sequence", "event_timestamp", "event_end_timestamp",
              "actor_id_pseudonymous", "actor_persona", "source_system", "source_event_id",
              "status_before", "status_after", "outcome_code", "reason_code",
              "duration_minutes", "waiting_time_minutes", "waiting_reason_code",
              "sla_code", "sla_target_minutes", "sla_measured_minutes", "sla_breach_flag",
              "sla_overrun_minutes", "rework_flag", "repeat_sequence_number",
              "manual_touch_flag", "handoff_flag", "exception_flag", "gap_reference",
              "business_unit", "job_family", "location", "job_level", "priority",
              "recruiter_id_pseudonymous", "hiring_manager_id_pseudonymous",
              "source_channel", "data_quality_flag", "extraction_batch_id"]


def write_csv(name, rows, cols=None):
    os.makedirs(OUT, exist_ok=True)
    path = os.path.join(OUT, name)
    if not rows:
        print(f"  !! {name}: no rows")
        return
    cols = cols or list(rows[0].keys())
    with open(path, "w", newline="", encoding="utf-8") as f:
        wr = csv.DictWriter(f, fieldnames=cols, extrasaction="ignore")
        wr.writeheader()
        wr.writerows(rows)
    size = os.path.getsize(path) / 1_048_576
    print(f"  {name:<34} {len(rows):>8} rows  {size:6.1f} MB")


GT_COLS = sorted({k for r in GROUND_TRUTH for k in r})
GT_COLS = ["requisition_id", "intent"] + [c for c in GT_COLS if c not in ("requisition_id", "intent")]

print("\nwriting CSV extract ...")
write_csv("ta_event_log.csv", KEPT, EVENT_COLS)
write_csv("ta_requisition_master.csv", REQ_ROWS)
write_csv("ta_application_master.csv", APP_ROWS)
write_csv("ta_stage_transitions.csv", STAGE_ROWS)
write_csv("ta_recruiter_load_snapshot.csv", LOAD_ROWS)
write_csv("ta_activity_master.csv", ACTIVITY_ROWS)
write_csv("ta_sla_definitions.csv", SLA_ROWS)
write_csv("ta_gap_mapping.csv", GAP_MAP_ROWS)
write_csv("ta_kpi_tier1.csv", T1_ROWS)
write_csv("ta_kpi_tier2.csv", T2_ROWS)
write_csv("ta_gap_detection.csv", GAP_ROWS)
write_csv("ta_scenario_ground_truth.csv", GROUND_TRUTH, GT_COLS)
write_csv("ta_data_dictionary.csv", DD_ROWS)

# ------------------------------------------------------------------ summary
def pct(a, b):
    return f"{(100.0 * a / b):.1f}%" if b else "n/a"


print("\n" + "=" * 78)
print(f"SYNTHETIC TALENT ACQUISITION EVENT LOG  |  seed {SEED}  |  {GEN_VERSION}")
print("=" * 78)
print(f"requisition cases          : {len(REQ_ROWS)}")
print(f"application cases          : {len(APP_ROWS)}")
print(f"events published           : {len(KEPT)}  (dropped after cut-off: {DROPPED})")
print(f"  requisition-level events : {sum(1 for e in KEPT if e['case_type'] == 'REQUISITION')}")
print(f"  application-level events : {sum(1 for e in KEPT if e['case_type'] == 'APPLICATION')}")
print(f"distinct activities        : {len({e['activity_name'] for e in KEPT})}")
print(f"event window               : {KEPT[0]['event_timestamp'][:10]} .. {CUTOFF:%Y-%m-%d} (cut-off)")

ro = Counter(r["requisition_outcome"] for r in REQ_ROWS)
print("\nrequisition outcomes")
for k, v in ro.most_common():
    print(f"  {k:<26} {v:>5}  {pct(v, len(REQ_ROWS))}")
ao = Counter(r["application_outcome"] for r in APP_ROWS)
print("\napplication outcomes")
for k, v in ao.most_common():
    print(f"  {k:<32} {v:>6}  {pct(v, len(APP_ROWS))}")

print("\napplication funnel (share of all applications reaching each milestone)")
n_app = len(APP_ROWS)
for label, key in [("screened", "screening_decision_timestamp"),
                   ("shortlisted to hiring manager", "shortlist_submitted_timestamp"),
                   ("interviewed (>=1 round)", "first_interview_timestamp"),
                   ("final selection decision", "selection_decision_timestamp"),
                   ("offer sent", "offer_sent_timestamp"),
                   ("offer accepted", "bgv_cleared_timestamp"),
                   ("joined", "join_timestamp")]:
    k = sum(1 for r in APP_ROWS if r[key])
    print(f"  {label:<32} {k:>6}  {pct(k, n_app)}")

filled = [r for r in REQ_ROWS if r["requisition_outcome"] == "Filled" and r["time_to_fill_business_days"] != ""]
ttf = sorted(r["time_to_fill_business_days"] for r in filled)
if ttf:
    print(f"\nT1-01 time to fill (business days, approved -> joined, n={len(ttf)})")
    print(f"  median {stx.median(ttf):.1f}   mean {stx.mean(ttf):.1f}   "
          f"P90 {ttf[int(0.9 * (len(ttf) - 1))]:.1f}   max {ttf[-1]:.1f}")
appr = [r["days_to_approval"] for r in REQ_ROWS if r["days_to_approval"] != ""]
print(f"T1-02 approval lead time   : median {stx.median(appr):.1f} business days (n={len(appr)})")
elig = sum(r["sla_eligible_count"] for r in REQ_ROWS) + sum(r["sla_eligible_count"] for r in APP_ROWS)
brch = sum(r["sla_breach_count"] for r in REQ_ROWS) + sum(r["sla_breach_count"] for r in APP_ROWS)
print(f"T1-03 SLA attainment       : {pct(elig - brch, elig)} ({brch} breaches of {elig} measured intervals)")
shares = [min(1.0, r["recorded_wait_business_days"] * WORK_MIN / r["total_elapsed_business_minutes"])
          for r in REQ_ROWS + APP_ROWS
          if r["total_elapsed_business_minutes"] > 0 and r["is_open_censored"] == "N"
          and r["recorded_wait_business_days"] > 0]
cov = sum(1 for r in REQ_ROWS + APP_ROWS if r["recorded_wait_business_days"] > 0)
print(f"T1-04 waiting-time share   : median {100 * stx.median(shares):.1f}% of case elapsed time sits in "
      f"intervals with a recorded waiting reason")
print(f"      (measured on the {cov} cases that carry at least one recorded waiting reason, "
      f"{pct(cov, len(REQ_ROWS) + len(APP_ROWS))} of all cases)")
print(f"      active-time coverage : {pct(sum(1 for e in KEPT if e['duration_minutes']), len(KEPT))} of events "
      f"carry a reliable duration, so active/wait split is reported, not inferred")
resp = [r for r in APP_ROWS if r["offer_acceptance_flag"] in ("Y", "N")]
print(f"T1-06 offer acceptance rate: {pct(sum(1 for r in resp if r['offer_acceptance_flag'] == 'Y'), len(resp))} "
      f"(offers with a final response n={len(resp)})")
acc = [r for r in APP_ROWS if r["offer_acceptance_flag"] == "Y"]
print(f"T1-07 offer-to-join        : {pct(sum(1 for r in acc if r['joining_flag'] == 'Y'), len(acc))}")
print(f"T1-08 withdrawal rate      : {pct(ao['Withdrawn'], len(APP_ROWS))}")
rw_req = sum(1 for r in REQ_ROWS if r["rework_event_count"] > 0)
rw_app = sum(1 for r in APP_ROWS if r["rework_event_count"] > 0)
lp_req = sum(1 for r in REQ_ROWS if r["rework_loop_count"] > 0)
lp_app = sum(1 for r in APP_ROWS if r["rework_loop_count"] > 0)
print(f"T1-09 rework rate          : by rework EVENT  requisitions {pct(rw_req, len(REQ_ROWS))}, "
      f"applications {pct(rw_app, len(APP_ROWS))}")
print(f"                             by rework LOOP   requisitions {pct(lp_req, len(REQ_ROWS))}, "
      f"applications {pct(lp_app, len(APP_ROWS))}")
dev = {g["process_instance_id"] for g in GAP_ROWS if g["gap_reference"] == "GAP-18"}
print(f"T1-10 conformance rate     : {pct(len(REQ_ROWS) + len(APP_ROWS) - len(dev), len(REQ_ROWS) + len(APP_ROWS))}")
lw = [r["weighted_load"] for r in LOAD_ROWS]
print(f"T1-11 recruiter load       : median weighted {stx.median(lw):.1f}, P80 {LOAD_P80:.1f}, max {max(lw):.1f}")
print(f"T2-23 manual touches       : {pct(sum(1 for e in KEPT if e['manual_touch_flag'] == 'Y'), len(KEPT))} of events")

print("\ngap detections derived from the event log (ta_gap_detection.csv)")
GAP_ELIGIBLE = {
    "GAP-01": ("REQ", None), "GAP-02": ("REQ", BUDGET_REQ), "GAP-03": ("REQ", REQ_SUBMIT),
    "GAP-04": ("REQ", REQ_APPROVED), "GAP-05": ("REQ", RECRUITER_ASSIGNED),
    "GAP-17": ("REQ", RECRUITER_ASSIGNED),
    "GAP-06": ("APP", SHORTLIST_SUBMIT), "GAP-07": ("APP", None),
    "GAP-08": ("APP", INT_REQUESTED), "GAP-09": ("APP", INT_CONDUCTED),
    "GAP-10": ("APP", ASM_ASSIGNED), "GAP-11": ("APP", FB_SUBMITTED),
    "GAP-12": ("APP", COMP_PROPOSED), "GAP-13": ("APP", OFFER_SENT),
    "GAP-14": ("APP", CAND_WITHDREW), "GAP-15": ("APP", OFFER_ACCEPTED),
    "GAP-16": ("BOTH", None), "GAP-18": ("BOTH", None),
}


def eligible_cases(scope, opening):
    pids = []
    if scope in ("REQ", "BOTH"):
        pids += [r["requisition_id"] for r in REQ_ROWS]
    if scope in ("APP", "BOTH"):
        pids += [r["application_id"] for r in APP_ROWS]
    if opening is None:
        return len(pids)
    return sum(1 for p in pids if any(e["activity_name"] == opening for e in BY_CASE[p]))


gc = Counter(g["gap_reference"] for g in GAP_ROWS)
gcases = defaultdict(set)
for g in GAP_ROWS:
    gcases[g["gap_reference"]].add(g["process_instance_id"])
print(f"  {'gap':<8}{'findings':>9}{'cases':>8}{'eligible':>10}  rate of the population that reached the stage")
for gid in [f"GAP-{i:02d}" for i in range(1, 19)]:
    scope, opening = GAP_ELIGIBLE[gid]
    base = eligible_cases(scope, opening)
    print(f"  {gid:<8}{gc[gid]:>9}{len(gcases[gid]):>8}{base:>10}  {pct(len(gcases[gid]), base)}")
print(f"  requisitions with >=1 linked finding (own or pipeline): "
      f"{pct(len({g['requisition_id'] for g in GAP_ROWS}), len(REQ_ROWS))}")

gt_clean = {r["requisition_id"] for r in GROUND_TRUTH if r.get("gt_clean_run") is True}
req_scope_gaps = {g["requisition_id"] for g in GAP_ROWS if g["case_type"] == "REQUISITION"
                  and g["gap_reference"] != "GAP-16"}
clean_rows = [r for r in REQ_ROWS if r["requisition_id"] in gt_clean]
other_rows = [r for r in REQ_ROWS if r["requisition_id"] not in gt_clean]
print("\nhealthy benchmark cohort vs the rest (the contrast Kinetic explains)")
print(f"  benchmark cohort size                : {len(clean_rows)} requisitions "
      f"({pct(len(clean_rows), len(REQ_ROWS))})")
for label, rows in (("benchmark", clean_rows), ("all others", other_rows)):
    ttfs = [r["time_to_fill_business_days"] for r in rows if r["time_to_fill_business_days"] != ""]
    appr2 = [r["days_to_approval"] for r in rows if r["days_to_approval"] != ""]
    gapless = sum(1 for r in rows if r["requisition_id"] not in req_scope_gaps)
    print(f"  {label:<10} n={len(rows):<5} median time to fill "
          f"{(stx.median(ttfs) if ttfs else float('nan')):6.1f} bd | median approval "
          f"{(stx.median(appr2) if appr2 else float('nan')):5.1f} bd | rework loops/case "
          f"{stx.mean([r['rework_loop_count'] for r in rows]):4.2f} | "
          f"no requisition-stage finding {pct(gapless, len(rows))}")
print(f"  requisitions with no requisition-stage gap finding at all : "
      f"{pct(len(REQ_ROWS) - len(req_scope_gaps), len(REQ_ROWS))}")

print("\ncontrast check - same job family and location, different execution")
seg = defaultdict(list)
for r in REQ_ROWS:
    if r["time_to_fill_business_days"] != "":
        seg[(r["job_family"], r["location"], r["job_level"])].append(r)
rich = sorted((v for v in seg.values() if len(v) >= 6),
              key=lambda v: -(max(x["time_to_fill_business_days"] for x in v)
                              - min(x["time_to_fill_business_days"] for x in v)))[:3]
for v in rich:
    k = v[0]
    lo = min(x["time_to_fill_business_days"] for x in v)
    hi = max(x["time_to_fill_business_days"] for x in v)
    print(f"  {k['job_family']:<20} {k['location']:<16} {k['job_level']}  n={len(v):<3} "
          f"time to fill {lo:.1f} .. {hi:.1f} business days")

print("\ncounterexample check (prevents 'delay always causes withdrawal' conclusions)")
long_wait = [r for r in APP_ROWS if r["max_inactivity_business_days"] >= 7]
print(f"  applications with a >=7 business-day inactivity gap : {len(long_wait)}")
print(f"    of those, withdrawn : {pct(sum(1 for r in long_wait if r['application_outcome'] == 'Withdrawn'), len(long_wait))}")
print(f"    of those, joined    : {pct(sum(1 for r in long_wait if r['application_outcome'] == 'Joined'), len(long_wait))}")
wd = [r for r in APP_ROWS if r["application_outcome"] == "Withdrawn"]
print(f"  withdrawals with no >=7-day wait beforehand        : "
      f"{pct(sum(1 for r in wd if r['max_inactivity_business_days'] < 7), len(wd))}")

print("\nvariant check")
print(f"  distinct requisition activity sequences : "
      f"{len({tuple(e['activity_name'] for e in BY_CASE[r['requisition_id']]) for r in REQ_ROWS})}")
print(f"  distinct application activity sequences : "
      f"{len({tuple(e['activity_name'] for e in BY_CASE[r['application_id']]) for r in APP_ROWS})}")
print(f"  waiting_reason_code recorded on        : "
      f"{pct(sum(1 for e in KEPT if e['waiting_reason_code']), len(KEPT))} of events "
      f"(remainder deliberately left unknown)")
print("=" * 78)
