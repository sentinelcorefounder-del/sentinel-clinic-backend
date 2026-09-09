# PostgreSQL lock audit â€” 2026-09-08

Scope: all backend Python lock calls, model relation nullability, inherited querysets, default ordering, filters, prefetches, and raw SQL searches. PostgreSQL settings: `config.settings.postgres_test`; disposable database: `test_sentinel_test` on port 5433.

## Changes made in this audit

Eight additional nullable-side locking failures were found. Each now uses `select_for_update(of=("self",))`; no business predicates, authorization checks, or workflow transitions were changed.

| Location | Nullable joined relations | Lock intent |
| --- | --- | --- |
| `reports/views.py:514` â€” structured report update | Patient assigned clinic; submitted/reviewed user in inherited queryset | Serialize the report edit/version check; clinical responsibility is locked by the existing service. |
| `reports/views.py:764` â€” clinic issuance | Patient assigned clinic | Serialize report issuance and version binding. |
| `reports/views.py:1050` â€” eye-health preview | Patient assigned clinic/branch; encounter service branch | Serialize the report preview checksum. |
| `reports/views.py:1100` â€” eye-health correction | Same scope relations, plus finalized version | Serialize draft/correction transition; finalized versions are immutable. |
| `reports/views.py:1197` â€” eye-health hospital release | Encounter hospital referral, source hospital, finalized version | Serialize report release; existing separate HospitalReferral lock remains. |
| `reports/eye_health.py:609` â€” finalization | Patient assigned clinic; encounter service branch | Serialize report finalization/version creation. |
| `ops/views.py:1096` â€” hospital release | Patient assigned clinic; encounter hospital referral | Serialize report release; existing separate HospitalReferral lock remains. |
| `payments/services/posting.py:18` â€” verified payment posting | Wallet and financial record | Serialize payment idempotency; wallet top-up already locks the wallet; encounter posting explicitly locks the financial record then collector wallet. |

PostgreSQL rejects the original unrestricted `FOR UPDATE` for these outer joins even when the particular row has non-null relations. The joined scope/reference rows do not need blanket locks. Existing explicit financial and referral locks remain in their original order.

Added one regression in `reports/test_eye_health_screening.py`: correcting a draft with a null finalized version returns HTTP 409 and creates no version. Existing workflow tests cover successful preview/finalization/correction/release, report editing/issuance, payment posting and idempotency.

`config/settings/postgres_test.py` was already untracked before this audit. It now isolates media, clinical assets and bulk staging under temporary storage, and uses a fast test-only password hasher. Database settings and PostgreSQL behavior are unchanged.

## Existing fixes verified and preserved

- Onward referrals: three service referral locks, the version/nullable recipient lock, and two view referral locks already restrict locking to the base row. Finalization explicitly locks its version and responsible optometrist; a superseded version is locked separately under the referral lock.
- Structured report submission already restricts its nullable clinic join to the report row.
- Finance record locks with nullable contracts, payer/origin organizations, or service sessions already use `of=self`.
- Sponsorship capture/cancellation join a nullable reservation but already use `of=self`. The financial record is locked first; reservation capture/release explicitly lock the reservation and wallet. Sponsorship approval explicitly locks its wallet and financial record.
- Finance action approval already restricts nullable financial-record/ledger-entry joins and explicitly locks the mutable financial record and wallet.
- Service-session view locks use the unannotated lock queryset; they do not lock the `Count(encounters)`/GROUP BY display queryset.
- Encounter scope locks and mobile image confirmation already restrict nullable clinic/branch joins to the base row.

## Suspected cases ruled out

- Both `BulkImageImport` confirmation queries join **non-nullable** service session, branch and organization fields. They use inner joins; retained as written. The plan's encounter/patient join is also non-nullable. Group item prefetch executes a separate query, not an outer join in the locking statement.
- Ops approval's report/encounter OneToOne is non-nullable: retained.
- Service-partner correction joins non-nullable earning, financial record, encounter and partner. It modifies the earning, so its existing related-row locks were retained.
- Finance action rejection, bank-transfer approval, treasury transfers and wallet/organization joins are non-nullable: retained.
- Wallet default ordering can introduce an organization join, but the FK is non-nullable. Other audited ordering fields do not introduce nullable locking joins.
- Allowance `contract__isnull`/contract-id predicates operate on the FK column; they do not require an outer join. Candidate ordering is explicit. Settlement exclusion uses a subquery; its locking query already uses `of=self`.
- Settlement prefetches are separate queries; financial records are explicitly locked in primary-key order before payment.
- Base-only get/filter/get-or-create queries, related-manager FK filters, and UPDATE statements do not introduce this nullable-side error. No additional raw `FOR UPDATE`, raw-query or extra-query locking paths were found.
- Referrals has no `select_for_update` calls of its own; its mutable release row is locked explicitly by report/Ops handlers.

## Validation

PostgreSQL EXPLAIN probes reproduced the exact nullable-side error for all eight original query shapes and accepted all eight corrected query shapes (8/8). These were planning-only probes against the disposable test database, without data changes. Completed targeted runs: release control 15/15; finance 100/100; sponsorship dashboard 17/17; PostgreSQL controls 14/14; eye health 13/13; payments 13/13. Onward referrals ran 21 tests (19 errors); bulk import ran 45 (19 errors). The failing runs retain their original connection-close errors. Full-suite and isolated diagnostic results are pending. Existing tests are not weakened or altered, other than the added regression above. Any original failing full-suite result is retained separately from diagnostic reruns.

## Complete lock-call inventory

133 calls: finance 90, reports 13, onward_referrals 9, encounters 6, uploads 6, ops 5, payments 3, patients 1. Entries marked unrestricted were inspected for joins and intended related-row locks and deliberately retained.

| Source | Function | Current lock scope |
| --- | --- | --- |
| `encounters/views.py:592` | `EncounterServicePackageCorrectionView.post` | base row (`of=self`) |
| `encounters/views.py:691` | `EncounterAssessmentLocationCorrectionView.post` | base row (`of=self`) |
| `encounters/views.py:1079` | `OcularAIReviewListCreateView.post` | unrestricted; safe joins/base query |
| `encounters/views.py:1080` | `OcularAIReviewListCreateView.post` | unrestricted; safe joins/base query |
| `encounters/views.py:1104` | `OcularAIReviewListCreateView.post` | unrestricted; safe joins/base query |
| `encounters/views.py:1242` | `OcularAIReviewListCreateView.post` | unrestricted; safe joins/base query |
| `finance/complimentary.py:64` | `request_complimentary` | unrestricted; safe joins/base query |
| `finance/complimentary.py:87` | `decide_complimentary` | unrestricted; safe joins/base query |
| `finance/complimentary.py:88` | `decide_complimentary` | unrestricted; safe joins/base query |
| `finance/complimentary.py:105` | `decide_complimentary` | unrestricted; safe joins/base query |
| `finance/management/commands/split_sentinel_treasury_clinic.py:96` | `Command.handle` | unrestricted; safe joins/base query |
| `finance/services.py:56` | `recognize_service_partner_earning` | base row (`of=self`) |
| `finance/services.py:158` | `prepare_service_partner_settlement` | unrestricted; safe joins/base query |
| `finance/services.py:171` | `prepare_service_partner_settlement` | unrestricted; safe joins/base query |
| `finance/services.py:204` | `decide_service_partner_settlement` | unrestricted; safe joins/base query |
| `finance/services.py:235` | `cancel_service_partner_settlement` | unrestricted; safe joins/base query |
| `finance/services.py:256` | `mark_service_partner_settlement_paid` | unrestricted; safe joins/base query |
| `finance/services.py:283` | `request_service_partner_correction` | unrestricted; safe joins/base query |
| `finance/services.py:311` | `decide_service_partner_correction` | unrestricted; safe joins/base query |
| `finance/services.py:549` | `create_finance_action_request` | unrestricted; safe joins/base query |
| `finance/services.py:585` | `approve_finance_action_request` | unrestricted; safe joins/base query |
| `finance/services.py:586` | `approve_finance_action_request` | base row (`of=self`) |
| `finance/services.py:597` | `approve_finance_action_request` | unrestricted; safe joins/base query |
| `finance/services.py:654` | `reject_finance_action_request` | unrestricted; safe joins/base query |
| `finance/services.py:714` | `price_encounter` | unrestricted; safe joins/base query |
| `finance/services.py:896` | `top_up_wallet` | unrestricted; safe joins/base query |
| `finance/services.py:918` | `submit_bank_transfer_proof` | unrestricted; safe joins/base query |
| `finance/services.py:941` | `verify_bank_transfer` | unrestricted; safe joins/base query |
| `finance/services.py:979` | `approve_bank_transfer` | unrestricted; safe joins/base query |
| `finance/services.py:1041` | `reject_bank_transfer` | unrestricted; safe joins/base query |
| `finance/services.py:1087` | `adjust_wallet` | unrestricted; safe joins/base query |
| `finance/services.py:1113` | `reserve_wallet_funds` | unrestricted; safe joins/base query |
| `finance/services.py:1114` | `reserve_wallet_funds` | unrestricted; safe joins/base query |
| `finance/services.py:1174` | `capture_wallet_reservation` | unrestricted; safe joins/base query |
| `finance/services.py:1175` | `capture_wallet_reservation` | base row (`of=self`) |
| `finance/services.py:1178` | `capture_wallet_reservation` | unrestricted; safe joins/base query |
| `finance/services.py:1243` | `release_wallet_reservation` | unrestricted; safe joins/base query |
| `finance/services.py:1244` | `release_wallet_reservation` | base row (`of=self`) |
| `finance/services.py:1247` | `release_wallet_reservation` | unrestricted; safe joins/base query |
| `finance/services.py:1301` | `refund_to_wallet` | unrestricted; safe joins/base query |
| `finance/services.py:1337` | `reserve_financial_record_from_originating_wallet` | base row (`of=self`) |
| `finance/services.py:1375` | `approve_service_allowance` | unrestricted; safe joins/base query |
| `finance/services.py:1393` | `reserve_service_allowance` | base row (`of=self`) |
| `finance/services.py:1396` | `reserve_service_allowance` | unrestricted; safe joins/base query |
| `finance/services.py:1411` | `reserve_service_allowance` | unrestricted; safe joins/base query |
| `finance/services.py:1416` | `reserve_service_allowance` | unrestricted; safe joins/base query |
| `finance/services.py:1465` | `fund_allowance_reservation` | unrestricted; safe joins/base query |
| `finance/services.py:1466` | `fund_allowance_reservation` | unrestricted; safe joins/base query |
| `finance/services.py:1480` | `fund_allowance_reservation` | unrestricted; safe joins/base query |
| `finance/services.py:1496` | `capture_financial_record_wallet_reservation` | unrestricted; safe joins/base query |
| `finance/services.py:1497` | `capture_financial_record_wallet_reservation` | unrestricted; safe joins/base query |
| `finance/services.py:1521` | `earn_financial_record_allocations` | unrestricted; safe joins/base query |
| `finance/services.py:1526` | `earn_financial_record_allocations` | unrestricted; safe joins/base query |
| `finance/services.py:1553` | `capture_finance_for_hospital_publication` | unrestricted; safe joins/base query |
| `finance/services.py:1604` | `create_settlement_batch` | unrestricted; safe joins/base query |
| `finance/services.py:1605` | `create_settlement_batch` | base row (`of=self`) |
| `finance/services.py:1645` | `approve_settlement_batch` | unrestricted; safe joins/base query |
| `finance/services.py:1669` | `mark_settlement_batch_paid` | unrestricted; safe joins/base query |
| `finance/services.py:1687` | `mark_settlement_batch_paid` | unrestricted; safe joins/base query |
| `finance/services.py:1721` | `cancel_settlement_batch` | unrestricted; safe joins/base query |
| `finance/services.py:1741` | `approve_financial_record_credit` | base row (`of=self`) |
| `finance/services.py:1764` | `cancel_financial_record` | unrestricted; safe joins/base query |
| `finance/services.py:1767` | `cancel_financial_record` | unrestricted; safe joins/base query |
| `finance/services.py:1774` | `cancel_financial_record` | unrestricted; safe joins/base query |
| `finance/services.py:1908` | `attach_encounter_to_service_session` | base row (`of=self`) |
| `finance/services.py:1911` | `attach_encounter_to_service_session` | unrestricted; safe joins/base query |
| `finance/services.py:2030` | `submit_encounter_sponsorship` | unrestricted; safe joins/base query |
| `finance/services.py:2047` | `decide_encounter_sponsorship` | unrestricted; safe joins/base query |
| `finance/services.py:2048` | `decide_encounter_sponsorship` | base row (`of=self`) |
| `finance/services.py:2062` | `decide_encounter_sponsorship` | unrestricted; safe joins/base query |
| `finance/services.py:2063` | `decide_encounter_sponsorship` | unrestricted; safe joins/base query |
| `finance/services.py:2111` | `capture_encounter_sponsorship` | unrestricted; safe joins/base query |
| `finance/services.py:2112` | `capture_encounter_sponsorship` | base row (`of=self`) |
| `finance/services.py:2136` | `cancel_encounter_sponsorship` | unrestricted; safe joins/base query |
| `finance/services.py:2137` | `cancel_encounter_sponsorship` | base row (`of=self`) |
| `finance/services.py:2203` | `submit_founder_funded_expense` | unrestricted; safe joins/base query |
| `finance/services.py:2219` | `decide_founder_funded_expense` | unrestricted; safe joins/base query |
| `finance/services.py:2285` | `post_opening_balance` | unrestricted; safe joins/base query |
| `finance/services.py:2375` | `create_treasury_transfer` | unrestricted; safe joins/base query |
| `finance/services.py:2407` | `submit_treasury_transfer` | unrestricted; safe joins/base query |
| `finance/services.py:2423` | `decide_treasury_transfer` | unrestricted; safe joins/base query |
| `finance/services.py:2434` | `decide_treasury_transfer` | unrestricted; safe joins/base query |
| `finance/services.py:2470` | `record_treasury_transfer_execution` | unrestricted; safe joins/base query |
| `finance/services.py:2478` | `record_treasury_transfer_execution` | unrestricted; safe joins/base query |
| `finance/services.py:2501` | `record_treasury_transfer_execution` | unrestricted; safe joins/base query |
| `finance/services.py:2526` | `cancel_treasury_transfer` | unrestricted; safe joins/base query |
| `finance/treasury_reversals.py:17` | `_locked` | unrestricted; safe joins/base query |
| `finance/treasury_reversals.py:18` | `_locked` | unrestricted; safe joins/base query |
| `finance/treasury_reversals.py:72` | `request_reversal` | unrestricted; safe joins/base query |
| `finance/treasury_reversals.py:186` | `execute_reversal` | unrestricted; safe joins/base query |
| `finance/treasury_reversals.py:202` | `execute_reversal` | unrestricted; safe joins/base query |
| `finance/views.py:182` | `ServicePartnerViewSet.perform_update` | base row (`of=self`) |
| `finance/views.py:238` | `AssessmentServiceSessionViewSet.perform_update` | base row (`of=self`) |
| `finance/views.py:267` | `AssessmentServiceSessionViewSet.activate` | base row (`of=self`) |
| `finance/views.py:288` | `AssessmentServiceSessionViewSet.complete` | base row (`of=self`) |
| `finance/views.py:310` | `AssessmentServiceSessionViewSet.cancel` | base row (`of=self`) |
| `onward_referrals/services.py:92` | `accept_responsibility` | unrestricted; safe joins/base query |
| `onward_referrals/services.py:304` | `finalize_referral` | base row (`of=self`) |
| `onward_referrals/services.py:309` | `finalize_referral` | base row (`of=self`) |
| `onward_referrals/services.py:315` | `finalize_referral` | unrestricted; safe joins/base query |
| `onward_referrals/services.py:357` | `finalize_referral` | unrestricted; safe joins/base query |
| `onward_referrals/services.py:394` | `supersede_referral` | base row (`of=self`) |
| `onward_referrals/services.py:427` | `make_available` | base row (`of=self`) |
| `onward_referrals/views.py:184` | `OnwardReferralDetailView.patch` | base row (`of=self`) |
| `onward_referrals/views.py:262` | `VoidView.post` | base row (`of=self`) |
| `ops/views.py:754` | `OpsReportReturnView.post` | unrestricted; safe joins/base query |
| `ops/views.py:816` | `OpsReportApproveView.post` | unrestricted; safe joins/base query |
| `ops/views.py:915` | `OpsReportRejectView.post` | unrestricted; safe joins/base query |
| `ops/views.py:1096` | `OpsReleaseReportToHospitalView.post` | base row (`of=self`) |
| `ops/views.py:1134` | `OpsReleaseReportToHospitalView.post` | unrestricted; safe joins/base query |
| `patients/identity_services.py:29` | `generate_sentinel_patient_id` | unrestricted; safe joins/base query |
| `payments/services/posting.py:18` | `post_verified_payment` | base row (`of=self`) |
| `payments/services/posting.py:73` | `post_verified_payment` | unrestricted; safe joins/base query |
| `payments/services/posting.py:106` | `post_verified_payment` | unrestricted; safe joins/base query |
| `reports/clinical_integrity.py:98` | `accept_responsibility` | unrestricted; safe joins/base query |
| `reports/clinical_integrity.py:129` | `require_responsible_clinician` | unrestricted; safe joins/base query |
| `reports/eye_health.py:609` | `finalize_screening_report` | base row (`of=self`) |
| `reports/recall_services.py:101` | `set_diabetic_recall` | unrestricted; safe joins/base query |
| `reports/views.py:514` | `StructuredReportDetailView.update` | base row (`of=self`) |
| `reports/views.py:695` | `submit_report_to_ops` | base row (`of=self`) |
| `reports/views.py:764` | `clinic_issue_report` | base row (`of=self`) |
| `reports/views.py:1006` | `EyeHealthScreeningReportView.post` | unrestricted; safe joins/base query |
| `reports/views.py:1007` | `EyeHealthScreeningReportView.post` | unrestricted; safe joins/base query |
| `reports/views.py:1050` | `EyeHealthScreeningPreviewView.post` | base row (`of=self`) |
| `reports/views.py:1100` | `EyeHealthScreeningCorrectionView.post` | base row (`of=self`) |
| `reports/views.py:1197` | `EyeHealthScreeningReleaseView.post` | base row (`of=self`) |
| `reports/views.py:1206` | `EyeHealthScreeningReleaseView.post` | unrestricted; safe joins/base query |
| `uploads/bulk_import.py:373` | `_confirmation_plan` | unrestricted; safe joins/base query |
| `uploads/bulk_import.py:383` | `_confirmation_plan` | unrestricted; safe joins/base query |
| `uploads/bulk_import.py:406` | `confirm_bulk_import` | unrestricted; safe joins/base query |
| `uploads/bulk_import.py:443` | `confirm_bulk_import` | unrestricted; safe joins/base query |
| `uploads/bulk_views.py:97` | `BulkImageImportDetailView.delete` | unrestricted; safe joins/base query |
| `uploads/views.py:294` | `MobileTransferImageReviewView.post` | base row (`of=self`) |
