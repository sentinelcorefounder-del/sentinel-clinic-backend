# Service-partner payables setup

After deployment, an internal finance administrator must create the real service partner through the restricted finance administration area. No production partner is seeded by migration.

1. Create the non-login service-partner organisation using its confirmed legal and display identity.
2. Configure each relevant assessment session with that organisation as the camera/team provider.
3. Confirm the session camera/team rate is `NGN 5000.00` (or the deliberately agreed session rate).
4. Activate the session before encounters are associated prospectively.

Do not attach historical encounters or run an automatic backfill. The provider, rate, currency and configuration version are frozen into each encounter’s delivery snapshot when it is prospectively attached.

## Refund and correction policy

A later customer, clinic, hospital, Paystack or wallet refund does not reverse a valid service-partner earning. When the partner genuinely performed the assessment, the report completed its applicable issue/release workflow, and Sentinel originally captured the revenue, the partner amount remains earned; the commercial refund is Sentinel’s responsibility.

Use the controlled service-partner correction workflow only when the underlying earning was invalid—for example a duplicate, wrong partner/session/provider attribution, invalid assessment, voided delivery, or an authorised snapshot-rate correction. An internal finance operator requests the correction and a different internal finance approver decides it. Approval posts an append-only negative adjustment without editing the original earning or any paid settlement. Unpaid invalid earnings reduce outstanding payables; corrections to paid earnings remain clearly carried forward and reduce a later positive settlement. The system never creates a zero or negative cash payment batch.
