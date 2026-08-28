# Private staging and permanent clinical storage for bulk Remidio imports

Bulk fundus imports must use storage that is separate from ordinary public media.
Production intentionally fails its Django system check and rejects import requests
until all dedicated staging settings are present.

Confirmed images are copied only after review into a second, durable private
clinical-asset bucket. The staging bucket is never the clinical record and the
ordinary/public media backend is never a fallback for bulk imports.

## Required production configuration

Configure `CORS_ALLOWED_ORIGINS` with the exact reviewed production frontend
origins. `CORS_ALLOWED_ORIGIN_REGEXES` defaults to no entries; if production
genuinely requires regex origins, provide them explicitly as a comma-separated
environment value after review. Wildcard Vercel preview access is never enabled
automatically. Staging should leave the regex value unset or empty and rely on
its exact staging origin in `CORS_ALLOWED_ORIGINS` and
`CSRF_TRUSTED_ORIGINS`.

- Create a separate private S3-compatible or Cloudflare R2 bucket.
- Disable anonymous/public access at bucket level.
- Do not attach a public custom domain.
- Issue least-privilege credentials limited to reading, writing, and deleting
  objects in this staging bucket.
- Configure:
  - `BULK_STAGING_R2_ACCOUNT_ID`
  - `BULK_STAGING_R2_ACCESS_KEY_ID`
  - `BULK_STAGING_R2_SECRET_ACCESS_KEY`
  - `BULK_STAGING_R2_BUCKET_NAME`
- Create a separate durable private clinical-assets bucket with independent
  least-privilege credentials and configure:
  - `CLINICAL_ASSETS_R2_ACCOUNT_ID`
  - `CLINICAL_ASSETS_R2_ACCESS_KEY_ID`
  - `CLINICAL_ASSETS_R2_SECRET_ACCESS_KEY`
  - `CLINICAL_ASSETS_R2_BUCKET_NAME`
- Do not configure lifecycle expiry for confirmed clinical assets. Retention
  must follow the clinical-record policy. Do not attach a public domain.
- Configure an object lifecycle rule as a safety net to expire staging objects
  after a short retention period (recommended: two days).
- Browser CORS access is not required because previews are streamed through
  authenticated Django endpoints. Do not enable public browser reads.

Development and tests use isolated `.bulk_staging` and `.clinical_assets`
filesystem directories, not ordinary media storage.

The same private-storage boundary now applies to all newly uploaded clinical
images. Normal clinic/laptop uploads and confirmed QR/mobile images are written
to the permanent clinical-assets alias with generated, non-identifying keys.
QR/mobile images awaiting confirmation use generated keys under
`bulk-staging/mobile/`; expiry, cancellation, rejection, or the
`cleanup_mobile_transfers` management command removes unconfirmed objects.
Legacy default-storage database names remain on the default media alias and are
served through authenticated application endpoints; they are not rewritten or
migrated by this rollout.

New ocular-investigation files and finalized clinical PDFs also use permanent
private clinical storage. Organization logos and existing finance evidence keep
their historical default-storage identity, but application pages and protected
download actions read them server-side without requiring a public bucket URL.

Confirmation uses generated keys with no patient data. Selected files are
copied to permanent storage before one atomic database commit. Failed copies
create no clinical attachments; successfully prepared objects are recorded and
reused on retry. Confirmed requests are idempotent. Rejected files remain only
in staging and are removed with all other staging objects after confirmation.
The cleanup command removes expired staging data and uncommitted prepared
objects, but never deletes an object referenced by a confirmed attachment.

## Production verification

Before enabling the feature:

1. Run `python manage.py check --settings=config.settings.production` with all
   required variables set. It must pass `uploads.E001` through `uploads.E004`.
2. Upload a synthetic object through the staging storage using the deployment
   credentials.
3. Confirm an anonymous request to the object endpoint returns access denied.
4. Confirm the application preview endpoint requires authentication and the
   correct organization and branch.
5. Confirm cancellation deletes the synthetic object.
6. Confirm bucket and endpoint details never appear in the API response.
7. Confirm permanent objects cannot be read anonymously and can be read only
   through the authenticated `/api/uploads/<id>/content/` application endpoint.
8. Exercise `python manage.py cleanup_bulk_imports` and alert on cleanup-pending
   imports or items so failed orphan deletion is retried.

Code support alone does not establish production privacy. The feature remains
blocked until the real bucket policy and anonymous-access denial are verified.
