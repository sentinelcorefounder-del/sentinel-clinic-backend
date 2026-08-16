# Retinal report clinical integrity

Sentinel keeps one `StructuredReport` for each encounter. Clinical corrections update that
stable report while preserving append-only `StructuredReportVersion` snapshots and workflow
events. Patient, encounter, report reference, clinic and linked-referral identity cannot be
changed through the report API.

## Clinical authority

Clinical actions require an authenticated clinic user with branch access and either:

- the exact `optometrist` group; or
- the exact `reviewer` group, with a professional name, role and registration number supplied
  when responsibility is accepted.

Roles are additive. An account may also hold `clinic_admin`, but that administrative group
does not itself grant clinical authority. Superuser, Ops, finance, hospital and generic admin
status provide no clinical authority. Responsibility must be accepted explicitly. A later
eligible professional must provide a takeover reason; the original clinician remains recorded.

## Ops authority

Return, rejection, approval, issue and hospital release require both:

- `UserSecurityProfile.is_internal_sentinel_staff = True`; and
- the exact `ops_admin` or `sentinel_ops` group.

Superuser status alone does not grant these actions. Existing legitimate Ops reviewers must be
checked for both settings before activation.

## Versions and transitions

Every meaningful clinical save creates an immutable snapshot with a SHA-256 checksum. No-op
saves do not create versions. All mutations require `expected_version`; stale requests return
HTTP 409. Ops actions additionally identify the exact submitted version.

Returned and legacy rejected reports may be corrected and resubmitted on the same report row.
A resubmission note is required. `ops_rejected` cannot be approved directly. Submitted, signed,
approved, issued and released clinical content is read-only. Report deletion is unavailable in
the API and clinical report records are read-only in Django admin.

Migration `reports.0010_report_clinical_integrity` creates one author-unknown
`legacy_baseline` for every existing report. It first refuses impossible duplicate encounter
relationships. It does not infer an author or credentials. Existing issued rows are marked as
having no bound historical PDF unless exact bytes are already available.

## Issued documents

New issue actions bind the exact submitted/current version, generate the final clinician PDF
once, and store it in permanent private clinical storage. Only checksum, size, generation time
and the private object key are retained internally. The authenticated report endpoint serves
the bytes and never exposes the object key or a storage URL. Hospital access still requires the
existing canonical release predicate. Legacy issued reports continue their existing protected
rendering behaviour and are clearly marked unbound.

Clinical versions and issued PDFs are permanent records and are not part of Remidio staging
cleanup. Production activation still requires the private clinical R2 storage area to be
provisioned and verified.
