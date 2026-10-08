# Coding standards

Judgement-call rules for review, adapted from Robert C. Martin's *Clean Code* (1st edition, chapter in brackets). Mechanical rules (formatting, import order, unused imports and names, outdated syntax) are enforced by Ruff (`uv run ruff check .` and `uv run ruff format --check .`, configured in `pyproject.toml`), and the reviewer already carries Fowler's smell baseline, which covers the book's ch. 17. This file adds only what neither covers. Where a rule differs from the book, it says so. Each rule ends with a case this repo has actually hit.

The rules apply to new and changed code. Fix an existing violation when you touch that code, not in a separate sweep.

Chapters with no rule here: 1 (introduction), 5 (formatting is Ruff's job), 10 (there are no classes yet; add one only when state and the behaviour on it belong together), 12 (emergence restates the rules below), 14–16 (case studies whose lessons are folded into the rules below) and 17 (the reviewer's smell baseline).

## Names [ch. 2]

- **Use the API's own words.** Name a concept with the term `openapi.yaml` uses: space, booking, member, user, subscription. Keep a **member** (the name a booking is stored under, `"guest"` by default) apart from a **user** (a registered account, `user_id`). *Hit:* `create_booking` takes both `member` and `user_id`, and they mean different things.
- **Near-identical names must not hide different jobs.** If two names differ by one word, the shorter one must not do less in a way the name hides. *Hit:* `mark_paid` only flips the row to paid; `mark_booking_paid` authorises the card, logs and handles already-paid bookings.
- **One verb per concept.** Stick to the verbs the codebase already uses:

  | Verb | Meaning |
  |---|---|
  | `is_valid_*` | check one raw input value, return `bool`, never raise |
  | `is_*` | answer a yes/no question, possibly with a query |
  | `normalise_*` | clean one raw input, or return `None` if it can't be used |
  | `calculate_*` | pure arithmetic, no I/O |
  | `get_*` | fetch one row, or `None` |
  | `list_*` | fetch every matching row, as a list |
  | `create_*` / `update_*` / `delete_*` | validate, then write; return `(payload, status)` |
  | `mark_*` | change one field of an existing row's state |
  | `*_to_json` | turn a row into a dict ready for `jsonify` |
  | `seed_*` | insert starter data only if it is missing |

## Functions [ch. 3]

- **One level of abstraction per function.** A domain function reads as a list of named steps: look up, validate, check for conflicts, write. Pull an inline calculation out into a named function or variable. *Hit:* `create_booking` decides between `0` and `calculate_booking_price_cents(...)` inside the `INSERT` parameter tuple.
- **Do one thing.** A function either answers a question or changes state (command-query separation). When it has to do both, the name says both.
- **The cursor comes first, and the caller owns it.** Domain functions take `cur` as their first argument. They never open a connection, commit or close. The route owns `with current_app.db.cursor()`.
- **No flag arguments.** A boolean that picks between two behaviours means there are two functions. *Differs from the book:* a flag that is part of the public API passes straight through as a keyword argument. *Hit:* `force_failure` goes from the request body to `authorize_card`.
- **Validate a kind of value once.** When two inputs need the same check, call one validator instead of copying its logic and its comment. *Hit:* `create_booking`'s `party_size` check repeats `is_valid_capacity`, including the "bool is a subclass of int" comment.
- **No numeric limit on arguments.** *Differs from the book:* there is no cap on argument count. A function that needs many related values still takes them by name. *Hit:* `create_booking` takes 7.

## Comments [ch. 4]

- **Comment only a why the code can't carry.** Good reasons are a Python gotcha, a race condition, a security consequence or an ADR. A function's name carries the what. *Hit:* "bool is a subclass of int in Python, so rule out true/false".
- **Docstrings state the return contract when the name can't.** Say what comes back, for success and failure, as in `Returns (payload, status) - the new space, or an {"error": ...}.`
- **Delete-test every comment.** If removing it loses no information the code doesn't already carry, it's restating: cut it.
- **History belongs to git.** Ticket and issue numbers, "added for X" and "previously Y" go in commit messages and ADRs, not comments. *Hit:* `schema.py` cites `(#171)`, `(#134, the first concrete step of #86)` and `(#119)`. Those are `spacey` issues, but from this repository they link to issues here.
- **No commented-out code and no banner or position-marker comments.**

## Objects and data structures [ch. 6]

- **Rows stay plain dicts.** Database rows come back as dicts. Don't wrap them in a class unless it adds behaviour. *Differs from the book:* the book prefers objects that hide their data.
- **Every query on a table returns the same shape.** Select the module's column constant (`BOOKING_COLUMNS`, `SPACE_COLUMNS`), not a hand-written column list.
- **Convert to wire format in one place per resource.** Domain functions return rows. The `*_to_json` function turns timestamps into UTC ISO 8601, and only the route calls it. *Hit:* `subscribe` formats `started_at` itself, while bookings use `booking_to_json` in the route.

## Error handling [ch. 7]

- **Expected failures are return values.** Domain functions return `({"error": "..."}, status)` for bad input (400), missing rows (404) and conflicts (409). *Differs from the book:* the book prefers exceptions to error codes. Here, the status code is part of every result and the route passes it through unchanged.
- **Catch database exceptions narrowly, at the statement that raises them.** Wrap only the `execute` that can fail, catch the specific exception and map it to a status. *Hit:* `except UniqueViolation` around the `INSERT` in `register_user`, and `except (DeadlockDetected, ExclusionViolation)` around the `INSERT` in `create_booking`.
- **Error messages tell the caller what to fix.** Write them lower-case and naming the field and the rule. Never include internal detail. *Hit:* `"party_size must be a whole number of at least 1"`; a database failure returns only `"payment unavailable"`.
- **Never log or store what could leak card or SQL data.** Log the outcome and ids, not the exception, since database diagnostics can include SQL parameters. Store only a card's last 4 digits. *Hit:* `mark_booking_paid` logs `outcome=database_error` and stores `card_number[-4:]`.

## Boundaries [ch. 8]

- **Routes have no business rules and no SQL.** A route parses the request, calls one domain function and shapes the response. *Hit:* the module docstring of `purchase/api.py` sets this rule.
- **Raw request data is validated in the domain function.** The route passes values through as sent (`body.get(...)`), so every domain function checks the types it is given. *Hit:* `is_valid_capacity` rejects `True`, which JSON can send where a number is expected.
- **Other services are black boxes behind one call each.** Purchase reaches Payment and Access only through their APIs (ADR 0001). Keep each to a single call site, so moving it to HTTP changes one place. *Hit:* `authorize_card` is the only Payment call.

## Tests [ch. 9]

- **Test pure rules with example tables.** Pure functions (`calculate_*`, `is_valid_*`) get a `pytest.mark.parametrize` table of inputs and expected outputs, named `test_<thing>_examples`.
- **Test behaviour through the HTTP API.** Use `create_app().test_client()`, not a mocked cursor, so the SQL is exercised too.
- **One behaviour per test, named as a sentence.** Name tests like `test_health_reports_local_revision_when_unset`. Separate arrange, act and assert with blank lines.
- **A new rule, default or error branch gets a test in the same change.**

## Systems [ch. 11]

- **The app is built by `create_app()`.** Importing a module has no side effects: no connections and no reading config at import time. Config comes from environment variables with a working local default. *Hit:* `/health` reads `APP_REVISION` per request, falling back to `"local"`, so tests can set it with `monkeypatch`.
- **Schema changes are additive until PUR-008.** `spacey` still runs its own schema on the same database (ADR 0001), so use `CREATE ... IF NOT EXISTS` and `ADD COLUMN IF NOT EXISTS`. Never drop or rename. Every statement must be safe to run on every start.

## Concurrency [ch. 13]

- **Every check-then-write has a database constraint behind it.** An application pre-check gives a clear error in the common case. Only a constraint stops two simultaneous requests. *Hit:* the overlap pre-check in `create_booking` is backed by the `no_overlapping_bookings` exclusion constraint, and the race is caught as `ExclusionViolation`.
- **A write a client may retry is idempotent.** *Hit:* paying an already-paid booking returns it with `200` and charges nothing; `subscribe` uses `ON CONFLICT ... DO UPDATE`.
