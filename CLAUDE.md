# RootScope

## Code Style: Comments and Documentation

- Prefer self-documenting code with clear naming and small, focused functions.
- Keep comments minimal. Explain **why**, not **what**.
- Add comments only for non-obvious business rules, concurrency assumptions, workarounds, and important technical constraints.
- Do not narrate straightforward code line by line.
- Avoid lengthy module-level docstrings and redundant function docstrings.
- Do not document implementation decisions or development history in source comments. Use ADRs when appropriate.
- Follow PEP 8 and PEP 257 where applicable, but do not add docstrings merely to satisfy a convention.
- Do not remove comments that explain correctness-critical behavior.
- Prefer concise, production-quality code over tutorial-style explanations.

## Architecture: Layering

- `api/` (router): HTTP concerns, request validation, response mapping. No SQLAlchemy, no transaction handling, no business logic.
- `services/`: business rules, orchestration, idempotency. Owns the transaction boundary (`async with session.begin():`) for one application operation.
- `db/repositories/`: SQLAlchemy queries and persistence. Operates within whatever transaction the caller already holds — never commits or rolls back itself.

Transaction-per-application-operation: the top-level service call owns `session.begin()`. A lower-level service/repository function being composed into a larger atomic operation later must not open its own nested transaction — see `docs/adr/0001-transaction-boundary-ownership.md`.
