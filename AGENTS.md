\# Sentinel Clinic Agent Instructions



\## Project Overview

Sentinel Clinic is a healthcare workflow platform with:

\- Django REST Framework backend

\- Next.js frontend

\- Sentinel Ops portal

\- Sentinel Clinic portal

\- Sentinel Hospital portal

\- Payments

\- Referrals

\- Encounters

\- Structured reports

\- AI image analysis integration



\## Repository Structure

\- `backend/` contains Django backend code.

\- `frontend/` contains Next.js frontend code.

\- Backend apps include: `patients`, `encounters`, `uploads`, `reports`, `ops`, `referrals`, `organizations`, `users`.



\## Critical Business Rules

\- Never bypass clinic scoping.

\- Clinic users must only see patients assigned to their clinic.

\- Ops users may see global system data.

\- Hospital users must only see their own referrals.

\- Referral-to-patient linkage must be preserved.

\- Self-referrals must be marked with `source\_system = "self\_referral"`.

\- OpenAI branding must never be shown to end users; display as `Hybrid AI`.

\- VA, IOP and image upload belong to the encounter workflow.

\- Diabetic grading and recommendation belong to the structured report workflow.

\- Poor VA flag should only use Corrected/Pinhole VA, not unaided VA.

\- Reports require completed consent before creation/submission.

\- Preserve audit logging for Ops actions.

\- Do not remove existing migration files.



\## Backend Commands

From `backend/`:

\- Run migrations: `python manage.py migrate`

\- Create migrations: `python manage.py makemigrations`

\- Run server locally: `python manage.py runserver`



\## Frontend Commands

From `frontend/`:

\- Install dependencies: `npm install`

\- Run dev server: `npm run dev`

\- Build: `npm run build`



\## Coding Rules

\- Prefer small, focused changes.

\- Preserve existing API field names unless explicitly changing contract.

\- When changing serializers, check frontend consumers.

\- When changing models, create migrations.

\- When changing permissions, consider clinic, hospital, ops and super admin roles.

\- For clinical logic, prefer safer review flags over false reassurance.

