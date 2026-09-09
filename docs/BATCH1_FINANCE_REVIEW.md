# Batch 1: finance controls and non-cash complimentary services

Status: Batch 1.1 implemented locally; PostgreSQL validation remains a release blocker. No deployment, commit, push, production query or production mutation was performed. Batch 2 has not started.

## Files

Backend (relative to backend):
- `finance/models.py`
- `finance/services.py`
- `finance/complimentary.py` (new)
- `finance/serializers.py`
- `finance/views.py`
- `finance/urls.py`
- `finance/migrations/0019_complimentary_and_reversal_controls.py` (new)
- `finance/test_complimentary.py` (new)
- `finance/test_batch1_controls.py` (new)
- `finance/test_sponsorship_dashboard.py`
- `docs/BATCH1_FINANCE_REVIEW.md` (this file)

Frontend (relative to frontend):
- `app/ops/finance/page.tsx`
- `app/ops/finance/complimentary/page.tsx` (new)
- `app/ops/finance/complimentary/ComplimentaryManager.tsx` (new)
- `app/ops/finance/transfers/TreasuryTransferManager.tsx`

## Lifecycle

An exact internal finance operator submits a complimentary request against an existing, fully unpaid, priced financial record. Creation snapshots the existing price and allocations; it does not price or fund the encounter. A different exact internal finance approver can approve or reject. An operator can cancel a pending request. Retries return the existing result without another event or finance posting. A reused idempotency key with changed intent is rejected.

Approval retains standard/list gross value (tested at NGN 15,000), sets outstanding and allocated amounts to zero, sets payer/payment method to waived and collector to none, clears payer/collecting organisations, and makes the financial record ready for release. `captured_at` stays empty. Existing pending allocation rows are retained as reversed, with their prior values preserved in the request and approval audit. No reservation, capture, ledger entry, new encounter or new financial record is created.

An approved disposition is final in this batch. No endpoint cancels or reverses it. Corrections that could reinstate debt require a separately reviewed future workflow. Generic finance cancellation, credit approval and forced repricing reject it. Lifecycle synchronization and allocation/service-partner earning do not generate income from it. A database check constraint rejects incompatible cash/outstanding states.

Existing active sponsorships, reservations, allowances, earned allocations, settlements and service-partner earnings block approval. Cancelled sponsorships with released reservations are supported. External beneficiaries and service-session-linked cases are conservatively excluded pending a separate payable review. This pathway does not settle or extinguish an external provider's entitlement.

Original encounter payment responsibility remains provenance; the financial record's explicit `disposition=complimentary_non_cash` and waived payer/payment fields represent the approved financial decision.

## Controls reused and corrected

- Existing append-only ledger, financial audit log, exact internal finance roles, organisation/branch controls and Treasury sponsorship behavior are retained.
- Nullable joined locks use `of=("self",)` in allowance reservation, financial credit approval, sponsorship capture and service-session attachment. Treasury reversal now uses separate unjoined row locks. See the Batch 1.1 lock table below.
- Settlement preparation serializes on the beneficiary organisation and avoids `DISTINCT` on its locking query. Repeat approval/payment is idempotent; a different reference after payment is rejected. External settlement payment still posts no Treasury wallet receipt.
- Treasury reversal now requires a draft/submission/approval/execution FinanceActionRequest lifecycle, a separate approver from requester and original creator/executor, reason, unique independent reference and PDF/PNG/JPEG evidence (maximum 10 MB). It distinguishes actual returned funds from correction of an incorrectly recorded original debit. Both require human evidence review; the system does not verify bank receipt automatically. It preserves the original evidence and stores separate reversal evidence, ledger metadata and audit events.

## Migration

`0019_complimentary_and_reversal_controls` adds financial disposition (default `standard`), complimentary requests/events, reversal type/reference/evidence, and constraints for positive list value, one active request per financial record, checker separation, one event per transition, non-cash financial invariants and unique nonempty reversal references.

It does not classify existing encounters, alter wallet amounts, create finance transactions or rewrite old migrations. Existing transfer reversals retain blank new metadata; no evidence is invented for history. The migration was exercised only while creating disposable test databases, not against the application or production database.

## Endpoints

- `GET/POST /api/finance/complimentary/`
- `GET /api/finance/complimentary/{id}/`
- `POST /api/finance/complimentary/{id}/approve/`
- `POST /api/finance/complimentary/{id}/reject/`
- `POST /api/finance/complimentary/{id}/cancel/`
- Existing `POST /api/finance/treasury-transfers/{id}/reverse/` is disabled and returns a validation error without posting. Use `POST /api/finance/treasury-transfers/{id}/reversal-requests/`, then `/api/finance/action-requests/{id}/submit/`, `/approve/`, and `/execute/`. Pending requests can be rejected; draft/pending requests can be cancelled.
- `GET /api/finance/treasury-transfers/{id}/reversal-evidence/` is protected by internal finance viewing permissions.

## Original Batch 1 validation (superseded by Batch 1.1 results below)

- `python manage.py test finance --noinput`: 184 tests, 183 passed, 1 PostgreSQL-only test skipped; 11.869 seconds of test execution.
- `python manage.py check`: passed, no issues.
- `python manage.py makemigrations --check --dry-run`: passed, no changes detected.
- `npm run build`: passed after allowing the existing Google Fonts build dependency to download. No deployment performed.
- `npx tsc --noEmit`: passed.
- ESLint on all four changed frontend files: passed.
- Both repositories' `git diff --check`: passed (Git reports line-ending normalization warnings).
- Repository-wide lint: 67 errors and 21 warnings in unchanged files. All four changed frontend files have zero errors and zero warnings.
- Final `python manage.py test finance.test_batch1_controls --noinput` after adding reversal-evidence access coverage: 5 tests, 4 passed, 1 PostgreSQL-only test skipped, 0.816 seconds.

The new tests cover NGN 15,000 list value with NGN 97,140 unchanged Treasury cash, no wallet/record duplication, request/decision retries, exact permissions, self-approval denial, cancellation/rejection, immutable audit events, stale pricing, external allocations, paid encounters, blocked repricing/reservation/credit/cancellation, released-sponsorship compatibility, database invariants, reason-only reversal denial, evidenced/idempotent correction, creator/executor separation and settlement entitlement versus cash. Existing paid and Treasury sponsorship tests were retained and passed; the old reversal test now supplies required evidence.

Warnings: the finance suite reports a missing local staticfiles directory and existing service-session snapshot warnings emitted by report synchronization. No PostgreSQL runtime, Docker or installed WSL was available, so PostgreSQL execution/concurrency is not certified by this run. An isolated PostgreSQL test run remains a release requirement. No historical-report transitions were changed in Batch 1; their transition regression tests belong to Batch 2. Full cross-application validation remains due before release of the complete batch programme.

## Later authorised use for encounter 54 (not executed)

After separately approved deployment/migration and PostgreSQL validation:
1. Read the encounter and existing financial record; confirm encounter 54 maps to record 34, gross NGN 15,000, fully unpaid, and no captured cash or earned/settled/external entitlement. Confirm the cancelled sponsorship's reservation is released. If any precondition differs, stop for review; do not reprice or manufacture funding.
2. An internal finance operator opens `/ops/finance/complimentary`, selects the existing encounter and submits the documented complimentary-service reason. The equivalent service is `request_complimentary(financial_record=existing_record, actor=operator, reason=reason, idempotency_key=unique_key)`.
3. A different internal finance approver reviews the record and approves through the same page, or `decide_complimentary(request, actor=approver, action="approve")`.
4. Read back the existing record and audit: list value NGN 15,000; outstanding zero; financially releasable; non-cash disposition; no capture. Compare all wallet balances, ledger/reservation counts and allocation IDs before/after. Treasury available/reserved amounts must be unchanged by this action.

Do not use a top-up, Treasury sponsorship, wallet transfer, historical-finance import or direct field update to perform this classification. This procedure must not be run before deployment approval.


## Batch 1.1 scope and migration

The restored Batch 1 work was preserved. No production database was queried or changed, and no commit, push or deployment occurred. Clinical asset code and the repository `.clinical_assets` directory were not edited. Batch 2 has not started.

Files changed during Batch 1.1: `finance/models.py`, `finance/services.py`, `finance/serializers.py`, `finance/views.py`, `finance/tests.py`, `finance/test_sponsorship_dashboard.py`, `finance/test_batch1_controls.py`, `finance/test_complimentary.py`, this document; new `finance/treasury_reversals.py`, `finance/test_finance_hardening.py`, `finance/test_postgresql_controls.py`, `finance/migrations/0020_treasury_reversal_workflow_and_settlement_integrity.py`; frontend `types/finance.ts` and `app/ops/finance/transfers/TreasuryTransferManager.tsx`. Other Batch 1 files listed above remain part of the combined WIP.

0019 is unchanged: SHA-256 `32B20888242B73FC6B55BCECD3502E02D2E9A6E2886BAB1F99D9B81311FF646F`.

0020 adds FinanceActionRequest Treasury link, reversal kind, approved snapshot, execution actor/time; extends type/status choices; adds one active reversal per transfer, one active reversal reference, positive-amount/transfer-required constraints; and adds unique `(batch, allocation)` SettlementItem membership. It has no RunPython/data conversion/balance updates and retains old finance-action statuses. Existing duplicate memberships will cause the new uniqueness constraint to fail; they must be reviewed before deployment, never silently deleted by migration.

## Settlement authority and integrity

`create_settlement_batch`, `mark_settlement_batch_paid` and `cancel_settlement_batch` require `has_internal_finance_role(actor, "operator")`; `approve_settlement_batch` requires `"approver"`. Both the internal Sentinel staff marker and exact group are mandatory. None, root alone, clinic roles, external users with finance groups and the wrong finance role fail before any mutation. Adding an approver role to a preparer does not permit self-approval.

Preparation holds the beneficiary organisation row, then locks eligible earned allocations in primary-key order in one transaction. It materializes the selection once. Active draft/approved/paid memberships are excluded. Approval/payment lock the batch; retries retain the first approval/payment result. A paid retry with a different reference fails. Payment also locks financial records in primary-key order before changing allocation status. Earned allocations and settlement bookkeeping never become wallet receipts.

Unique `(batch, allocation)` is database-enforced, including direct inserts. A portable partial unique constraint cannot depend on `SettlementItem.batch.status`: this is a joined cross-row condition. No unsafe trigger/raw SQL was introduced. Active cross-batch exclusivity is enforced by the controlled preparation service's beneficiary lock and selection, not a database-wide guarantee against direct ORM/SQL writes. Cancelled membership remains historical and replacement membership is permitted. Uncontrolled inserts must not be used to prepare settlement batches.

## Final reviewed lock order

All below run within transaction.atomic(). `of=("self",)` locks only the primary table, excluding nullable joined rows from PostgreSQL FOR UPDATE. An unjoined lock cannot encounter the nullable outer-join restriction. Reading a relation later is not equivalent to locking it.

| Function/path | Locks and joins | Nullable relation / safety |
| --- | --- | --- |
| Complimentary request/approval | Financial record -> complimentary request -> allocations | Unjoined locks. Decision checks price/allocation snapshots while holding the record. |
| decide_encounter_sponsorship | Financial record -> sponsorship -> wallet; reserve helper reuses held record/wallet | Joined sponsor wallet, organisation, record, encounter are required; sponsorship uses `of=("self",)` and record/wallet are separately locked. |
| capture_encounter_sponsorship | Financial record -> sponsorship -> reservation -> wallet | Sponsorship.reservation nullable; sponsorship uses `of=("self",)`. |
| cancel_encounter_sponsorship | Financial record -> sponsorship -> reservation -> wallet when releasing | Nullable reservation excluded by `of=("self",)`. Draft cancellation has no reservation to lock. |
| capture_financial_record_wallet_reservation | Financial record -> sponsorship -> reservation -> wallet | Same order as direct capture. Record serializes the direct/generic entry points. |
| reserve_wallet_funds | Financial record -> wallet -> newly created reservation | Unjoined record/wallet locks. |
| capture_wallet_reservation / release_wallet_reservation | Financial record -> existing reservation -> wallet | Required wallet/record joins read with `of=("self",)`; wallet separately locked. |
| create_settlement_batch | Beneficiary organisation -> allocations ordered by PK | Required financial-record filter join; exclusion uses subquery. `of=("self",)`, no DISTINCT. |
| approve/cancel settlement | Settlement batch -> allocation updates | Unjoined batch lock; SQL updates lock affected allocations. |
| pay settlement | Settlement batch -> financial records ordered by PK -> allocation updates | Prefetch is a separate read, not a nullable locking join. |
| Treasury reversal request/transitions | Treasury transfer -> FinanceActionRequest -> wallet at execution -> founder expense when applicable | Unjoined locks; nullable original ledger/founder relations are read separately. Only execution needs the wallet lock. |
| approve_finance_action_request, legacy record correction | Financial record if present -> request -> wallet -> allocation updates | Nullable financial_record and related_entry are read using `of=("self",)`; record and wallet explicitly locked. Treasury type dispatches to the dedicated lifecycle first. |
| reserve_service_allowance | Financial record -> allowance reservation -> allowances | Nullable contract/payer joins restricted with `of=("self",)`. |
| approve_financial_record_credit | Financial record | Nullable contract join restricted with `of=("self",)`. |
| recognize_service_partner_earning | Financial record | Nullable encounter.service_session join restricted with `of=("self",)`. |
| attach_encounter_to_service_session | Service session -> encounter | Nullable branch/partner excluded by `of=("self",)`. |

This table describes the affected paths, not a claim that every legacy finance service has been redesigned. PostgreSQL runtime/deadlock behavior must still be verified with the isolated backend-specific tests.

## Treasury reversal request lifecycle

1. Internal operator creates an immutable draft FinanceActionRequest of type `treasury_reversal` against an executed, unreversed transfer. Required: returned_funds OR bookkeeping_correction, independent reference distinct from original payment, reason, PDF/PNG/JPEG evidence (matching file signature, <=10 MB), idempotency key. Full original amount/currency/wallet/debit are taken from the persisted transfer, never a client amount.
2. Internal operator submits draft -> pending. No wallet posting.
3. Internal approver independently reviews evidence, then approves pending -> authorized. Checker cannot be requester or original transfer creator/executor. Approval stores amount/currency/wallet/original debit, reason/kind/reference, evidence storage identity and SHA-256, maker/checker and original actor details. No wallet posting.
4. A separate internal operator executes authorized -> executed. Evidence is re-read and its hash and all approved details are compared. Only then does one positive reversal ledger entry restore the evidenced amount, linked to the original debit and request. Original transfer becomes reversed. An executed founder reimbursement returns its payable to approved with an audit event. Retry returns the original posted entry.
5. Draft/pending cancellation requires an operator and reason. Pending rejection requires an independent approver and reason. Both retain history and post nothing. A replacement can be requested after rejection/cancellation. Approved requests cannot be edited, cancelled or self-executed; executed/closed rows cannot be reopened through model save or services.

Each transition appends FinanceControlAudit and TreasuryTransferEvent. Execution ledger metadata retains the approved snapshot. Direct old reversal is disabled. Generic corrections identifying an original Treasury debit are rejected and Treasury action approval/rejection delegates to this lifecycle. Both reversal kinds increase recorded available Treasury cash ONLY at execution: returned_funds requires evidence of the actual return; bookkeeping_correction requires evidence that the original debit overstated an actual outflow. A reason, bank balance elsewhere, clinic wallet balance, or earned allocation is never sufficient. The software validates presence/integrity of evidence; independent finance review must establish its truth. Evidence files require protected storage: the hash detects replacement before execution, but is not a WORM storage guarantee against administrators/raw storage access. The approval snapshot and append-only ledger preserve the exact approved digest.

## Batch 1.1 tests and release gates

New regressions explicitly call settlement preparation after complimentary approval and assert no membership/cash movement and unchanged NGN 15,000 list value. Hardening tests exercise direct-service authority, wrong roles and missing marker, maker-checker, same-batch database uniqueness, cancelled replacement, retries/reference mismatches, request transitions, reason-only/evidence-less denial, evidence tampering, terminal immutability, generic bypass rejection and API workflow. Existing tests now use authorized settlement actors and the controlled reversal lifecycle.

PostgreSQL tests cover complimentary request/approval/settlement exclusion, sponsorship approval/capture/cancellation/release, nullable credit/allowance/session joins, allowance funding/capture, settlement create/approve/pay, reversal lifecycle, concurrent settlement preparation, concurrent direct/generic capture, and concurrent reversal execution. SQLite skips these tests; a skipped PostgreSQL test is not validation of PostgreSQL.

Release checklist (Batch 1 only):

- Run the complete finance suite, including `finance.test_postgresql_controls`, against an isolated PostgreSQL database using the production major version and a test-only role allowed to create/drop its disposable test database. Confirm no skips and no SQL/deadlock errors.
- Read-only preflight for duplicate memberships: `SettlementItem.objects.values("batch_id", "allocation_id").annotate(n=Count("id")).filter(n__gt=1)`. Also inspect allocations with multiple draft/approved/paid memberships. Stop for a separately reviewed correction if any exist.
- Confirm a backup and rollback plan, exact operator/checker accounts, and protected evidence storage. Drain finance writes before schema/application rollout so old workers cannot execute the retired direct reversal path during deployment.
- Apply 0019 if not already applied, then additive 0020; deploy backend and frontend together; restart workers on the new code before resuming finance writes. Never roll back to code exposing the old direct reversal after enabling the new workflow.
- Post-deploy read-only checks: applied migration names; internal role capabilities; no duplicate active memberships/reversal requests; executed reversals have one matching posted entry/snapshot and distinct checker; pre/post wallet ledger totals unchanged by migration; list/retrieve/evidence routes work for authorized users only.
- Only after separate production approval, use the later encounter-54 complimentary procedure above. Reconfirm the current existing record identity; the previously reported record 34 mapping has not been verified in this pass. Expected result: list value NGN 15,000; patient owes 0; clinic owes 0; outstanding 0; financially releasable true; existing allocations reversed, no earned payable; no new encounter/financial record/reservation/capture/ledger row; Treasury cash and wallet balances unchanged. No clinic-to-Treasury transfer is part of this procedure.

Production readiness: NO until isolated PostgreSQL execution and deployment preflight succeed. No production procedure was run.


## Final Batch 1.1 validation — 8 September 2026

Validation used `backend/.venv/Scripts/python.exe` (Django 6.0.3). Commands ran with a temporary settings module importing `config.settings.local` and overriding only database/media/private-clinical-asset/bulk-staging paths to disposable local storage. Business rules, permissions, password hashing and test assertions were not weakened. No application/production migration was applied.

| Requested check | Final result |
| --- | --- |
| `python manage.py check` | PASS; no issues, zero silenced. |
| `python manage.py makemigrations --check` | PASS; no changes detected after creating 0020. |
| `python manage.py test finance.tests --noinput` | PASS; 100 tests, 100 passed, 0 skipped/errors/failures; 26.953 seconds. |
| `python manage.py test --noinput` | PASS; 397 tests, 382 passed, 15 skipped, 0 failures/errors; 455.329 seconds. |
| Frontend `npm run build` | PASS with network access for existing Google Fonts; TypeScript completed. Initial restricted-network attempt failed fetching Geist/Geist Mono. No application change was made to bypass that dependency. |
| Backend and frontend `git diff --check` | PASS. Git emits LF/CRLF normalization notices, not whitespace errors. |
| Isolated PostgreSQL execution | NOT RUN / UNVALIDATED. No configured isolated PostgreSQL database, local PostgreSQL tools, Docker, or listener on the default local port was found. All 15 skips are PostgreSQL-only: 14 new lock/concurrency cases plus the existing Batch 1 case. |

An earlier focused run had 41 tests, 40 passed and 1 PostgreSQL skip (12.970 seconds). The final full run includes the subsequently added hardening cases and current implementation.

The full run emitted existing missing-staticfiles and immutable service-session synchronization warnings. A premature hospital release was rejected and an injected upload database-failure diagnostic was logged by negative-path tests; neither produced a failed test. These diagnostics were not silenced or worked around by weakening controls.

No commits, pushes, deployments, production data reads/writes, encounter-54 changes, or Batch 2 work were performed. Batch 1 is NOT production-ready until isolated PostgreSQL tests and read-only deployment preflight pass. The active cross-batch constraint limitation and evidence-authenticity/storage limitations above remain explicit.
