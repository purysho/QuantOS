# Stage 4 Complete — Research Intake v0.5.0

Stage 4 turns QuantOS into a live research-discovery system while preserving a strict separation between **interesting**, **verified source identity**, and **trusted claim**.

## Proven flow

```text
arXiv metadata
   ↓
SHA-256 raw feed artifact
   ↓
revision-preserving discovery
   ↓
deterministic attention triage
   ↓
review quarantine
   ↓
explicit reviewer workflow
   ↓
catalog candidate
   ↓
QUARANTINED research catalog
   ↓
exact-source artifact intake
   ↓
VERIFIED source identity
```

At no point in this stage does publication, attention, review, catalog admission,
or artifact verification create a trusted empirical claim.

## Live integration

The live smoke workflow has successfully:
- called the real arXiv legacy API from a GitHub-hosted runner;
- parsed current q-fin metadata;
- persisted five discovery records;
- archived the raw Atom response as an immutable artifact;
- generated attention triage;
- queued review candidates;
- printed `DISCOVERY_ONLY`;
- uploaded disposable state without committing live-discovered data to git.

## Safety properties

- repeated API calls from one adapter instance obey a >=3-second interval;
- discovery revisions do not overwrite prior versions;
- triage profiles are hashed;
- queue transitions are explicit;
- a reviewer must be assigned before disposition;
- catalog candidates enter with `authority=UNASSESSED`, `stance=UNASSESSED`, and no supported claims;
- source verification requires exact bytes, canonical source URI, verifier identity and notes;
- source bytes are content-addressed by SHA-256;
- source verification does not create Claim Cards;
- the capital firewall remains unchanged.

## Next stage — Claim Workbench

A verified source is still not trusted reasoning input.

Stage 5 should add a claim-level workflow:

```text
VERIFIED source
     ↓
claim draft + exact locator
     ↓
scope / assumptions / limitations
     ↓
counter-evidence review
     ↓
independent reviewer
     ↓
APPROVED claim candidate
     ↓
ClaimStore promotion
```

Recommended gates:
1. no claim without a verified source artifact;
2. no material claim without an exact locator;
3. empirical/inferred claims must include scope and limitations;
4. drafter and reviewer must differ;
5. reviewer must record counter-evidence search notes;
6. approval and promotion remain separate actions;
7. promotion must re-check the source is still VERIFIED and artifact identity matches;
8. approval is claim-scoped, never source-wide.
