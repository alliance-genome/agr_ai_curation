# AI Curation

<!-- impeccable:product-schema 1 -->

## Platform

web

## Users

Curators use ordinary conversations and extraction flows daily. Administrators need to understand their cost and investigate the requests contributing to it.

## Product Purpose

AI-assisted literature curation, with attributable accounting for model usage.

## Capabilities and Constraints

The admin cost surface is read-only, uses the existing application admin identity, and reads the shared canonical ledger. It must not create a duplicate usage store or pricing catalog. Recorded charges, estimates, unknowns, and coverage are distinct. Benchmarking will consume the same accounting foundation; ordinary conversations and flows are the immediate priority.

## Operating Context

The admin cost service runs separately in Docker at the same-origin `/cost` path. Initial delivery targets dev for the planned 0.10.0 release; production deployment is a separate approval.

## Product Principles

- Unknown cost is not zero.
- Preserve exact accounting facts and reproducible valuation provenance.
- Show a summary and filters before conversation and flow drill-down.
- Keep accounting metadata separate from scientific content.
