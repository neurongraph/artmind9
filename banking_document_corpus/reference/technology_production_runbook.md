# FirstUK Bank — Production Runbook

## Metadata

| Field | Value |
|-------|-------|
| Document ID | PROD-RUNBOOK-001 |
| Version | 1.2 |
| Effective Date | 2026-01-15 |
| Review Date | 2027-01-15 |
| Owner | Infrastructure Manager |
| Department | Technology/Operations |
| Status | Active |
| Classification | Internal |
| Audience | Operations Team, On-Call Engineers, Support Staff |
| Related Documents | [[technology_application_landscape]], [[incident_response_plan]], [[systems]] |

---

## Purpose

Provide step-by-step procedures for day-to-day operations, system health checks, incident response, and disaster recovery for all FirstUK Bank production systems.

---

## 1. DAILY OPERATIONS

### Pre-Business Hours (07:00 AM)

**Checklist (15 minutes):**

- [ ] **Check Dashboard** — Open ops dashboard at https://dashboard.firstuk.bank
  - AMS status: Should be "Green"
  - IBP status: Should be "Green"
  - MBA status: Should be "Green"
  - PPS status: Should be "Green"

- [ ] **Review Alerts Overnight** — Check alert queue for any issues reported 00:00–07:00
  - Command: `kubectl get events -n production | grep -i warning`
  - Action: Address any critical alerts immediately

- [ ] **Verify Database Backups** — Check last backup completed
  - Command: `aws s3 ls s3://firstuk-backups/ --recursive | tail -5`
  - Expected: Most recent backup within last 24 hours

- [ ] **Check Payment Systems** — Verify BACS/FPS connections active
  - Command: `curl https://api.firstuk.bank/health | grep "payments"`
  - Expected: "status": "ok"

**Issue Found?** → Go to Step 6 (Incident Response)

### During Business Hours (09:00–17:00)

**Hourly Checks (5 minutes each):**

- [ ] **API Gateway Health** — Monitor Kong gateway
  - Command: `kong health --yaml | grep status`
  - Expected: All services responding <100ms

- [ ] **Database Connection Pool** — Check PostgreSQL connections
  - Command: `psql -U postgres -c "SELECT datname, count(*) FROM pg_stat_activity GROUP BY datname;"`
  - Expected: <80% of max connections (200/250)

- [ ] **Transaction Processing Rate** — Monitor throughput
  - Command: `tail -f /var/log/pps/transactions.log | grep -c "SUCCESS" | head -1`
  - Expected: 2–5 transactions/minute during business hours

**Issue Found?** → Go to Step 6 (Incident Response)

### End of Business (17:00 PM)

**Checklist (10 minutes):**

- [ ] **Payment Settlement Confirmation** — Verify daily settlement completed
  - Expected: All FPS/BACS payments settled by 17:30

- [ ] **Reconciliation Report** — Run nightly reconciliation
  - Command: `python /scripts/reconciliation.py`
  - Expected: "PASS: All transactions reconciled"

- [ ] **Backup Initiation** — Trigger nightly backup
  - Command: `aws s3 sync /data/backups s3://firstuk-backups/ --sse AES256`
  - Expected: "Backup complete: XXX MB uploaded"

- [ ] **Data Warehouse Load** — Trigger nightly DW load
  - Command: `spark-submit /scripts/dw_load.py`
  - Expected: "Load complete: NNN rows inserted"

---

## 2. SYSTEM STARTUP (Emergency Only)

**When to Use:** After complete outage or scheduled maintenance restart

### Startup Sequence (Follow Exactly)

**Phase 1: Database (5–10 minutes)**

```
1. Start PostgreSQL
   systemctl start postgresql@main

2. Wait for startup
   psql -U postgres -c "SELECT version();"
   (Should return version info)

3. Verify replication
   psql -U postgres -c "SELECT slot_name, restart_lsn FROM pg_replication_slots;"
   (Should show active slots)

4. Verify backups
   du -h /var/lib/postgresql/backup/
```

**Phase 2: Core Applications (10–15 minutes)**

```
1. Start AMS (Account Management)
   kubectl apply -f /deployments/ams-deployment.yaml
   kubectl rollout status deployment/ams --timeout=5m

2. Start PPS (Payment Processing)
   kubectl apply -f /deployments/pps-deployment.yaml
   kubectl rollout status deployment/pps --timeout=5m

3. Start FDE (Fraud Detection)
   kubectl apply -f /deployments/fde-deployment.yaml
   kubectl rollout status deployment/fde --timeout=5m

4. Verify core services healthy
   kubectl get pods -n production | grep Running
   (Should see all pods in "Running" state)
```

**Phase 3: Customer-Facing (5–10 minutes)**

```
1. Start IBP (Internet Banking)
   kubectl apply -f /deployments/ibp-deployment.yaml
   kubectl rollout status deployment/ibp --timeout=5m

2. Start MBA (Mobile App backend)
   kubectl apply -f /deployments/mba-deployment.yaml
   kubectl rollout status deployment/mba --timeout=5m

3. Clear cache
   redis-cli FLUSHALL

4. Test endpoints
   curl https://api.firstuk.bank/health
   (Should return "status": "ok")
```

**Phase 4: Verification (5 minutes)**

```
1. Run smoke tests
   bash /tests/smoke_tests.sh
   Expected: All tests PASS

2. Verify dashboard
   Check https://dashboard.firstuk.bank
   Expected: All systems Green

3. Notify team
   Post to #operations-channel: "Systems startup complete"
```

**If Any Phase Fails:** Go to Step 6 (Incident Response) — Do NOT proceed to next phase

---

## 3. SYSTEM SHUTDOWN (Maintenance Only)

**When to Use:** Scheduled maintenance, controlled shutdown

### Shutdown Sequence

**Phase 1: Notify Customers**

```
1. Post maintenance notice
   - Website banner: "System maintenance 02:00–03:00 AM"
   - SMS alert to all customers (optional)
   - Check: Maintenance window during low-traffic time
```

**Phase 2: Graceful Shutdown (20 minutes)**

```
1. Stop accepting new transactions
   kubectl scale deployment ibp --replicas=0
   kubectl scale deployment mba --replicas=0

2. Wait for in-flight transactions to complete
   kubectl logs -f deployment/pps | grep "COMPLETE"
   (Monitor for ~10 minutes)

3. Stop payment processing
   kubectl scale deployment pps --replicas=0

4. Stop application servers
   kubectl scale deployment ams --replicas=0
   kubectl scale deployment fde --replicas=0

5. Backup database
   /scripts/backup_database.sh

6. Stop database
   systemctl stop postgresql@main
```

**Phase 3: Notify Completion**

```
Post to #operations: "System shutdown complete"
```

---

## 4. COMMON ISSUES & TROUBLESHOOTING

### Issue: "AMS Service Unavailable"

**Symptoms:** API returns 503, IBP shows error

**Steps:**
```
1. Check service status
   kubectl get pod -n production | grep ams

2. Check logs
   kubectl logs -f deployment/ams | tail -50

3. Check database connection
   curl https://api.firstuk.bank/ams/health/db

4. If DB connection issue
   Verify PostgreSQL running: systemctl status postgresql@main
   Verify connection pool: psql -U postgres -c "SELECT count(*) FROM pg_stat_activity WHERE datname='firstuk_prod';"

5. Restart service
   kubectl rollout restart deployment/ams
   kubectl rollout status deployment/ams --timeout=5m

6. Verify recovery
   curl https://api.firstuk.bank/accounts -H "Authorization: Bearer [token]"
   (Should return account list)
```

### Issue: "Payment Processing Slow"

**Symptoms:** FPS transfers taking >15 minutes, BACS delayed

**Steps:**
```
1. Check payment queue
   curl https://api.firstuk.bank/pps/queue-length

2. If queue growing
   Monitor: kubectl logs -f deployment/pps | grep "pending"

3. Check if database is bottleneck
   psql -U postgres -c "SELECT count(*) FROM pg_stat_activity WHERE wait_event_type='IO';"

4. If high I/O wait
   Increase database resources: kubectl set resources deployment/ams --limits=memory=4Gi,cpu=2

5. Monitor recovery
   Watch queue decrease: watch 'curl https://api.firstuk.bank/pps/queue-length'

6. If not improving
   Escalate to database team (see Step 6)
```

### Issue: "High Fraud Alert Rate"

**Symptoms:** Many legitimate transactions flagged as fraud, customer complaints

**Steps:**
```
1. Check FDE alert rate
   kubectl logs -f deployment/fde | grep "ALERT" | wc -l
   (Compare to baseline: <10 alerts/hour)

2. Review recent alerts
   tail -100 /var/log/fde/alerts.log | grep -A5 "false_positive"

3. If alert rate >3x normal
   Check for DDoS/attack: kubectl get events -n production

4. Temporarily loosen rules
   Edit /config/fraud_rules.yaml, restart FDE
   Notify Financial Crime team

5. Review with team
   Schedule urgent meeting to review false positives

6. Restore to normal settings
   Once resolved, revert rule changes
```

---

## 5. INCIDENT RESPONSE FLOWCHART

```
Issue Detected
    ↓
Severity Assessment
    ├─ Critical (System Down) → IMMEDIATE ESCALATION
    ├─ High (Partial Outage) → Escalate within 1 hour
    └─ Low (Degraded) → Escalate within 4 hours
    ↓
Investigate (See Troubleshooting)
    ├─ Issue Identified → Apply Fix
    └─ Issue Not Found → Escalate to Team
    ↓
Implement Fix
    ├─ Rollback? → Roll back deployment
    ├─ Restart? → Restart service
    └─ Config Change? → Update and reload
    ↓
Verify Recovery
    ├─ PASS → Close incident, document
    └─ FAIL → Escalate to team lead
    ↓
Document Incident
    - Record in incident log
    - Timeline of actions
    - Root cause analysis
    - Prevention measures for future
```

---

## 6. ESCALATION CONTACTS

**For issues requiring escalation:**

| Severity | Team | Contact | Phone |
|----------|------|---------|-------|
| Critical | On-Call Engineer | oncall@firstuk.bank | 0800-555-0911 |
| High | Technology Manager | tech-manager@firstuk.bank | ext. 5000 |
| Medium | Database Team | db-team@firstuk.bank | ext. 5010 |
| Low | Infrastructure | infra-team@firstuk.bank | ext. 5020 |

**Escalation Criteria:**
- Critical: System completely down or major data loss
- High: Partial outage affecting customers
- Medium: Performance issue or non-critical system
- Low: Minor issue, system functioning

---

## 7. MONITORING DASHBOARDS

**Primary Dashboard:** https://dashboard.firstuk.bank

**Key Metrics to Monitor:**

| Metric | Normal Range | Alert Threshold |
|--------|---|---|
| API Response Time (p95) | <500ms | >2s |
| Database Connections | <100 | >200 |
| Error Rate | <0.1% | >1% |
| Memory Usage | <70% | >85% |
| Disk Usage | <60% | >80% |
| Payment Success Rate | >99.9% | <99% |
| Fraud Alert Rate | <10/hour | >30/hour |

**Check Dashboard:** Every hour during business hours, every 4 hours overnight

---

## 8. BACKUP & DISASTER RECOVERY

### Backup Status

**Daily Backup:** 23:59 UTC (automated)  
**Location:** AWS S3 (encrypted, multi-region)  
**Retention:** 30 days daily, 1 year weekly  
**Verification:** Automated restore test weekly  

### Recovery Procedures

**Recovery Point Objective (RPO):** 15 minutes  
**Recovery Time Objective (RTO):** 2 hours  

**To Restore from Backup:**
```
1. Contact Infrastructure team
2. Provide target restore time
3. Team restores from backup point
4. Verify data integrity
5. Bring systems online
6. Notify stakeholders
```

---

## 9. MAINTENANCE WINDOWS

**Scheduled Maintenance:** Tuesdays 02:00–04:00 AM UTC

**Activities:**
- Database patching
- OS updates
- Application updates
- Network maintenance

**Advance Notice:** Posted 1 week prior  
**Customer Impact:** Minimal (off-peak hours)

---

## 10. CONTACT & ESCALATION

**24/7 On-Call Support:**
- Email: oncall@firstuk.bank
- Phone: 0800-555-0911
- Slack: #incident-response

**Business Hours Support:**
- Help Desk: ext. 5000
- Technology Lead: tech-lead@firstuk.bank

---

## Document Version History

| Version | Date | Change | Author |
|---------|------|--------|--------|
| 1.2 | 2026-01-15 | Updated procedures, added dashboards | Infrastructure Mgr |
| 1.1 | 2025-07-01 | Added troubleshooting section | Infrastructure Mgr |
| 1.0 | 2025-01-01 | Initial runbook | Infrastructure Mgr |

---

## Sign-Off

**Approved by:**  
Chief Technology Officer — **Date: 2026-01-15**  
Infrastructure Manager — **Date: 2026-01-15**

---

**Last Updated:** January 15, 2026

This runbook must be kept current. Updates required when systems change.

---
