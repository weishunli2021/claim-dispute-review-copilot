# Provider Network Policy (Synthetic)

> **Synthetic Data Notice:** This document is a fictional policy reference created for a prototype/demo. It does not represent an actual policy from any real health plan or payer, and contains no proprietary content of any kind. Plan names, network names, and identifiers are entirely fictional.

## NET-1. Network Participation Overview

A provider is considered in-network when they have a current contract with the member's plan network (for example, the Meridian Preferred Network). A provider without such a contract is considered out-of-network, regardless of the provider's specialty or where they are located.

## NET-2. In-Network Benefit Level

Services performed by an in-network provider are processed at the plan's standard benefit level, reflecting the negotiated contracted rate between that provider and the network.

## NET-3. Out-of-Network Consequences

Services performed by an out-of-network provider may be denied outright or paid at a reduced benefit level, depending on the member's plan type. PPO plans may allow a reduced out-of-network benefit in some circumstances, while HMO and EPO plans typically do not cover out-of-network care except in an emergency. A claim denied for reason code OUT_OF_NETWORK_PROVIDER reflects this network determination, independent of whether the underlying service is a covered benefit.

## NET-4. Verifying Provider Network Status

Network status should always be verified against the current provider directory record for the specific provider, rather than assumed from the provider's name, specialty, or location. A provider's network status can change over time, so an associate should not rely on a member's or provider's own statement about network participation without confirming it against the directory.
