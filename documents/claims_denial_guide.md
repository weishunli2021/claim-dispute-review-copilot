# Claims Denial Guide (Synthetic)

> **Synthetic Data Notice:** This document is a fictional policy reference created for a prototype/demo. It does not represent an actual policy from any real health plan or payer, and contains no proprietary content of any kind. Plan names, network names, and identifiers are entirely fictional.

## CLM-1. Understanding Claim Denial Reason Codes

When a claim is denied, the claim record includes a denial reason code that identifies the general category of the denial, such as AUTH_REQUIRED, SERVICE_NOT_COVERED, or OUT_OF_NETWORK_PROVIDER. The denial reason code is a starting point for investigation, not a full explanation on its own -- an associate should confirm the underlying facts (benefit rules, authorization records, provider network status) before explaining a denial to a member.

## CLM-2. AUTH_REQUIRED

A denial reason code of AUTH_REQUIRED means the billed service required prior authorization and no approved, effective authorization record was found on file for the date of service. See the Prior Authorization Policy (sections PA-3 and PA-4) for how the effective window of an authorization is evaluated. This denial can sometimes be resolved by locating an authorization request that was submitted but not yet approved, or by submitting a new request.

## CLM-3. SERVICE_NOT_COVERED

A denial reason code of SERVICE_NOT_COVERED means the billed service is not a covered benefit under the member's specific plan, as recorded in that plan's benefit schedule. See the Benefits Guide for how coverage is determined per plan and service code. This is a different situation from a service that is covered but requires prior authorization.

## CLM-4. OUT_OF_NETWORK_PROVIDER

A denial reason code of OUT_OF_NETWORK_PROVIDER means the servicing provider was not contracted with the member's plan network at the time of service. See the Provider Network Policy for how network participation is determined and what benefit level, if any, applies to out-of-network care.

## CLM-5. Claims With Incomplete Information

In some cases a claim cannot be fully explained because required information is missing -- for example, no benefit rule exists on file for the plan and service code combination, or the servicing provider cannot be identified in the provider directory. A missing record is not the same as a negative determination, and an associate should never convert missing information into an assumed coverage or network decision. Cases like this should be flagged for manual review rather than resolved with an inferred denial reason.
