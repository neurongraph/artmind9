# FirstUK Bank — Organisation Model

## Metadata

| Field | Value |
|-------|-------|
| Document ID | ORG-2026-001 |
| Version | 2.1 |
| Effective Date | 2026-01-15 |
| Review Date | 2027-01-15 |
| Owner | Chief Operating Officer |
| Department | Enterprise Architecture |
| Status | Active |
| Classification | Internal |
| Audience | Executive, Department Heads, Staff |
| Related Documents | [[departments]], [[systems]], [[business_ontology]] |

---

## Executive Summary

FirstUK Bank operates as a UK retail banking institution serving 500+ customers across 12 branches. The organisation is structured functionally with clear accountability lines, enabling efficient product delivery, risk management, and regulatory compliance.

**Founded:** 2018  
**Headquarters:** London  
**Regulatory Authority:** Financial Conduct Authority (FCA), Prudential Regulation Authority (PRA)  
**Primary Regulator Reference:** FCA Handbook, PRA Rulebook  

---

## Organisational Structure

```
┌─────────────────────────────────────────────────────────────┐
│                     Board of Directors                       │
│                                                              │
└──────────────────┬──────────────────────────────────────────┘
                   │
        ┌──────────┼──────────┐
        │          │          │
    ┌───▼──┐  ┌───▼──┐  ┌───▼──┐
    │ CEO  │  │ CFO  │  │ COO  │
    └──┬───┘  └──────┘  └──┬───┘
       │                    │
    ┌──┴─────────────────────┴──────┐
    │                               │
┌───▼────┐  ┌──────────┐  ┌────────▼─┐
│ Retail │  │ Risk &   │  │Technology│
│Banking │  │Compliance│  │          │
└────────┘  └──────────┘  └──────────┘
```

---

## Executive Leadership

### Chief Executive Officer (CEO)
- **Reports to:** Board of Directors
- **Responsibilities:** 
  - Overall strategic direction
  - Business performance
  - Board liaison
- **Department:** Office of the CEO

### Chief Financial Officer (CFO)
- **Reports to:** Board of Directors
- **Responsibilities:**
  - Financial management
  - Treasury
  - Financial reporting
  - Capital management
- **Department:** Finance

### Chief Operating Officer (COO)
- **Reports to:** Board of Directors
- **Responsibilities:**
  - Operational efficiency
  - Business process management
  - Technology governance
  - Internal audit
- **Department:** Operations

---

## Business Units

### 1. Retail Banking (Head: Director of Retail Banking)

**Mission:** Deliver consumer banking products and services

**Key Functions:**
- Product Management (see [[products]])
- Account Services
- Customer Service (see [[departments]])
- Sales & Onboarding
- Branch Operations (see [[branches]])

**Key Metrics:**
- Customer acquisition: 50 per quarter
- Account activation rate: 92%
- Net Promoter Score (NPS): 68
- Customer retention: 94%

**Primary Products:**
- SmartSaver Account (see [[business_ontology]])
- Current Accounts
- Mortgages
- Debit Cards

---

### 2. Risk & Compliance (Head: Chief Risk Officer)

**Mission:** Protect FirstUK Bank through proactive risk identification and regulatory compliance

**Key Functions:**
- Compliance (see [[departments]])
- Financial Crime (see [[departments]])
- Fraud Prevention
- Risk Assessment
- Internal Audit

**Regulatory Responsibilities:**
- AML/KYC compliance (per [[business_ontology]])
- Customer Due Diligence (CDD)
- Enhanced Due Diligence (EDD) for high-risk customers
- Suspicious Activity Reporting (SAR)

**Key Policies:**
- Customer Identification Policy (CID-POL-001)
- AML Policy (AML-POL-002)
- Operational Risk Policy (ORP-POL-003)
- Privacy Policy (PRI-POL-004)

---

### 3. Technology (Head: Chief Technology Officer)

**Mission:** Provide secure, scalable technology infrastructure

**Key Functions:**
- Enterprise Architecture (see [[systems]])
- Application Development
- Infrastructure & Operations
- Information Security
- Data Management

**Technology Vision:**
- Microservices-based architecture
- Cloud-ready infrastructure
- Real-time data processing
- API-first integration model (see [[systems]])

---

## Departments (Detailed)

See [[departments]] for complete departmental structure, roles, responsibilities, and interfaces.

### Core Departments:
1. **Retail Banking** — Product delivery, customer acquisition
2. **Product Management** — Product lifecycle, pricing, features
3. **Operations** — Back-office processes, settlements
4. **Customer Service** — Support, complaints, retention
5. **Financial Crime** — AML, sanctions, fraud
6. **Compliance** — Regulatory reporting, internal controls
7. **Risk** — Enterprise risk, credit risk, operational risk
8. **Technology** — Systems, infrastructure, security
9. **Enterprise Architecture** — Technology strategy, integration design
10. **Marketing** — Brand, campaigns, market positioning
11. **Legal** — Contracts, regulatory affairs, litigation
12. **Internal Audit** — Control testing, audit findings

---

## Governance Framework

### Decision-Making Authority

| Decision Type | Authority | Approval Required |
|---------------|-----------|-------------------|
| Product Launch | Board Risk Committee | Board, CRO, Business Unit Head |
| Policy Changes | Compliance Committee | CRO, COO, CEO |
| Technology Investment >£500k | Board Technology Committee | Board, CTO, CFO |
| Pricing Changes | Executive Committee | CEO, CFO, Business Unit Head |
| Risk Exceptions | Risk Committee | CRO, CEO |
| Customer Escalations | Management | Department Head + Escalation Matrix |

**See:** Escalation Matrix (ESC-001)

---

## Branch Network

**Total Branches:** 12  
**Total Employees:** 30 (approx. 2-3 per branch)  
**See:** [[branches]] for complete branch listing, locations, and staffing.

---

## Roles & Accountability

### Senior Roles

**Chief Executive Officer (CEO)**
- Accountable for overall bank performance
- Board reporting
- Strategic initiatives

**Chief Operating Officer (COO)**
- Accountable for operational efficiency
- Process optimization
- Technology delivery

**Chief Risk Officer (CRO)**
- Accountable for risk management
- Regulatory compliance
- Internal control effectiveness

**Chief Technology Officer (CTO)**
- Accountable for technology strategy
- System reliability
- Security posture

---

## Cross-Functional Committees

### Executive Committee
- **Frequency:** Weekly
- **Members:** CEO, CFO, COO, CRO, CTO
- **Purpose:** Strategic decision-making, escalation

### Board Risk Committee
- **Frequency:** Quarterly
- **Members:** Board directors (independent majority)
- **Purpose:** Risk oversight, policy approval, audit review

### Compliance Committee
- **Frequency:** Monthly
- **Members:** CRO, Compliance Head, Legal Head, relevant business heads
- **Purpose:** Regulatory updates, policy changes, compliance metrics

### Product Committee
- **Frequency:** Bi-weekly
- **Members:** Product Management, Retail Banking, Risk, Compliance, Technology
- **Purpose:** Product development, feature prioritization, launch planning

---

## Reporting Lines

All reporting relationships documented in [[departments]].

**Key Principle:** Clear single reporting line with dotted-line relationships for cross-functional collaboration.

---

## Workforce Overview

| Role Category | Count | Location Distribution |
|---------------|-------|----------------------|
| Customer-facing (Branch) | 12 | Branch network |
| Product & Strategy | 4 | Head Office |
| Operations | 5 | Head Office + Branches |
| Risk & Compliance | 5 | Head Office |
| Technology | 6 | Head Office |
| Finance | 2 | Head Office |
| Marketing | 1 | Head Office |

**Total Headcount:** 30+ (including contractors)

---

## Locations

**Head Office:** London  
**Branches:** See [[branches]]

---

## Related Policies

- Customer Identification Policy (CID-POL-001)
- AML Policy (AML-POL-002)
- Privacy Policy (PRI-POL-004)
- Operational Risk Policy (ORP-POL-003)
- Document Retention Policy (RET-POL-005)
- Complaint Handling Policy (COM-POL-006)

---

## Document Version History

| Version | Date | Change | Author |
|---------|------|--------|--------|
| 2.1 | 2026-01-15 | Added CTO role, updated headcount | COO |
| 2.0 | 2025-06-01 | Restructured reporting, added committees | CEO |
| 1.0 | 2024-01-01 | Initial document | COO |

---

## Sign-Off

**Approved by:**  
Chief Operating Officer — **Date: 2026-01-15**  
Chief Executive Officer — **Date: 2026-01-15**

---
