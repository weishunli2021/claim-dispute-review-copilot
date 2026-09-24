# Prior Authorization Policy (Synthetic)

> **Synthetic Data Notice:** This document is a fictional policy reference created for a prototype/demo. It does not represent an actual policy from any real health plan or payer, and contains no proprietary content of any kind. Plan names, network names, and identifiers are entirely fictional.

## PA-1. What Is Prior Authorization

Prior authorization is an approval obtained before certain services are performed, confirming that the service meets the plan's clinical and administrative requirements. Prior authorization is distinct from a benefit determination: a service being a covered benefit does not, by itself, satisfy a prior authorization requirement for that service.

## PA-2. Services That Require Prior Authorization

Outpatient MRI services (including MRI-KNEE) generally require prior authorization under Meridian plans. An initial physical therapy visit (service code PT-INITIAL) and a basic lab panel (service code LAB-BASIC) typically do not require prior authorization, though they remain subject to standard benefit and network rules. Associates should confirm the specific requirement using the member's benefit record for the plan and service code in question rather than assuming based on service category alone.

## PA-3. Authorization Effective and Expiration Dates

Every approved prior authorization has an effective date and an expiration date that define the window during which the authorization applies. An authorization record on file only satisfies the prior authorization requirement for a claim if the claim's date of service falls on or between that authorization's effective date and expiration date. An authorization that has already expired, or one whose effective date has not yet arrived, does not satisfy the requirement for a claim outside that window, even though a record of it exists.

## PA-4. AUTH_REQUIRED Denial Reason

A claim is denied with reason code AUTH_REQUIRED when the billed service requires prior authorization and no approved authorization record with an effective window covering the date of service is found on file. This denial reason reflects the absence of a matching, currently-effective authorization at the time the claim was processed; it does not by itself mean the service was never authorized at any point, since a member can have authorization records for different, non-overlapping time windows.

## PA-5. Requesting Prior Authorization

Prior authorization requests are typically submitted by the ordering physician's office before the service is scheduled. A request should include the requested service code, the anticipated date of service, and supporting clinical documentation. An associate assisting a member after an AUTH_REQUIRED denial should confirm whether a request was ever submitted, since a missing authorization can sometimes be resolved by submitting a new request rather than by filing a formal appeal.
