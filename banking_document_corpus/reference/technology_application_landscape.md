# FirstUK Bank — Application Landscape

## Metadata

| Field | Value |
|-------|-------|
| Document ID | APP-LAND-001 |
| Version | 2.1 |
| Effective Date | 2026-01-15 |
| Review Date | 2027-01-15 |
| Owner | Chief Technology Officer |
| Department | Technology |
| Status | Active |
| Supersedes | None |
| Superseded By | None |
| Classification | Internal |
| Audience | Technology Staff, Architects, Developers, Operations |
| Related Documents | [[systems]], [[technology_context_diagram]], [[technology_api_catalogue]] |

---

## Purpose

Provide a complete catalog of all applications, systems, and platforms used by FirstUK Bank, including their ownership, technology stack, lifecycle status, and integrations.

---

## System Landscape Overview

```
┌─────────────────────────────────────────────────────────────┐
│                   Customer Channels                          │
├─────────────────────────────────────────────────────────────┤
│  Branch  │  Online Banking  │  Mobile App  │  Phone/Chat    │
└────────────────┬───────────────────────┬────────────────────┘
                 │                       │
         ┌───────▼───────────────────────▼───────┐
         │      API Gateway (Kong)                │
         │  Authentication, Rate Limiting, Docs  │
         └──────────────┬──────────────────────┘
                        │
         ┌──────────────┼──────────────┬──────────────┐
         │              │              │              │
    ┌────▼──┐   ┌──────▼──┐   ┌──────▼──┐   ┌─────▼──┐
    │  AMS  │   │  IBP    │   │  FDE    │   │  PPS   │
    │ Acct  │   │Internet │   │ Fraud   │   │Payment │
    │ Mgmt  │   │Banking  │   │Detect   │   │Process │
    └──┬────┘   └────┬────┘   └────┬────┘   └────┬───┘
       │             │             │             │
       └─────────────┼─────────────┼─────────────┘
                     │
            ┌────────▼──────────┐
            │   Data Warehouse  │
            │  Analytics/Reporting
            └───────────────────┘
```

---

# CORE SYSTEMS

## 1. Account Management System (AMS)

**System ID:** AMS-001  
**Status:** Active (Production)  
**Technology Stack:** Java Spring Boot microservices, PostgreSQL  
**Deployment:** AWS (Kubernetes)  
**Availability SLA:** 99.9%  
**Owner:** Head of Technology  
**Support Team:** Database team, Application team  

### Purpose
Central repository for customer accounts, balances, and transaction ledger.

### Key Features
- Account CRUD (create, read, update, delete)
- Balance calculation and reconciliation
- Transaction ledger (immutable record)
- Interest calculation
- Account status management

### Data Entities
- Customers (500 records)
- Accounts (800 records)
- Account Holders (joint account support)
- Transactions (2,000+ monthly)
- Standing Orders (50+ active)
- Direct Debits (30+ active)

### Database
- **Database:** PostgreSQL 13+
- **Size:** ~500 MB (growing ~50 MB/month)
- **Backup:** Daily, encrypted to S3
- **Replication:** Continuous to standby

### APIs Exposed
- `POST /accounts` — Create account
- `GET /accounts/{id}` — Retrieve account
- `POST /accounts/{id}/transactions` — Record transaction
- `GET /accounts/{id}/balance` — Get current balance

### Integrations
- ← Internet Banking Platform (account data)
- ← Mobile Banking App (account queries)
- ← Fraud Detection Engine (transaction data)
- ← Payment Processing System (balance updates)
- → Data Warehouse (nightly batch)

### Maintenance Schedule
- **Patching:** Monthly (Tuesday 2–4 AM)
- **Backups:** Daily (midnight)
- **Archiving:** Monthly (old data to cold storage)

---

## 2. Internet Banking Platform (IBP)

**System ID:** IBP-001  
**Status:** Active (Production)  
**Technology Stack:** React.js (frontend), Node.js (backend), Express  
**Deployment:** AWS (ECS)  
**Availability SLA:** 99.8%  
**Owner:** Head of Technology  
**Support Team:** Frontend team, Backend team  

### Purpose
Web-based customer portal for account access and self-service.

### Key Features
- Secure login (2FA via OTP)
- Account dashboard
- Transaction history
- Standing order management
- Direct debit authorization
- Money transfer
- E-statements
- Profile management

### Technology Details
- **Frontend:** React 17+, TypeScript
- **Backend:** Node.js 16+, Express
- **Database:** PostgreSQL (shared with AMS)
- **Cache:** Redis (session management)
- **CDN:** CloudFront (static assets)

### Performance
- Response time (p95): <2 seconds
- Daily active users: 200–300
- Peak traffic: 10:00–12:00 GMT
- Monthly transactions: 500+

### Security
- TLS 1.3 encryption
- Session timeout: 15 minutes idle
- Rate limiting: 100 requests/minute per user
- XSS/CSRF protection

### Integrations
- → AMS (account queries, transaction initiation)
- → FDE (fraud scoring for transfers)
- → PPS (payment submission)

---

## 3. Mobile Banking App (MBA)

**System ID:** MBA-001  
**Status:** Active (Production)  
**Technology Stack:** React Native, Node.js backend  
**Deployment:** AWS + App Stores (iOS/Android)  
**Availability SLA:** 99.5%  
**Owner:** Head of Technology  
**Support Team:** Mobile team, Backend team  

### Purpose
Native mobile app for iOS and Android devices.

### Key Features
- Biometric login (fingerprint, face)
- Account dashboard (mobile-optimized)
- Push notifications
- Card management (lock/unlock)
- Money transfer
- Virtual card numbers

### Technology Details
- **Frontend:** React Native (cross-platform)
- **Backend:** Node.js (shared with IBP)
- **Database:** PostgreSQL (shared with AMS)
- **Push Notifications:** Firebase Cloud Messaging

### App Metrics
- **Downloads:** 800+ (cumulative)
- **Daily active users:** 150–200
- **Ratings:** 4.7/5 stars (iOS/Android)
- **Latest version:** 2.3.1

### Platform Support
- **iOS:** Version 13+
- **Android:** Version 9+
- **Update frequency:** Monthly

### Integrations
- Same as IBP (shared backend services)

---

## 4. Fraud Detection Engine (FDE)

**System ID:** FDE-001  
**Status:** Active (Production)  
**Technology Stack:** Python, TensorFlow, Apache Kafka  
**Deployment:** AWS (Lambda + EC2)  
**Availability SLA:** 99.7%  
**Owner:** Head of Financial Crime  
**Support Team:** Data science team, DevOps  

### Purpose
Real-time fraud detection and transaction risk scoring.

### Key Features
- Transaction risk scoring (<100ms latency)
- Rule-based detection (20+ fraud rules)
- Anomaly detection (ML models)
- Velocity checks
- Geographic anomaly detection
- Auto-blocking for high-risk transactions

### Data Input
- Real-time transactions from AMS/PPS
- Customer profile data
- Historical transaction data
- External threat intelligence

### Models
- **Transaction Risk Model** — Predicts fraud probability (0–100)
- **Anomaly Detection Model** — Detects unusual patterns
- **Chargeback Prediction** — Predicts dispute likelihood

### Performance
- Detection rate: 97%+
- False positive rate: <2%
- Average fraud prevented/month: £47k–£80k

### Integrations
- ← AMS (transaction stream)
- ← IBP (real-time transaction scoring)
- ← PPS (transfer risk assessment)
- → Database (fraud alerts stored)

---

## 5. Payment Processing System (PPS)

**System ID:** PPS-001  
**Status:** Active (Production)  
**Technology Stack:** Java, RabbitMQ, Spring Boot  
**Deployment:** AWS (Kubernetes)  
**Availability SLA:** 99.95%  
**Owner:** Head of Technology  
**Support Team:** Payments team  

### Purpose
Process customer payments and inter-bank settlements.

### Supported Payment Schemes
- Faster Payments (FPS) — Real-time, up to £1M
- BACS — Overnight, high volume
- CHAPS — High-value, real-time
- Internal transfers (same-bank)

### Processing
- **FPS:** Immediate settlement
- **BACS:** Submitted 20:00, settled next day
- **CHAPS:** Real-time (during business hours)

### Performance
- Transactions/day: 100–200
- Success rate: 99.95%
- Processing latency (FPS): <15 minutes

### Integrations
- ← AMS (payment instruction)
- ← FDE (fraud assessment)
- → Bank of England (settlement)
- → External banks (payment delivery)

---

## 6. Data Warehouse (DW)

**System ID:** DW-001  
**Status:** Active (Production)  
**Technology Stack:** AWS Redshift, Apache Spark, S3  
**Deployment:** AWS  
**Update Frequency:** Nightly batch + real-time  
**Owner:** Head of Technology  
**Support Team:** Data engineering team  

### Purpose
Centralized analytics and reporting platform.

### Data Marts
- **Customer Mart:** 500 customers, demographics
- **Account Mart:** 800 accounts, balances, product mix
- **Transaction Mart:** 2,000+ monthly transactions
- **Product Mart:** 5 products, performance KPIs
- **Compliance Mart:** KYC, AML, complaints data

### Key Dashboards
- Executive Dashboard (KPIs)
- Product Performance
- Fraud Detection
- Compliance & Risk
- Customer Analytics

### Data Flow
- Nightly ETL from AMS
- Real-time streaming (Kafka)
- Cold storage archive (S3 Glacier after 3 years)

---

## SUPPORTING SYSTEMS

## 7. Customer Relationship Management (CRM)

**System ID:** CRM-001  
**Status:** Active (Production)  
**Platform:** Salesforce CRM (SaaS)  
**Owner:** Head of Retail Banking  

### Purpose
Customer interaction tracking and relationship management.

### Features
- Contact management
- Interaction history
- Campaign tracking
- Complaint logging
- Sales pipeline

---

## 8. Document Management System (DMS)

**System ID:** DMS-001  
**Status:** Active (Production)  
**Platform:** Elasticsearch + S3  
**Owner:** Head of Operations  

### Purpose
Store and manage internal documents (policies, procedures, templates).

### Document Types
- Policies
- SOPs
- Email/SMS templates
- Reports
- Compliance docs

---

## 9. Reporting & Regulatory System (RRS)

**System ID:** RRS-001  
**Status:** Active (Production)  
**Platform:** Python + XBRL toolkit  
**Owner:** Head of Compliance  

### Purpose
Generate regulatory reports (COREP, FINREP, CASS).

### Reports
- COREP (capital requirements)
- FINREP (financial statements)
- CASS (client assets)
- AML suspicious activity reports

---

# SYSTEM LIFECYCLE STATUS

| System | Status | Version | Last Update | EOL Date |
|--------|--------|---------|-------------|----------|
| AMS | Active | 3.2 | 2026-01-15 | None |
| IBP | Active | 2.5 | 2026-01-10 | None |
| MBA | Active | 2.3.1 | 2025-12-20 | None |
| FDE | Active | 1.8 | 2025-11-30 | None |
| PPS | Active | 2.1 | 2025-12-15 | None |
| DW | Active | 1.4 | 2025-10-01 | None |
| CRM | Active | Cloud | Continuous | None |
| DMS | Active | Cloud | Continuous | None |
| RRS | Active | 1.2 | 2025-09-01 | None |

---

# CONTACT & SUPPORT

**For system issues:**
- **AMS/IBP/MBA:** Contact Technology Help Desk (ext. 5000)
- **FDE:** Contact Data Science team (fraud@firstuk.bank)
- **PPS:** Contact Payments team (payments@firstuk.bank)
- **DW:** Contact Data Engineering (analytics@firstuk.bank)

---

## Document Version History

| Version | Date | Change | Author |
|---------|------|--------|--------|
| 2.1 | 2026-01-15 | Added new systems, updated metrics | CTO |
| 2.0 | 2025-07-01 | Added DW, CRM, DMS | CTO |
| 1.0 | 2025-01-01 | Initial landscape | CTO |

---

## Sign-Off

**Approved by:**  
Chief Technology Officer — **Date: 2026-01-15**

---
