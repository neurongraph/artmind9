# FirstUK Bank — SMS Alert Template Suite

## Metadata

| Field | Value |
|-------|-------|
| Document ID | SMS-TEMPLATE-001 |
| Version | 1.0 |
| Effective Date | 2026-01-15 |
| Review Date | 2027-01-15 |
| Owner | Head of Marketing |
| Department | Marketing, Customer Service |
| Status | Active |
| Classification | Internal |
| Audience | Customer Service Staff, Operations |
| Related Documents | [[email_templates]], [[sop_account_opening]] |

---

## Purpose

Provide concise SMS message templates for time-sensitive customer alerts and notifications while respecting character limits (160 chars per SMS).

---

## Template 1: Large Withdrawal Alert

**Trigger:** Withdrawal >£1,000 from mobile/ATM

```
FirstUK: Large withdrawal of £[amount] from your account [last 4 
digits]. If not you, call 0800-555-2265 immediately. Ref: [code]
```

*Character count: ~120*

---

## Template 2: Transfer Sent Confirmation

**Trigger:** Successful transfer completion

```
FirstUK: Transfer of £[amount] to [recipient name] completed. 
Your balance: £[new balance]. Call 0800-555-2265 with questions.
```

*Character count: ~110*

---

## Template 3: Failed Direct Debit Alert

**Trigger:** Direct Debit rejected (insufficient funds, etc.)

```
FirstUK: Direct Debit of £[amount] to [biller] failed on [date]. 
Reason: [insufficient funds/other]. Add funds to retry. 
Call 0800-555-2265.
```

*Character count: ~125*

---

## Template 4: Card Payment Alert

**Trigger:** Contactless or online payment >£500

```
FirstUK: Card payment of £[amount] at [merchant] on [date] [time]. 
If not you, call 0800-555-2265 fraud line immediately.
```

*Character count: ~115*

---

## Template 5: Suspicious Activity Alert

**Trigger:** Fraud detection rule triggered (moderate risk)

```
FirstUK: Unusual activity detected on your account. Call 
0800-555-2265 to confirm. For security, we may restrict 
transactions. Reply CONFIRM or call immediately.
```

*Character count: ~145*

---

## Template 6: Card Lost/Blocked Alert

**Trigger:** Card blocked due to fraud or loss

```
FirstUK: Your [product] card has been blocked. Use online banking 
or visit a branch. Order replacement: Call 0800-555-2265 or online 
at www.firstuk.bank
```

*Character count: ~135*

---

## Template 7: Password Reset Initiated

**Trigger:** Password reset requested

```
FirstUK: Password reset requested. Click link to create new password: 
[short URL]. Expires in 24h. Ignore if you didn't request this.
```

*Character count: ~125*

---

## Template 8: 2FA Code (One-Time)

**Trigger:** Two-Factor Authentication required

```
FirstUK: Your security code is [6-digit code]. Never share this. 
Code expires in 5 minutes. If you didn't request this, call 
0800-555-2265.
```

*Character count: ~130*

---

## Template 9: Account Opened Confirmation

**Trigger:** New account successfully created

```
FirstUK: Welcome! Your SmartSaver account is open. Account: 
[ACC-XXXXX]. Log in at www.firstuk.bank/login to activate. 
Call 0800-555-2265 for help.
```

*Character count: ~135*

---

## Template 10: Interest Payment Notification

**Trigger:** Monthly interest credit

```
FirstUK: Interest of £[amount] credited to your account. 
New balance: £[balance]. At 4.50% AER. Questions? Call 
0800-555-2265
```

*Character count: ~115*

---

## Template 11: Standing Order Executed

**Trigger:** Recurring standing order payment processed

```
FirstUK: Standing order of £[amount] to [payee] processed on 
[date]. Your balance: £[balance]. Call 0800-555-2265 with questions.
```

*Character count: ~130*

---

## Template 12: Account Verification Needed

**Trigger:** KYC documents required

```
FirstUK: We need your ID to complete verification. Upload at 
www.firstuk.bank/kyc or call 0800-555-2265. Deadline: [date]. 
Your account will be restricted if not completed.
```

*Character count: ~160*

---

## Template 13: Rate Change Alert

**Trigger:** Interest rate updated (usually monthly)

```
FirstUK: Your rate changed to [new rate]% AER (was [old rate]%). 
Bank of England base rate moved to [new BoE rate]%. Your new 
interest starts next month.
```

*Character count: ~145*

---

## Template 14: Fraud Claim Resolved

**Trigger:** Disputed transaction investigated and resolved

```
FirstUK: Your dispute claim (Ref: [EXC-XXXXX]) has been resolved. 
Refund of £[amount] processed. See email for full details. 
Call 0800-555-2265 with questions.
```

*Character count: ~150*

---

## Template 15: Branch Appointment Reminder

**Trigger:** Scheduled branch meeting (24h before)

```
FirstUK: Reminder: You have an appointment at [branch name] on 
[date] at [time]. Contact 0800-555-2265 if you need to reschedule.
```

*Character count: ~140*

---

## Template 16: Account Closure Confirmation

**Trigger:** Account closure processed

```
FirstUK: Your account has been closed (Ref: [ACC-XXXXX]). 
Final balance of £[amount] will be transferred to [method] within 
3–5 days. Call 0800-555-2265 with questions.
```

*Character count: ~150*

---

## Template 17: Maintenance Notice

**Trigger:** Scheduled system maintenance

```
FirstUK: Scheduled maintenance 02:00–03:00 AM on [date]. 
Online/mobile banking temporarily unavailable. Sorry for any 
inconvenience. We'll be back soon!
```

*Character count: ~150*

---

## Template 18: Survey Request (Optional)

**Trigger:** Post-transaction or post-support

```
FirstUK: How did we do? Rate your experience: www.firstuk.bank/survey 
Takes 1 minute. Thanks for banking with us!
```

*Character count: ~110*

---

## Template 19: Unrecognized Login Alert

**Trigger:** Login from new device or location

```
FirstUK: Login attempt on [date] [time] from [location/device]. 
If not you, change password immediately: www.firstuk.bank/reset or 
call 0800-555-2265 fraud line.
```

*Character count: ~160*

---

## Template 20: Payment Reversal Notification

**Trigger:** Transaction reversed/refunded

```
FirstUK: Transaction of £[amount] to [recipient] has been reversed. 
Refund credited to your account. Ref: [EXC-XXXXX]. 
Call 0800-555-2265 if you have questions.
```

*Character count: ~150*

---

## SMS Guidelines

**Character Limits:**
- Standard SMS: 160 characters
- Multi-part SMS: 153 characters per part (if needed)
- Keep under 160 where possible to avoid multi-part charges

**Tone:** Urgent yet professional, concise, action-oriented

**Critical Elements:**
- Always include phone number for support
- Include reference/confirmation codes when possible
- Clear action items (approve, confirm, call, click, etc.)
- Never ask for sensitive info via SMS (PIN, full account number)

**Do NOT Include in SMS:**
- Full account numbers (use last 4 digits only)
- Passwords or PINs
- Detailed personal information
- Long URLs (use shorteners)

**Opt-Out:**
All SMS templates should include note that customers can opt out 
of non-critical alerts via online banking settings.

---

## Document Version History

| Version | Date | Change | Author |
|---------|------|--------|--------|
| 1.0 | 2026-01-15 | Initial SMS templates | Head of Marketing |

---
