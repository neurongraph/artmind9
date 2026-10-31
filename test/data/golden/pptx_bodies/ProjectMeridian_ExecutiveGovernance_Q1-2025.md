PROJECT MERIDIAN

Enterprise Data Platform Transformation

Executive Governance Review  ·  Q1 2025

Programme Sponsor:  Sarah Whitfield, Chief Data Officer

Delivery Partner:  Apex Consulting

Client:  NovaTech Solutions

Review Date:  24 January 2025

STRICTLY CONFIDENTIAL

NovaTech Solutions &amp; Apex Consulting  |  © 2025 Apex Consulting

EXECUTIVE SUMMARY  ·  PROGRAMME HEALTH DASHBOARD

● OVERALL: AT RISK

62%

£4.1M

14

11 / 18

Programme Progress

Budget Utilised

Open Issues

Milestones Achieved

vs 68% planned

of £7.2M total

3 critical

this quarter

WORKSTREAM STATUS AT A GLANCE

| Workstream                 | Lead          | RAG       | Progress   | Key Concern                    |
|----------------------------|---------------|-----------|------------|--------------------------------|
| Data Ingestion & Pipelines | A. Thornton   | ON TRACK  | 78%        | On schedule; UAT in Feb        |
| Cloud Infrastructure (AWS) | D. Okafor     | AT RISK   | 55%        | Env. provisioning delayed 2wks |
| Data Governance & Quality  | P. Navarro    | AT RISK   | 48%        | Policy sign-off pending        |
| Analytics & Reporting      | L. Yamamoto   | ON TRACK  | 71%        | Phase 1 dashboards delivered   |
| Security & Compliance      | R. Mbeki      | OFF TRACK | 32%        | IAM framework gaps — critical  |
| Change & Training          | C. Harrington | AT RISK   | 40%        | Stakeholder engagement low     |

NovaTech Solutions &amp; Apex Consulting  ·  Strictly Confidential  ·  Q1 2025  ·  Slide 2

PROGRAMME MILESTONES  ·  Q4 2024 – Q3 2025

Oct '24

Dec '24

Feb '25

Jun '25

Kickoff

&amp; Discovery

Data Model

Approval

UAT

Start

Go-Live

Phase 1

Infra

Blueprint

Dev Sprint

Complete

Security

Signoff

Nov '24

Jan '25

Mar '25

Completed

In Progress

Planned

KEY MILESTONE DETAIL

| Milestone                             | Due Date    | Owner       | Status      | Comment                                        |
|---------------------------------------|-------------|-------------|-------------|------------------------------------------------|
| Dev Sprint Complete (Phase 1 modules) | 31 Jan 2025 | A. Thornton | IN PROGRESS | 3 of 5 modules complete; 2 carry into Feb      |
| Security & IAM Framework Sign-off     | 28 Feb 2025 | R. Mbeki    | OFF TRACK   | IAM gaps identified; revised plan due 10 Feb   |
| UAT Commencement — Analytics Platform | 14 Feb 2025 | L. Yamamoto | ON TRACK    | Test scripts ready; business testers confirmed |

NovaTech Solutions &amp; Apex Consulting  ·  Strictly Confidential  ·  Q1 2025  ·  Slide 3

BUDGET &amp; FINANCIAL TRACKING

£7.2M

£4.1M

£7.6M

£0.38M

Total Budget

Spent to Date

Forecast at Completion

Contingency Remaining

Approved

57% consumed

+£0.4M variance

of £0.6M available

SPEND BY WORKSTREAM (£'000)

MONTHLY SPEND TREND vs PLAN (£'000)

<!-- image -->

Bar chart

|                 |   Budget |   Actual / Forecast |
|-----------------|----------|---------------------|
| Data Pipelines  |     1800 |                1420 |
| Cloud Infra     |     2100 |                1310 |
| Data Governance |      900 |                 390 |
| Analytics       |     1200 |                 870 |
| Security        |      650 |                 250 |
| Change Mgmt     |      550 |                 180 |

<!-- image -->

Line chart

|     |   Planned Cumulative |   Actual Cumulative |
|-----|----------------------|---------------------|
| Oct |                  580 |                 460 |
| Nov |                 1250 |                1100 |
| Dec |                 2100 |                1890 |
| Jan |                 3050 |                2750 |
| Feb |                 4100 |                3600 |
| Mar |                 5200 |                4800 |

NovaTech Solutions &amp; Apex Consulting  ·  Strictly Confidential  ·  Q1 2025  ·  Slide 4

RISK REGISTER  ·  TOP PROGRAMME RISKS

2  Critical (5×5)

5  High (4+ score)

7  Medium

4  Low

| ID   | Risk Description                                                        | Category       | Owner         | Likelihood   | Impact       |   Rating | Mitigation / Action                                                    |
|------|-------------------------------------------------------------------------|----------------|---------------|--------------|--------------|----------|------------------------------------------------------------------------|
| R01  | IAM framework gaps may block production deployment of secure data zones | Security       | R. Mbeki      | High (4)     | Critical (5) |       20 | Remediation plan in review; interim controls in place; deadline 14 Feb |
| R02  | Key AWS environment provisioning slippage threatens Feb UAT window      | Infrastructure | D. Okafor     | High (4)     | High (4)     |       16 | Escalated to AWS account team; workaround environment commissioned     |
| R03  | Data Governance policy sign-off delayed due to stakeholder availability | Governance     | P. Navarro    | Medium (3)   | High (4)     |       12 | Governance Committee slot booked 5 Feb; pre-read issued                |
| R04  | Change fatigue: business engagement below target for training uptake    | People         | C. Harrington | Medium (3)   | Medium (3)   |        9 | Targeted engagement campaign; senior sponsor communications planned    |
| R05  | Third-party data quality for legacy migration worse than estimated      | Data           | A. Thornton   | Low (2)      | High (4)     |        8 | Automated profiling in place; cleansing sprint added to backlog        |
| R06  | Programme resource attrition — 2 senior engineers at risk of leaving    | Resourcing     | Programme Mgr | Low (2)      | High (4)     |        8 | HR retention review initiated; knowledge transfer in progress          |
| R07  | Regulatory change (DORA) may require rework to resilience architecture  | Regulatory     | R. Mbeki      | Low (2)      | Medium (3)   |        6 | Monitoring DORA guidance; architecture review on standby               |

NovaTech Solutions &amp; Apex Consulting  ·  Strictly Confidential  ·  Q1 2025  ·  Slide 5

ISSUES LOG  ·  OPEN ISSUES REQUIRING ATTENTION

14  Total Open

3  Critical

5  High

9  Resolved this month

| ID   | Issue Description                                                                        | Workstream        | Raised By     | Priority   | Due Date   | Resolution / Action                                       |
|------|------------------------------------------------------------------------------------------|-------------------|---------------|------------|------------|-----------------------------------------------------------|
| I01  | Production-grade encryption keys not yet provisioned for data-at-rest in S3 zones        | Security          | R. Mbeki      | Critical   | 10 Feb 25  | AWS KMS setup in progress; security team review 7 Feb     |
| I02  | Legacy ETL jobs failing intermittently due to schema mismatch in source system           | Data Pipelines    | A. Thornton   | Critical   | 31 Jan 25  | Hotfix deployed; permanent fix targeted EOW               |
| I03  | Business sign-off on data dictionary blocked pending Legal review of PII classifications | Governance        | P. Navarro    | Critical   | 14 Feb 25  | Legal review scheduled 6 Feb; owner escalating            |
| I04  | Test environment refresh SLA breached — 48hr delay to UAT readiness                      | Cloud Infra       | D. Okafor     | High       | 7 Feb 25   | Infrastructure automation script being corrected          |
| I05  | Power BI licensing shortfall — 15 analyst seats not yet provisioned                      | Analytics         | L. Yamamoto   | High       | 14 Feb 25  | Procurement request raised; approval pending CFO sign-off |
| I06  | Training content for Data Stewards not reviewed by SMEs — 2wk overdue                    | Change & Training | C. Harrington | High       | 28 Jan 25  | SME review session booked 30 Jan; content frozen after    |
| I07  | API rate limiting on source system causing pipeline throughput degradation               | Data Pipelines    | A. Thornton   | High       | 14 Feb 25  | Throttling config under review with vendor                |
| I08  | DPIA not yet initiated for new customer analytics module                                 | Governance        | P. Navarro    | Medium     | 28 Feb 25  | DPO engaged; DPIA workshop scheduled 12 Feb               |

NovaTech Solutions &amp; Apex Consulting  ·  Strictly Confidential  ·  Q1 2025  ·  Slide 6

DECISIONS REQUIRED FROM EXECUTIVE SPONSORS

D01

Approve Revised Security Remediation Timeline

Owner: CDO / CISO  ·  Decision needed by: 31 Jan 2025

The IAM framework gaps (Risk R01) require an additional 3-week remediation sprint. This pushes the Security Sign-off milestone from 28 Feb to 21 Mar 2025.

Option A: Approve revised timeline — accept 3-week delay to overall programme (recommended)

Option B: Maintain Feb deadline — accept elevated risk with interim compensating controls

D02

Authorise Additional Budget — AWS Infrastructure Overrun

Owner: CFO / Programme Board  ·  Decision needed by: 7 Feb 2025

Cloud infrastructure costs are tracking £400K above original estimate due to higher-than-anticipated data egress charges and additional compute needed for backfill runs.

Option A: Release £400K from programme contingency (contingency balance: £380K — shortfall of £20K)

Option B: Approve supplementary budget of £420K via Change Control process (recommended)

D03

Confirm Go-Live Approach: Big Bang vs Phased Rollout

Owner: CDO / CTO  ·  Decision needed by: 14 Feb 2025

Original plan assumes a single go-live in June 2025. Given current delays, the team proposes a phased approach — Analytics in June, Data Platform in Aug — reducing deployment risk.

Option A: Proceed with phased rollout (Jun + Aug) — lower risk, business continuity maintained (recommended)

Option B: Maintain big-bang June go-live — higher risk but avoids dual running of legacy and new platforms

NovaTech Solutions &amp; Apex Consulting  ·  Strictly Confidential  ·  Q1 2025  ·  Slide 7

NEXT STEPS  ·  KEY ACTIONS FOR FEBRUARY 2025

|   # | Action                                                                | Owner         | Due    | Priority   |
|-----|-----------------------------------------------------------------------|---------------|--------|------------|
|   1 | Finalise and present IAM remediation plan to Security Review Board    | R. Mbeki      | 7 Feb  | Critical   |
|   2 | Raise Change Control for additional AWS infrastructure budget (£420K) | Programme Mgr | 7 Feb  | Critical   |
|   3 | Convene Governance Committee to sign off Data Policy v2.0             | P. Navarro    | 5 Feb  | Critical   |
|   4 | Fix legacy ETL schema mismatch — deploy permanent patch to production | A. Thornton   | 31 Jan | High       |
|   5 | Submit Power BI licence procurement request for CFO approval          | L. Yamamoto   | 3 Feb  | High       |
|   6 | Complete SME review and freeze Change & Training content              | C. Harrington | 30 Jan | High       |
|   7 | Provision corrected test environment refresh automation script        | D. Okafor     | 7 Feb  | High       |
|   8 | Initiate DPIA workshop with DPO for new analytics module              | P. Navarro    | 12 Feb | Medium     |
|   9 | Confirm executive decision on Go-Live approach (D03)                  | CDO / CTO     | 14 Feb | Medium     |
|  10 | Deliver monthly programme status report to Steering Committee         | Programme Mgr | 28 Feb | Standard   |

📅  Next Executive Governance Review: 28 February 2025  ·  09:00–10:30  ·  Boardroom 4B / Virtual

NovaTech Solutions &amp; Apex Consulting  ·  Strictly Confidential  ·  Q1 2025  ·  Slide 8