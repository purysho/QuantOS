# Research Cases — Stage 6.1

Stage 6 moves First Current from single-claim reasoning to whole-case reasoning.

A Research Case is immutable and content-addressed.

## Required case structure

Every case requires:
- case type;
- subject IDs;
- universe;
- thesis;
- proposed mechanism;
- decision/research horizon;
- as-of timestamp;
- at least one trusted supporting ClaimCard;
- explicit alternative explanations;
- explicit falsifiers;
- explicit monitoring conditions;
- author and creation timestamp.

Optional but explicit:
- limiting claims;
- contradicting claims;
- assumptions.

## Point-in-time rule

Every referenced ClaimCard must have an `as_of` timestamp no later than the
case's `as_of` timestamp.

A later claim cannot be silently used to justify an earlier case.

## Revisions

Cases are immutable.

A revised case is a new content-addressed record with
`supersedes_case_id`.

The old case remains queryable and the lineage is preserved.

A revision cannot silently change the case type or subject IDs. A genuinely
different subject is a new research case, not a revision.

## Evidence roles

A trusted claim is assigned exactly one role inside one case:
- supporting
- limiting
- contradicting

The same ClaimCard cannot be placed in multiple roles to manufacture apparent
evidence breadth.

## What a Research Case is not

It is not:
- an order;
- a position;
- a recommendation;
- an approval to allocate capital;
- a probability that the thesis is true.

The next slice should add explicit scenario trees and probability discipline to
the case without creating false precision.
