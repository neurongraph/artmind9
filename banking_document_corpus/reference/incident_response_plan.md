# FirstUK Bank — Incident Response Plan

## Metadata

| Field | Value |
|-------|-------|
| Document ID | IRP-001 |
| Version | 1.1 |
| Effective Date | 2026-01-15 |
| Review Date | 2027-01-15 |
| Owner | Chief Information Security Officer |
| Department | Technology |
| Status | Active |
| Classification | Internal |
| Audience | Technology, Operations, Executive, Board |
| Related Documents | [[policy_information_security]], [[policy_operational_risk]], [[escalation_matrix]] |

---

## Purpose

Establish procedures for detecting, investigating, and responding to security incidents (cyberattacks, data breaches, unauthorized access, system failures) to minimize impact and restore operations quickly.

---

## Incident Categories & Severity

### Incident Types

**1. Cybersecurity Incident**
- Malware infection
- Ransomware attack
- Unauthorized access
- Data exfiltration
- DDoS attack
- Credential compromise

**2. Data Breach**
- Customer personal data exposed
- Financial data accessed
- System/operational data leaked
- Regulatory data breached

**3. System Incident**
- System outage/unavailability
- Data corruption
- Performance degradation
- Integration failure
- Backup failure

**4. Physical Security Incident**
- Facility breach
- Equipment theft
- Fire/environmental damage
- Access control failure

**5. Fraud/Misconduct**
- Unauthorized transactions
- Employee fraud
- Third-party fraud

### Severity Classification

**Severity 1 (Critical)**
- Major data breach (personal data exposed, >1,000 customers)
- Ransomware affecting critical systems
- System outage (>4 hours)
- Customer data public exposure
- Regulatory violation (significant)
- Estimated financial impact: >£100,000

**Severity 2 (High)**
- Suspected data breach (<1,000 customers affected)
- Unauthorized access detected
- System outage (1–4 hours)
- Fraud incident (>£10,000)
- Regulatory concern
- Estimated financial impact: £10,000–£100,000

**Severity 3 (Medium)**
- Suspected unauthorized access (not confirmed)
- Malware detected (contained)
- System performance issue
- Fraud incident (<£10,000)
- Policy violation
- Estimated financial impact: £1,000–£10,000

**Severity 4 (Low)**
- Security alert (false positive)
- Policy violation (minor)
- System hiccup (minor impact)
- Incident report (no immediate threat)
- Estimated financial impact: <£1,000

---

## Incident Response Team

### Composition

**Incident Commander (IC)**
- Authority: Directs response, makes decisions
- Primary: Chief Information Security Officer
- Backup: Chief Technology Officer

**Response Team Members:**
- Technology Lead (system expertise)
- Security Lead (investigation)
- Communications Lead (internal/external messaging)
- Operations Lead (restoration)
- Compliance/Legal (regulatory obligations)

### On-Call Rotation

- **Severity 1:** Immediate escalation (within 1 hour)
- **Severity 2:** Within 2 hours
- **Severity 3:** Next business day
- **Severity 4:** Logged, scheduled investigation

---

## Incident Response Procedure

### Phase 1: Detect & Alert (0–15 Minutes)

**Detection Methods:**
- Automated alerts (SIEM, IDS, monitoring)
- Staff report (customer, employee, system)
- Regulatory notification (law enforcement, regulator)
- Third-party notification (vendor, partner)

**Immediate Actions:**
1. **Report:** Alert reaches on-call incident commander
2. **Verify:** Confirm incident is real (not false alarm)
3. **Classify:** Assign severity level (1–4)
4. **Notify:** Escalate per escalation matrix ([[escalation_matrix]])
5. **Activate:** Convene incident response team

**Notification Timeline:**
- **Severity 1:** Executive team (ASAP), Board (within 2 hours)
- **Severity 2:** Executive team (within 30 min), CRO (within 1 hour)
- **Severity 3:** Department head (within 4 hours)
- **Severity 4:** Log incident (routine)

---

### Phase 2: Containment (15–60 Minutes)

**Objectives:**
- Stop attack/prevent spread
- Preserve evidence
- Protect unaffected systems
- Maintain business continuity

**Containment Actions:**

**For Cyberattack (Malware/Ransomware):**
- Isolate affected system (disconnect from network)
- Prevent further access (reset credentials, block IP)
- Preserve logs/evidence (copy before shutdown)
- Kill processes (if possible without data loss)

**For Data Breach:**
- Identify compromised data (scope)
- Contain access (revoke credentials)
- Monitor for misuse (credit monitoring if PII)
- Preserve evidence (logs, audit trail)

**For System Outage:**
- Failover to backup (if available)
- Isolate failed component
- Prevent escalation
- Begin restoration

**For Fraud:**
- Freeze compromised account
- Block fraudulent transactions
- Prevent further unauthorized access
- Collect evidence

---

### Phase 3: Investigation (1–24 Hours)

**Investigative Steps:**
1. **Timeline:** When did incident start? How was it detected?
2. **Scope:** How many systems/customers affected?
3. **Root Cause:** How did it happen? Why?
4. **Data:** What data was accessed/exfiltrated?
5. **Threat:** Who did this? External or internal?
6. **Evidence:** Collect logs, memory dumps, disk images

**Investigation Tools:**
- Forensic tools (EnCase, FTK)
- Log analysis (splunk, ELK)
- Network analysis (packet capture, network flows)
- Endpoint analysis (EDR telemetry)

**Evidence Preservation:**
- Chain of custody maintained
- Logs collected and protected
- System snapshots taken
- Timeline established

---

### Phase 4: Recovery & Restoration (1–48 Hours)

**Recovery Actions:**

**System Recovery:**
1. **Backup Restore:** Restore from clean backup
2. **Patch:** Apply security patches
3. **Rebuild:** Rebuild from clean media (if needed)
4. **Test:** Verify functionality, no malware
5. **Monitor:** Watch for re-infection

**Data Recovery:**
1. **Identify:** What data needs recovery
2. **Restore:** Restore from backup
3. **Validate:** Verify data integrity
4. **Communicate:** Notify customers (if breach)

**Service Restoration:**
1. **Phased:** Restore critical services first
2. **Verify:** Test before full restoration
3. **Capacity:** Ensure systems handle normal load
4. **Monitoring:** Enhanced monitoring post-incident

---

### Phase 5: Notify & Report (24–72 Hours)

### Internal Notification

**Immediate (within 4 hours):**
- Executive team (CEO, CFO, CRO)
- Board (if Severity 1–2)
- Department heads (affected areas)

**Follow-up (24 hours):**
- All staff (via email + meeting)
- Customers (if data breach)
- Partners/vendors (if affected)

### External Notification

**Regulatory Notification (If Required):**
- **Data Breach (Personal Data):** ICO within 72 hours
- **Security Breach (Suspected):** FCA within 24 hours
- **Fraud/Crime:** Law enforcement (police report)
- **Sanctions Breach:** OFSI (if sanctions violation)

**Customer Notification (If Required):**
- **Data Breach:** Email/letter (72 hours)
- **Content:** Incident description, impact, mitigation, contact info
- **Support:** Credit monitoring offer (if appropriate)

### Documentation

**Incident Report:**
- Summary of incident
- Timeline and progression
- Root cause analysis
- Impact assessment
- Remediation steps
- Prevention recommendations

**Report Distribution:**
- Executive team
- Board Risk Committee
- Internal Audit
- Regulators (if required)

---

### Phase 6: Post-Incident Review (1–2 Weeks)

**Review Meeting:**
- Incident commander leads review
- Incident response team attends
- Key stakeholders included
- Lessons learned discussed

**Review Topics:**
1. **What Happened?** Incident summary
2. **Why Did It Happen?** Root cause (technical and process)
3. **What Did We Do Right?** Acknowledgment of good decisions
4. **What Could We Improve?** Process/technical gaps
5. **What's Our Fix?** Preventive and corrective actions

**Outcomes:**
- Documented lessons learned
- Action plan (prevent recurrence)
- Owner assigned (implement fix)
- Timeline (when fix complete)

**Follow-Up:**
- Owner implements corrective actions (30–90 days)
- Effectiveness verified (audit or re-test)
- Communication sent to team

---

## Communication During Incident

### Internal Communication

**Frequency:**
- **Severity 1:** Hourly updates to executive team
- **Severity 2:** 2-hourly updates
- **Severity 3:** Daily updates
- **Severity 4:** Email summary

**Communication Template:**
```
Time: [HH:MM]
Status: [Ongoing / Controlled / Resolved]
Impact: [Description of customer/system impact]
Actions: [What we're doing]
ETA: [Estimated restoration time]
Next Update: [When next communication]
```

### Customer Communication (If Data Breach)

**Email/Letter Contents:**
- Incident description (plain language)
- Timeline (when discovered, when reported)
- Data affected (type, scope, number of customers)
- Mitigation (what we did to contain)
- Customer action (what they should do)
- Support (credit monitoring, phone line for questions)
- Apology (acknowledge impact)

---

## Incident Severity Examples

### Severity 1 Example
```
Ransomware affects payment processing system
- 4-hour outage
- 100+ failed transactions
- Customer funds delayed
- >£50,000 estimated impact
→ Response: IMMEDIATE
→ Board notification: Within 2 hours
→ Regulatory notification: Within 24 hours (FCA)
```

### Severity 2 Example
```
Unauthorized access to customer data
- 50 customers' personal data accessed
- No evidence of exfiltration
- Access stopped within 2 hours
- £10,000–£100,000 estimated investigation cost
→ Response: Within 2 hours
→ Notification: Executive team + CRO
→ Regulatory: Under assessment (may need notification)
```

### Severity 3 Example
```
Malware detected on staff laptop
- User clicked phishing link
- Malware contained (endpoint isolated)
- No spread to other systems
- Routine incident
→ Response: Next business day
→ Notification: Department head
→ No external notification
```

---

## Tools & Resources

**Detection Tools:**
- SIEM (Splunk)
- IDS (Snort)
- Antivirus (EDR)
- Log aggregation (ELK)

**Investigation Tools:**
- Forensic tools (EnCase)
- Memory dumps (WinDbg)
- Packet analysis (Wireshark)
- Timeline tools (Autopsy)

**Communication:**
- Incident Slack channel (real-time updates)
- War room (video call, during major incidents)
- Email for external communication

---

## Training & Exercises

**Annual Training:**
- Incident response procedures (all IT staff)
- Forensic investigation (security team)
- Breach notification (compliance team)

**Annual Tabletop Exercise:**
- Simulated incident scenario
- Response team activation
- Decision-making under pressure
- Communication practice

---

## Related Documents

- [[policy_information_security]] — Security Policy (preventive controls)
- [[policy_operational_risk]] — Operational Risk (incident categorization)
- [[escalation_matrix]] — Escalation paths
- Incident Log (tracking all incidents)

---

## Document Version History

| Version | Date | Change | Author |
|---------|------|--------|--------|
| 1.1 | 2026-01-15 | Added communication templates | CISO |
| 1.0 | 2025-01-01 | Initial plan | CISO |

---

## Sign-Off

**Approved by:**  
Chief Information Security Officer — **Date: 2026-01-15**  
Chief Technology Officer — **Date: 2026-01-15**  
Chief Risk Officer — **Date: 2026-01-15**  
Chief Executive Officer — **Date: 2026-01-15**

---
