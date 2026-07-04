# FirstUK Bank — Information Security Policy

## Metadata

| Field | Value |
|-------|-------|
| Document ID | SEC-POL-002 |
| Version | 2.0 |
| Effective Date | 2026-01-15 |
| Review Date | 2027-01-15 |
| Owner | Chief Information Security Officer |
| Department | Technology |
| Status | Active |
| Classification | Internal |
| Audience | All Staff, Technology, Risk, Board |
| Regulatory Reference | FCA COBS Part 5, PRA Operational Resilience |
| Related Documents | [[systems]], [[departments]], [[policy_operational_risk]] |

---

## Executive Summary

FirstUK Bank maintains an Information Security Policy to protect confidentiality, integrity, and availability of customer data, systems, and intellectual property against threats (cyberattacks, unauthorized access, data breaches).

---

## Purpose & Scope

### Purpose

To establish security procedures that:
- Prevent unauthorized access to systems/data
- Protect customer data against breaches
- Ensure system availability and reliability
- Respond rapidly to security incidents
- Meet regulatory security standards (PRA Operational Resilience, FCA)

### Scope

Applies to:
- All customer data (personal, financial)
- All systems and infrastructure
- All staff, contractors, third parties
- All physical and digital assets
- All geographic locations

---

## Policy Statement

**FirstUK Bank protects customer data and systems through defense-in-depth security controls, incident response, and continuous threat monitoring.**

---

## Security Principles

**Zero-Trust Security:**
- No implicit trust (users, devices, networks)
- Continuous authentication and authorization
- Verify every access request
- Assume breach and defend proactively

**Defense-in-Depth:**
- Multiple layers of security controls
- No single point of failure
- Layered protections (network, system, data, process)

**Confidentiality-Integrity-Availability (CIA Triad):**
- **Confidentiality:** Data encrypted, access controlled
- **Integrity:** Data cannot be modified without authorization
- **Availability:** Systems accessible when needed

---

## Threat Landscape

**Known Threats FirstUK Bank Monitors:**

- **Cyberattacks:** Ransomware, malware, phishing
- **Unauthorized Access:** Hacking, credential theft, social engineering
- **Data Breaches:** Sensitive data theft, exposure
- **Insider Threats:** Disgruntled staff, inadvertent disclosure
- **Third-Party Risk:** Vendor compromise, supply chain attacks
- **Natural Disasters:** Power outages, fire, flooding

---

## Access Control

### Authentication (Who You Are)

**Customer Authentication:**
- Online Banking: Username/password + OTP (email/SMS)
- Mobile App: Biometric (fingerprint/face) + optional PIN
- Branch: Photo ID verification

**Staff Authentication:**
- LDAP/Active Directory (username/password)
- MFA for privileged access (admin, compliance)
- Physical badges for facility access
- VPN required for remote access

### Authorization (What You Can Access)

**Role-Based Access Control (RBAC):**
- Customer-facing staff: Limited customer data (own customers)
- Compliance staff: Full data access + audit logging
- Technology staff: System access with audit trail
- Executives: Aggregated reports only
- Developers: Pre-production environments only

**Segregation of Duties:**
- Account opening != Account approval
- Transaction initiation != Transaction approval
- System administration != Application development
- Audit != Tested system ownership

**Logging & Monitoring:**
- All access logged (who, what, when, where)
- Audit logs retained 1 year
- Suspicious access patterns flagged
- Periodic access reviews (quarterly)

---

## Data Encryption

### In Transit

**TLS 1.3 (Transport Layer Security):**
- All internet-facing connections encrypted
- HTTPS enforced (HTTP redirects to HTTPS)
- mTLS for internal service-to-service communication
- Certificate management (auto-renewal, monitoring)

**VPN for Remote Access:**
- Staff working remotely use VPN
- All traffic encrypted through secure tunnel
- Multi-factor authentication required

### At Rest

**Database Encryption:**
- AES-256 encryption for sensitive columns
- Encryption keys stored separately (HSM)
- Transparent encryption (application-level)

**Backup Encryption:**
- Encrypted backups to S3
- AES-256 encryption standard
- Keys managed in AWS KMS
- Tested recovery procedures

**File Encryption:**
- Encrypted file storage (on-premises + cloud)
- Encrypted USB/portable devices
- Deletion: Secure overwriting (DoD standard)

---

## Network Security

### Perimeter Security

**Firewall:**
- Stateful inspection firewall
- Ingress: Port 443 (HTTPS) only for public APIs
- Egress: Restricted outbound (whitelist-based)
- Rules logged and monitored

**DDoS Protection:**
- AWS Shield (managed DDoS protection)
- Rate limiting on APIs
- Anomalous traffic detection
- Traffic rerouting if DDoS detected

**Intrusion Detection/Prevention:**
- Network-based IDS (Snort-like rules)
- Host-based IDS on critical systems
- Automated response (block malicious IPs)

### Internal Network Segmentation

**Network Segregation:**
- Customer-facing systems: Separate subnet
- Internal staff systems: Separate subnet
- Database tier: Isolated, restricted access
- Development systems: Separated from production

**VPN & Bastion Hosts:**
- Remote access through VPN only
- Bastion host for admin access
- Multi-hop authentication
- Session logging and timeout

---

## Endpoint Security

### Laptops & Desktops

**Antivirus/Anti-malware:**
- Endpoint detection and response (EDR) tool deployed
- Real-time scanning enabled
- Virus definitions updated daily
- Suspicious behavior isolated automatically

**Device Management:**
- Mobile Device Management (MDM) for phones/tablets
- Encryption enforced on all devices
- Lost device remote wipe capability
- OS patches applied within 30 days (critical within 7)

**Full-Disk Encryption:**
- BitLocker (Windows), FileVault (Mac)
- Encryption keys escrowed (recovery)
- Mandatory for all devices with customer data access

---

## Application Security

### Development Security (Secure SDLC)

**Code Review:**
- Peer review required before deployment
- Security-focused code review checklist
- Authorization controls review
- SQL injection/XSS prevention checks

**Testing:**
- Unit testing (developers)
- Integration testing (QA)
- Security testing (penetration tests, vulnerability scans)
- Load testing (before production release)

**Deployment:**
- Automated deployment (no manual prod changes)
- Change advisory board approval (changes to prod)
- Rollback plan documented
- Production monitoring enabled

### API Security

**API Gateway (Kong):**
- Authentication (API key + JWT)
- Rate limiting (prevent abuse)
- Request validation (prevent injection)
- Monitoring and alerting

**API Design:**
- Input validation (sanitize all inputs)
- Output encoding (prevent XSS)
- Authorization checks (role-based)
- Audit logging of API calls

---

## Vulnerability Management

### Vulnerability Scanning

**Frequency:**
- Production systems: Weekly scans
- Pre-deployment: Every release
- Third-party libraries: Continuous scanning

**Tools:**
- Network vulnerability scanner (Nessus)
- Web application scanner (Burp Suite)
- Container image scanner (Trivy)
- Dependency checker (npm audit, etc.)

### Patch Management

**Criticality Timeline:**
- **Critical:** Patch within 7 days
- **High:** Patch within 30 days
- **Medium:** Patch within 60 days
- **Low:** Patch within 90 days

**Process:**
1. Vulnerability identified
2. Patch released by vendor
3. Testing in non-prod
4. Production deployment per timeline
5. Verification and monitoring

---

## Third-Party Risk

### Vendor Security Assessment

**Pre-Assessment:**
- Security questionnaire completed
- SOC 2 Type II audit reviewed (if available)
- Penetration test results (if applicable)
- Reference checks (other customers)

**Ongoing:**
- Annual re-assessment
- Incident notification requirements (SLA: 24 hours)
- Audit rights (FirstUK Bank can audit vendor)
- Service Level Agreement (SLA) for security

### Third-Party Access

**Contractor/Vendor Access:**
- Limited to systems needed
- Time-bound (auto-revocation)
- Monitored (all actions logged)
- MFA required
- NDA and security agreement signed

---

## Physical Security

### Data Centers

**Access Control:**
- Badge access only (biometric at main entrance)
- Visitor log maintained
- Escort required for non-staff
- CCTV monitoring (24/7)

**Environmental Controls:**
- Fire suppression (sprinkler system)
- Power backup (generators, UPS)
- Climate control (temperature/humidity)
- Water detection (to prevent flooding)

### Offices & Branches

**Access Control:**
- Badge access (doors locked after hours)
- Visitor sign-in
- Conference rooms lockable
- Secure document storage areas

**Screen Privacy:**
- Desk positioning to prevent shoulder-surfing
- Privacy filters on laptops (when public spaces)
- Screens lock (screen saver after 15 min)

---

## Incident Response

### Incident Reporting

**Immediate Actions:**
1. Detect incident (automated alerts or staff report)
2. Report to Information Security Officer
3. Escalate to CTO and CRO if confirmed
4. Activate incident response team

**Classification:**
- **Severity 1 (Critical):** Data breach, ransomware, system unavailable
- **Severity 2 (High):** Suspected breach, unauthorized access, significant compromise
- **Severity 3 (Medium):** Malware detected, vulnerability found, access attempt
- **Severity 4 (Low):** Policy violation, minor vulnerability, suspicious activity

### Incident Investigation

**Timeline:**
- Severity 1: Investigation started immediately
- Severity 2: Investigation within 2 hours
- Severity 3: Investigation within 24 hours
- Severity 4: Investigation within 1 week

**Investigation Elements:**
- What happened (timeline)
- How it happened (root cause)
- What data was affected
- How many customers impacted
- Extent of compromise

### Containment & Recovery

**Containment:**
- Isolate affected systems (disconnect from network)
- Preserve evidence (logs, memory, disk)
- Prevent spread (quarantine malware)
- Restore from backup (if uncompromised)

**Recovery:**
- Restore affected systems from clean backup
- Patch vulnerability
- Verify integrity post-recovery
- Gradual restoration to production

### Notification & Reporting

**Internal Notification:**
- Immediately to CTO, CRO, CEO (Severity 1–2)
- To Board within 24 hours (Severity 1–2)

**Regulatory Notification:**
- FCA: Suspicious breach within 24 hours (if confirmed)
- ICO (UK): Data breach report within 72 hours (if personal data breach)

**Customer Notification:**
- If personal data breach: Notify customers per GDPR (without undue delay)
- Email/letter with details and mitigation steps

---

## Security Training

**Mandatory Annual Training:**
- All staff: 1-hour security awareness training
- Phishing simulation (monthly test emails)
- Certification: Staff complete quiz
- Management: 2-hour security responsibility training

**Topics:**
- Phishing and social engineering
- Password security
- Data protection
- Incident reporting
- Physical security
- Regulatory compliance

**Non-Compliance:**
- Failure to complete training: Disciplinary action
- Repeated policy violations: Termination

---

## Audit & Monitoring

**Internal Audit:**
- Annual security assessment
- Penetration testing (annual)
- Access control review (quarterly)
- Vulnerability scanning validation

**Regulatory Audit:**
- FCA periodic operational resilience reviews
- PRA Operational Resilience assessment

**Continuous Monitoring:**
- Security Information and Event Management (SIEM)
- Real-time threat detection
- Anomalous behavior alerts
- Suspicious access attempts logged

---

## Compliance & Regulatory Requirements

**Regulations Addressed:**
- FCA COBS Part 5 (Cybersecurity)
- PRA Operational Resilience
- GDPR (data protection)
- ISO 27001 (information security standard)

---

## Policy Review & Updates

**Review Frequency:** Annual (or upon threat/regulatory change)  
**Last Review:** 2026-01-15  
**Next Review:** 2027-01-15  

---

## Related Documents

- [[systems]] — Technology architecture, security measures
- [[policy_operational_risk]] — Operational risk management
- Incident Response Plan (detailed procedures)
- Security Control Framework (detailed controls)

---

## Sign-Off

**Approved by:**  
Chief Information Security Officer — **Date: 2026-01-15**  
Chief Technology Officer — **Date: 2026-01-15**  
Chief Risk Officer — **Date: 2026-01-15**  
Chief Executive Officer — **Date: 2026-01-15**

---
