# Research Radar Triage v0.4.2

The radar now has a deterministic attention-ranking layer.

It is intentionally **not** a truth score, alpha score, or investment score.

## Components

- **Relevance** — matches configurable research themes using title, abstract and categories.
- **Lexical novelty** — one minus the closest Jaccard similarity to prior radar items.
- **Freshness** — exponential age decay with a configurable half-life.

Default attention score:

```text
0.60 relevance + 0.25 lexical novelty + 0.15 freshness
```

The result is placed into one of:

- `ATTENTION_HIGH`
- `ATTENTION_REVIEW`
- `ATTENTION_LOW`

Every result carries the caveat that attention does not establish correctness, replication, causality, or investment value.

## Default themes

The prototype watches for:
- asset pricing and factors
- portfolio construction
- research integrity / overfitting
- causal methods
- market microstructure and execution
- machine learning / representation learning
- alternative data
- volatility / derivatives
- risk / regime change

The scoring profile is hashed so later changes to themes or weights cannot silently rewrite old triage results.
