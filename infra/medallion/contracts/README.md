# Data contracts for silver and gold

Each silver and gold table or view in `../catalog.json` has a contract here:
`silver/<name>.yaml` or `gold/<name>.yaml`. A contract is what the table promises
its readers:

- its grain and key;
- each column's type, whether it may be null, what it means and how sensitive it is;
- quality rules;
- a freshness promise;
- the owning team, and the code and views that read and write it.

The files follow the [Open Data Contract Standard](https://bitol-io.github.io/open-data-contract-standard/)
v3.0.2, so standard tools can read them.

## What is generated and what is written by hand

`scripts/medallion_contracts.py generate` builds a database from `migrations/postgres`
and writes the parts the schema decides:

- the columns, their types, nullability, primary key and unique columns;
- the freshness promise (`slaProperties`), copied from `ibkr_etl.layer_freshness.RULES`;
- `customProperties`: the catalog source and note, the views that read the table, and
  the `ibkr_etl` modules whose SQL reads it (`consumers`) or writes it (`producers`).

Everything else is written by hand and kept when the file is regenerated: the purpose,
usage and limitations, the grain, column descriptions, classifications, dates stored as
text (`logicalType: date`) and quality rules. A description is written only where the
code makes the meaning clear.

Column classifications: `restricted` (can hold an IBKR account number or a person),
`confidential-financial` (client figures), `internal` (load metadata, versions, public
market data).

## Quality rules

Every rule is a SQL count of bad rows that must be 0 (`type: sql`, `mustBe: 0`), with
the table named with its layer schema so it runs whatever the search_path. Rules state
only what the schema or the writing code already guarantees or plainly intends:

- `severity: error`: the schema or the single writer guarantees it, so a failure is
  a bug or corrupted data.
- `severity: warning`: intended but not enforced, such as rows without a known account,
  or consistency across tables.

Nothing runs the rules on live data yet.

## Changing a contract

- **A migration changes a silver or gold table.** CI's agent-database job
  (`tests/test_medallion_contracts_db.py`) compares every contract with a freshly
  migrated database and fails, naming each difference. Run
  `python scripts/medallion_contracts.py generate` against a disposable server
  (`AGENT_TEST_PG_ADMIN_DSN`), describe any new column, and raise the version.
- **Hand-written parts change** (a description, a rule). Edit the YAML and raise the
  version.
- `python scripts/medallion_contracts.py validate` checks the files offline;
  `tests/test_medallion_contracts.py` does the same in the main test job.

## Versioning

Each contract has its own semantic version.

- **Major:** a column removed or renamed, a type changed (other than widening), a
  column that now allows nulls, a key change, or a rule that changes what a figure
  means. Readers must be checked.
- **Minor:** a column added, a column that becomes NOT NULL or unique, a new or
  tightened quality rule, a widened type.
- **Patch:** wording only.

A change to how a gold figure is calculated also changes the table's calculation
version column (`calculationVersionColumn` in the contract) in the code that writes it.

## How the later steps read contracts

- **Quality monitoring (review item 7):** the run-time checks read every rule from the
  contracts, run it after each load and keep the counts as history. Errors raise an
  incident, warnings go in the weekly quality report. The freshness promise
  (`slaProperties` and `freshnessCheck`) is already checked by `layer_freshness`.
- **Write, check, then publish (review item 2):** a gold build writes a new version,
  then runs that table's rules and those of the silver tables it reads. The version
  readers see moves forward only when no `error` rule fails.
  `calculationVersionColumn` says which column tells versions apart.
- **The catalog page (review item 10)** shows each contract's purpose, owner,
  freshness promise, consumers and classifications.
