# Review Objection Resolution — Stage 5.5

Stage 5.5 makes professional objections resolvable without making them erasable.

## Rule

A blocking or conditional review remains immutable.

To close it:
- the resolver must be the same reviewer who raised it;
- resolution notes are mandatory;
- at least one evidence reference is mandatory;
- the resolution is stored separately and append-only.

A `NO_OBJECTION` or `NOT_APPLICABLE` review cannot be "resolved" because it
does not carry an open issue.

## Panel behavior

The panel continues to show every original review.

Open blocking/conditional IDs exclude only reviews that have an attached valid
resolution. Resolved review IDs are listed separately.

This means the audit trail can answer both:
- "What was the original objection?"
- "What evidence convinced the reviewer that it had been addressed?"

Resolution does not authorize capital. It only closes one documented review
issue for one exact dossier fingerprint.
