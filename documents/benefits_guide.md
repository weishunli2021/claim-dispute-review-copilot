# Benefits Guide (Synthetic)

> **Synthetic Data Notice:** This document is a fictional policy reference created for a prototype/demo. It does not represent an actual policy from any real health plan or payer, and contains no proprietary content of any kind. Plan names, network names, and identifiers are entirely fictional.

## BEN-1. How Plan Benefits Are Determined

Each Meridian plan maintains its own benefit schedule that lists, for every service code, whether the service is a covered benefit and whether prior authorization is required. Coverage is always determined per plan and per service code; a service covered under one plan may not be covered under another, even for otherwise similar members.

## BEN-2. Outpatient MRI Benefit Under Gold Plans

Under Meridian Gold PPO plans, outpatient MRI (including MRI-KNEE) is a covered benefit, subject to the prior authorization requirement described in the Prior Authorization Policy and the Outpatient Imaging Policy. Being a covered benefit does not remove the need for an approved, currently-effective prior authorization at the time of service.

## BEN-3. Laboratory Services Under Silver Plans

Under Meridian Silver HMO plans, basic laboratory services (service code LAB-BASIC) are not a covered benefit. A claim for this service under a Silver plan is expected to deny for a coverage reason (SERVICE_NOT_COVERED) rather than a network or authorization reason.

## BEN-4. Initial Physical Therapy Visit Under Gold Plans

Under Meridian Gold PPO plans, an initial physical therapy visit (service code PT-INITIAL) is a covered benefit and does not require prior authorization. This benefit is only available at its standard level when performed by an in-network provider; see the Provider Network Policy for the effect of using an out-of-network provider for this same service.

## BEN-5. When Benefit Information Is Missing

Occasionally no benefit record exists on file for a given plan and service code combination. This is different from a benefit record that marks the service as not covered: the absence of a record means the coverage determination could not be completed with the information available, not that the service is excluded. A case like this should be escalated for manual benefit review rather than resolved by assuming either outcome.
