# Open Banking Delivery Outcome

| Field | Value |
|---|---|
| Document ID | OB-OUTCOME-2026-07 |
| Reporting Date | 2026-07-10 |
| Owner | Chief Technology Officer |
| Status | Core delivery complete; limited exception under remediation |
| Regulatory Reference | FCA-OP-2026-01 |
| Related Documents | [[regulatory_circulars_2026]], [[systems]], [[policy_privacy]], [[board_risk_committee_minutes_q1_2026]] |

FirstUK met the 2026-06-30 core delivery date. OAuth 2.0 APIs provide consented account, transaction, standing-order, and direct-debit mandate data. Consent scope, expiry and revocation are recorded; API-access data is purged after 90 days unless renewed.

For 37 inactive legacy mandates, the API returns identifier and status but not a historical cancellation timestamp. Support provides the timestamp within one business day while migration completes by 2026-08-15. Compliance logged the exception on 2026-06-30; the CTO owns closure and reports it to the Board Risk Committee in September.
