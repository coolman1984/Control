# BUSINESS_RULES

Project knowledge is stored in strictly validated TOML packs referenced by `[rules].packs` in `xl2ai.toml`. `xl2ai doctor` validates pack syntax and duplicate identifiers before a refresh opens Excel.

Example:

```toml
[pack]
name = "sales"
version = "1.0"

[[term]]
term = "Net Sales"
meaning = "Recognized sales after approved deductions."
aliases = ["Sales"]
unit = "EGP"
status = "confirmed"

[[key]]
table = "orders/orders"
columns = ["order_id"]

[[relation]]
from_table = "orders/orders"
from_column = "customer_id"
to_table = "customers/customers"
to_column = "customer_id"
kind = "reference"

[[rule]]
id = "no_negative_qty"
sql = "SELECT COUNT(*) FROM {{table:orders/orders}} WHERE quantity < 0"
expect = "zero"
severity = "error"
message = "Negative quantity requires review."

[[kpi]]
id = "net_sales"
sql = "SELECT SUM(net_sales) FROM {{table:orders/orders}}"
unit = "EGP"
```

## Selectors

A table selector is:

```
source_id/table_name
```

Giving important configured sources a stable `alias` is recommended so moving a file does not change business-rule selectors.

## Expectations

Rules currently support:

- `zero`
- `nonzero`
- `true`
- `not_null`
- `equals:<value>`

Rule SQL must be read-only.

## Promotion behavior

A rule execution error fails the rules stage.

A rule whose expectation is not met is stored as `fail`. It blocks promotion only when:

```toml
[rules]
block_on_error = true
```

and that rule has `severity = "error"`.

## Trust

Inferred keys/relationships remain inferred. Declaring them in a pack changes their status to confirmed. That distinction is intentional and is exposed to AI-facing outputs.

## Confirming an auto-drafted definition

Every refresh drafts a plain-language definition for each table (grain + time coverage) and for money/quantity/
percentage columns with a detected unit, and stores it in `_dictionary` with `status = "inferred"`,
`origin = "auto"` (see `xl2ai query meta definitions`). These are a starting point, not a decision: an agent must
still say "not confirmed yet" when asked for a definitive meaning. A human confirms one by adding the equivalent
`[[term]]` entry to a pack with the agreed wording; the pack's entry (`status = "confirmed"`, `origin = "pack"`)
is what future runs and agents should treat as authoritative. The same applies to a column's inferred role
(`query meta column_roles`) and a table's inferred grain (`query meta grain`): confirm the ones that matter with
`[[key]]`/`[[term]]` entries so later agents stop seeing "inferred" and start seeing "confirmed".
