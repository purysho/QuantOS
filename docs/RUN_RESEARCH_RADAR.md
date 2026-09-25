# Running the Research Radar

The radar is deliberately separate from the capital-safety CLI.

```bash
python -m pip install -e .

# Scan all nine q-fin subcategories
quantos-radar scan-arxiv

# Narrow or broaden explicitly
quantos-radar scan-arxiv \
  --category q-fin.PM \
  --category q-fin.ST \
  --category stat.ML \
  --max-results 50
```

You may set a descriptive User-Agent:

```bash
export ARXIV_USER_AGENT="QuantOS contact@example.com"
```

If omitted, the prototype identifies itself with the public repository URL.

A scan:
1. makes one metadata query;
2. archives the Atom feed as a SHA-256 artifact;
3. preserves each paper revision as a discovery record;
4. calculates deterministic research-attention triage;
5. prints the highest-attention items;
6. promotes **zero** claims.

The adapter enforces a minimum three-second interval between repeated requests from the same instance.
