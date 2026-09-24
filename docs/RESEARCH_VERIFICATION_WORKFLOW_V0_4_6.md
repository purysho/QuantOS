# Research Verification Workflow — v0.4.6

Stage 4.6 makes the discovery/review boundary operational without weakening it.

## Gates

```text
DISCOVERED / TRIAGED
        ↓
      QUEUED
        ↓
   UNDER_REVIEW
        ↓
 CATALOG_CANDIDATE
        ↓
 catalog-admit
        ↓
 QUARANTINED source metadata
        ↓
 independently obtain exact source bytes
        ↓
 catalog-verify-file
        ↓
 VERIFIED source identity
        ↓
 claim-level extraction / counter-evidence / replication
        ↓
 trusted Claim Cards
```

The two key distinctions are:

1. **Catalog admission is not verification.** A reviewed candidate enters with
   authority and stance set to `UNASSESSED`, an empty `supports` list, and
   explicit do-not-infer warnings.
2. **Source verification is not claim validation.** `VERIFIED` means the
   exact stored artifact has been checked as the intended source/revision.
   It does not mean the paper's conclusions are true, replicated, causal, or
   investable.

## Operator commands

```bash
quantos-radar review-list --status QUEUED
quantos-radar review-start --queue-id review:... --reviewer analyst-1
quantos-radar review-candidate \
  --queue-id review:... \
  --reviewer analyst-1 \
  --notes "Relevant enough for source verification; no claims accepted."

quantos-radar catalog-admit --queue-id review:...

quantos-radar catalog-verify-file \
  --source-id ARXIV:2609.01234v2 \
  --path /secure/intake/paper.pdf \
  --source-uri https://arxiv.org/abs/2609.01234v2 \
  --verifier analyst-2 \
  --notes "Revision, title, authors and canonical source identity checked."
```

For the verification command, the exact file bytes are copied into the
content-addressed artifact store and the catalog records the SHA-256 artifact
ID. The command prints `CLAIM_TRUST UNCHANGED` because no Claim Cards are
created at this stage.
