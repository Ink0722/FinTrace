# Unified entity resolution

This offline pipeline answers one narrow question: whether a company-holder
record and a listed-company record denote the same legal entity. It does not
infer ownership, control or group membership from similar names.

## Build order

First build the offline company-code universe from shareholder snapshots,
research reports, announcements and the three financial statements:

```powershell
F:\conda_envs\FinTrace\python.exe -m data_pipeline.entity_resolution.build_company_universe
```

This writes
`data/processed/entity_resolution/company_universe.jsonl`. A stock code remains
a listed-company node even when no research report or AKShare profile exists.
Only structurally valid A-share codes are eligible for automatic profile
fetching; nonstandard and other-market identifiers remain local code-only
nodes.

Estimate the remaining network work without importing AKShare or calling an
upstream API:

```powershell
F:\conda_envs\FinTrace\python.exe -m data_pipeline.entity_resolution.fetch_company_profiles --estimate
```

Then fetch and freeze company profiles. Start with one code when validating a
new network environment:

```powershell
F:\conda_envs\FinTrace\python.exe -m data_pipeline.entity_resolution.fetch_company_profiles --code 600030
```

Then resume the full universe. Successful codes already present in the JSONL
are skipped:

```powershell
F:\conda_envs\FinTrace\python.exe -m data_pipeline.entity_resolution.fetch_company_profiles
```

For a small network check use `--limit 100`. Successful codes are skipped on
later runs, while failed codes remain eligible for retry; therefore `--limit`
is a request cap rather than a stable page selector.

The fetcher appends every result immediately to
`data/source/company_profiles/akshare_company_profiles.jsonl`. Failures include
their exception and attempt count, and `fetch_manifest.json` reports remaining
work. A run exits with code 2 when any requested code failed; rerunning retries
those codes without repeating successful requests. Use `--limit 10` for a
small batch and `--delay`/`--retries` to tune upstream pressure.
`--connect-timeout` and `--read-timeout` default to 10 and 30 seconds. They are
injected into AKShare's upstream request so a stalled SSL read becomes a
recorded retryable failure instead of blocking the whole corpus indefinitely.

After collection, rebuild the two deterministic indexes:

```powershell
F:\conda_envs\FinTrace\python.exe -m data_pipeline.entity_resolution.build_index
F:\conda_envs\FinTrace\python.exe -m data_pipeline.ownership.build_index
```

The first command writes
`data/indexes/entity_resolution/entity_master.sqlite`; the second imports only
confirmed `SAME_LEGAL_ENTITY` links into the ownership runtime index.

## Tables

| Table | Purpose |
|---|---|
| `entities` | Stable listed-company and company-holder nodes |
| `entity_aliases` | Original disclosed names and normalized lookup keys |
| `entity_identifiers` | Stock codes and source `compcode` values |
| `entity_links` | Auto-confirmed or human-confirmed same-entity decisions |
| `match_candidates` | Uncertain mappings awaiting review; never used online |

## Decision boundary

- An unambiguous exact normalized name is auto-confirmed.
- An AKShare legal-name match is marked `exact_legal_name` and takes
  precedence over security-name matching.
- A legal-core match is auto-confirmed only when it is unique on both the
  listed-company side and the company-holder side.
- Legal-core matching considers every disclosed name attached to the same
  shareholder `compcode`, rather than only its first observed name.
- Ambiguous exact names are pending candidates.
- Ambiguous exact or legal-core matches remain pending candidates.
- Fuzzy, embedding and LLM matches are not production links. They may later be
  added as candidate generators, but require review before use.
- Parent companies, subsidiaries, funds and asset-management plans remain
  separate entities even when their names share a core phrase.

The manifest records source hashes, counts and the schema version. Rebuilding
the entity index invalidates the ownership index until the latter is rebuilt.

AKShare is an offline acquisition dependency pinned to `1.18.94`. The default
source is `stock_individual_basic_info_xq`, which provides `org_name_cn` as the
legal name. `--source cninfo` retains the older CNInfo endpoint only as a manual
fallback because that upstream can time out or return non-JSON responses. A
valid Snowball token can be supplied through `FINTRACE_XQ_TOKEN` or
`--xq-token`; tokens are never written to the snapshot. The online Agent never
calls either source. Production and evaluation use only the frozen local
snapshot so upstream latency or downtime cannot affect answers.
