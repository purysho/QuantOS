# Scenario Discipline — Stage 6.2

Stage 6.2 adds probabilistic scenarios to immutable Research Cases.

The goal is disciplined uncertainty, not false precision.

## Probability bands

Each scenario carries:
- lower probability bound;
- central probability estimate;
- upper probability bound;
- written probability rationale.

For every scenario:

```text
0 <= low <= central <= high <= 1
```

Across a scenario set:
- central probabilities must sum to 1;
- lower bounds may not sum above 1;
- upper bounds may not sum below 1.

This ensures the probability intervals are at least jointly feasible.

## Scenario content

Every scenario also requires:
- name and description;
- assumptions;
- observable conditions;
- one or more outcome ranges.

Each outcome range carries:
- metric;
- unit;
- low;
- central;
- high.

```text
low <= central <= high
```

## Expected central outcome

A probability-weighted central outcome can be calculated only when every
scenario defines exactly one matching metric/unit.

This is a descriptive calculation over the stated assumptions. It is not a
forecast guarantee, expected alpha claim, or capital instruction.

## Versioning

Scenario sets are immutable and attach to one exact Research Case.

A revised scenario set can supersede an earlier set only for the same case.
A changed Research Case requires a new scenario set rather than silently
reusing old probabilities.
