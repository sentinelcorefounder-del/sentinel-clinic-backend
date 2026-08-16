# Onward ophthalmology referrals (Phase 1)

This bounded workflow creates a professional care-escalation letter after a completed assessment. It is separate from inbound hospital referrals, report issue/release, patient-report delivery, payment, wallet, earning and settlement workflows.

## Eligibility and authority

- The encounter must already be `completed` under the encounter workflow.
- At least one professional source must have a controlled onward-care outcome: ocular `refer_routine`, `refer_urgent` or `refer_emergency`; retinal `urgent_referral` or `ophthalmology_required`.
- Combined assessments require explicit ocular/retinal source selection.
- AI output is never an eligible clinical source.
- An active user with the exact `optometrist` or exact `reviewer` group, performing-clinic membership and explicit branch access must accept responsibility with a recorded name, professional role and registration number.
- Takeover requires another qualified clinical professional in the same clinic/branch and a recorded reason. Clinic administrators, Ops, finance users and superusers cannot accept, author, sign or supersede clinical content through those roles alone.
- A small-clinic master account may combine `clinic_admin` with either clinical role. Administrative capability neither grants nor removes its independently qualified clinical capability.
- Clinic administrators may view status. Distribution requires the exact `onward_referral_distributor` group in addition to `clinic_admin`; the responsible clinical professional may also distribute.

No historical author is inferred or silently backfilled.

## Routes and recipient isolation

- `originating_hospital` derives the recipient from the linked inbound referral and snapshots its MRN/reference only for that route.
- `registered_hospital` selects an active hospital organization from Sentinel's controlled organization list.
- `clinic_download` provides an authenticated protected download for an externally managed handoff and creates no hospital availability record.

A hospital with the exact `hospital_admin` role sees only versions explicitly made available to its organization. Availability does not release an assessment report or grant access to other patient records. The portal records document view and download independently; neither means sent, delivered, read, clinically accepted or booked.

## Versions, emergency handling and private documents

Drafts are editable only by the responsible clinical professional, except recipient selection by an authorized clinic administrator. Emergency finalization requires a structured escalation method and brief safe note plus explicit confirmation that immediate instructions or action occurred. The UI/PDF warns that the letter is not a substitute for immediate escalation and guarantees no receipt, acceptance, appointment or treatment.

Finalization freezes clinical, author, patient, recipient and branding snapshots, generates the PDF once, and stores its exact bytes and SHA-256 checksum under the non-PHI prefix:

`clinical-documents/onward-referrals/<uuid>/v<number>.pdf`

This prefix is permanent private clinical storage, not bulk-import staging or public/default media. Clients receive only authenticated Django document endpoints with `private, no-store` and `nosniff`; object keys and storage URLs are never serialized. Finalized clinical content is append-only. Corrections create a superseding version and preserve earlier PDFs, availability and access history. A changed/returned professional source produces a stale-source warning.

Production activation depends on the already required private clinical-assets storage configuration. Retention must follow Sentinel's clinical-record retention policy; the bulk-import cleanup command does not address this prefix.

## Deliberately deferred

Phase 1 does not send referral letters by email and does not use general organization contact addresses as clinical recipients. It also does not attach images, OCT, visual fields or assessment reports. A later bounded delivery phase must add verified clinical recipients, authorized recipient administration, a durable idempotent outbox, provider references, accepted/failed states, safe retries/webhooks, PHI-free subjects and authenticated delivery. Provider acceptance must never be labelled clinical acknowledgement or appointment booking. Controlled clinical attachments are also a later enhancement.
