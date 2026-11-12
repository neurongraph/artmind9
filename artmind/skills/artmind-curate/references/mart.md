# Mart: dimension members and calendar periods

Only for a domain with a mart declaration (`domains/mart/<domain>.yaml`). The mart's dimensions
(person, place, type, time) group entities into levels: a place into city and country, a person by
relationship, a type into its family. Each member's levels are decided in this order, the first that
decides winning:

1. **user records** (what this reference writes);
2. declared rules (type families, kinship words);
3. graph seeds (a place's `location` or `LOCATED_IN` edge);
4. the LLM, only with `mart enrich --llm`; proposals below the domain's confidence threshold wait for
   review.

Every decision is a record under `.artmind/data/curation/mart_dimension/` (named periods under
`mart_calendar/`) and travels with the vault. The mart tables are rebuilt from the records, with no LLM
call.

## Read

```bash
artmind mart status --domain <d> --compact        # coverage by dimension and source; unreviewed proposals
artmind mart review list --domain <d> --compact   # proposals below threshold and members with no record
```

## Decide

```bash
artmind mart enrich --domain <d> --dryRun --compact                 # what rules, seeds and the LLM would decide
artmind mart enrich --domain <d> --llm --limit 50 --compact         # LLM calls: the user's yes first
artmind mart review set --domain <d> --dimension person|place|type \
  --member "<name|CLASS|domain or type:value>" --level <level>=<value> [--level ...] --dryRun --compact
artmind mart calendar set --domain <d> --name "<period>" --from YYYY-MM-DD --to YYYY-MM-DD --dryRun --compact
```

A `review set` record always wins over rules, seeds and the LLM: use it to correct a wrong country or
relationship. Level values must come from the declaration's vocabulary. A member merged by same-as
follows its canonical key.

Then rebuild the tables: `artmind mart build --domain <d> --compact` (no LLM).
