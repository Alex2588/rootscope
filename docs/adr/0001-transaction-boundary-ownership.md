# 1. Transaction boundary ownership

## Status
Accepted

## Context
Our first implementation committed inside the `get_session` FastAPI dependency, after
`yield` (commit on success, rollback on exception). This was empirically verified broken:
a `yield`-dependency's post-`yield` code runs during FastAPI's exit-stack teardown, which
happens *after* the response has already been serialized and sent over the ASGI
connection. A commit failure there cannot be surfaced to the client — we confirmed this
by forcing a commit failure and observing the client receive a false 201 Created.

An interim fix moved the commit into the router (`await session.commit()` before
`return`). This closed the gap but put transaction management in the HTTP layer, coupling
routers to persistence concerns and making it impossible to compose two writes (e.g. an
incident and its first investigation) into one atomic operation without threading
transaction control through the router.

## Decision
Three layers, transaction-per-application-operation:

- **Router** (`api/`): HTTP only. No SQLAlchemy, no commit/rollback.
- **Service** (`services/`): owns the transaction via `async with session.begin():`.
  Commits on normal exit, rolls back on exception — both within the service's own call
  stack, before the router returns and before any response is built.
- **Repository** (`db/repositories/`): executes queries within whatever transaction the
  caller already holds. Never commits or rolls back.
- **`get_session`**: provides the session and nothing else. `AsyncSession.close()` (on
  the dependency's exit) implicitly rolls back any transaction that wasn't explicitly
  committed, so no explicit rollback is needed there either.

A service function that opens its own `session.begin()` cannot be nested inside a larger
transaction later — if `create_incident()` always owns its transaction, a future
`create_incident_with_investigation()` can't combine it with a second write atomically.
For now we accept this (transaction-per-operation, one service call = one transaction)
rather than introduce a Unit of Work abstraction pre-emptively. When a real multi-entity
write arrives, the options are: (a) refactor the inner service functions to accept an
already-open transaction and have only the top-level caller open one, or (b) introduce an
explicit Unit of Work. Decide then, with a concrete case in hand.

## Consequences
- A commit failure anywhere in a service's transaction surfaces as a normal exception,
  caught by FastAPI's exception handling and converted to a 500 before any response is
  sent — the lifecycle bug this ADR responds to cannot recur through this path.
- Routers stay free of persistence concerns; business/idempotency logic is unit-testable
  without an ASGI client.
- Composing multiple writes atomically is not yet supported and will require revisiting
  this ADR when it's actually needed.
