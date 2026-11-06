# 2. Investigation execution

## Status

**Proposed**

Date: To be set when the ADR is merged.

## Context

RootScope requires a resilient, asynchronous execution engine for AI-powered incident investigations.

Investigations involve long-running LangGraph workflows, multiple LLM requests, and external read-only data sources. Execution may be interrupted by worker crashes, network partitions, stalled processes, or transient provider failures.

RabbitMQ provides at-least-once delivery. Messages can be duplicated or redelivered, and workers may continue executing after losing ownership of an investigation.

The architecture must:

- Prevent duplicate messages from starting the same execution attempt.
- Recover interrupted investigations without unnecessarily repeating completed work.
- Prevent stale workers from corrupting investigation state or checkpoints.
- Enforce a bounded wall-clock execution deadline.
- Avoid holding RabbitMQ deliveries unacknowledged throughout long-running investigations.
- Preserve consistency between investigation state transitions and event publication.
- Remain operationally simple for RootScope's expected workload of approximately 10–100 investigations per month.

PostgreSQL is the authoritative source of truth for investigation state and execution ownership. RabbitMQ is responsible for asynchronous delivery, not ownership management.

## Decision

### 1. Investigation lifecycle

Investigations retain the existing statuses:

| Status | Meaning |
|---|---|
| `queued` | Awaiting initial execution or recovery |
| `running` | Claimed by an active worker |
| `succeeded` | Successfully completed |
| `failed` | Permanently terminated |

We retain the existing `InvestigationStatus` enum, database CHECK constraint, and API contract. No status renaming is required.

The investigation record maintains:

| Field | Purpose |
|---|---|
| `attempt_number` | Monotonically increasing execution fencing token, initially 0 |
| `last_heartbeat_at` | Timestamp of the latest successful heartbeat |
| `deadline_at` | Absolute wall-clock execution deadline |
| `started_at` | Timestamp of the first successful execution claim |
| `completed_at` | Terminal completion timestamp |
| `error` | Latest recorded failure |

The maximum attempt count is a configuration setting rather than a dedicated database column.

Existing investigation identifiers, idempotency constraints, and configuration fields remain unchanged.

Terminal statuses (`succeeded`, `failed`) cannot transition back to `queued` or `running` through automatic recovery.

### 2. Execution deadline

**Decision: 30-minute absolute wall-clock deadline.**

The deadline is established on the first successful execution claim:

`deadline_at = NOW() + INTERVAL '30 minutes'`

It is persisted in PostgreSQL and never reset during retries.

The deadline includes:

- LangGraph execution.
- LLM API calls and their retries.
- Worker failure detection.
- Recovery queue waiting.
- Checkpoint restoration.
- Final result persistence.

The worker must use the remaining deadline budget when scheduling external calls.

#### Remaining budget

The worker starts a monotonic timer immediately before sending the claim request. The claim returns `deadline_at - NOW()` as the remaining budget, and the worker's local deadline is the timer's start plus that value. Claim latency is therefore deducted from the budget, making the local deadline no later than `deadline_at`.

The worker never compares `deadline_at` with its local wall clock.

#### Finalization margin

A 30-second soft finalization margin is applied:

- At `deadline_at - 30 seconds`, the worker stops starting new LLM requests.
- Each LLM call's timeout is capped at the remaining budget minus the finalization margin, so every in-flight call ends by `deadline_at - 30 seconds`.
- Already completed work may still be finalized.
- At `deadline_at`, the investigation becomes ineligible for successful completion.

The 30-second margin is configurable and may be tuned using actual execution metrics. It does not guarantee that finalization will succeed.

#### Deadline enforcement

The scheduler independently detects expired investigations, regardless of heartbeat health.

All successful completion transitions require:

`status = 'running' AND attempt_number = :my_attempt AND deadline_at > NOW()`

Deadline expiry results in `failed`, without retry.

No additional grace period is provided after the absolute deadline.

The scheduler and worker use conditional PostgreSQL transactions to resolve races between deadline failure and successful completion.

### 3. Heartbeats and worker self-fencing

Workers emit periodic heartbeats to PostgreSQL independently of LangGraph execution.

| Parameter | Value |
|---|---|
| Heartbeat interval | 15 seconds |
| Heartbeat staleness threshold | 60 seconds |
| Worker self-fencing threshold | 45 seconds |
| Recovery scheduler interval | 15 seconds |

Heartbeat updates are conditional on current ownership:

```sql
UPDATE investigations
SET last_heartbeat_at = NOW()
WHERE id = :investigation_id
  AND status = 'running'
  AND attempt_number = :my_attempt
  AND deadline_at > NOW();
```

A heartbeat update affecting zero rows means the worker no longer owns the investigation or the deadline has expired.

The worker must stop execution in that case.

If PostgreSQL is unreachable, the worker cannot assume it still owns the investigation.

**Worker self-fencing rule:**

If no heartbeat has been successfully persisted for 45 seconds, the worker must stop execution and attempt to cancel ongoing LangGraph work.

The worker measures this interval using a monotonic clock, avoiding dependence on local wall-clock adjustments.

A successful heartbeat resets the local timer.

Cancellation is best-effort. An external LLM request already in progress may still complete or incur charges.

Heartbeats must not be blocked by long-running LLM requests. They run in a separate asynchronous task or equivalent independent execution mechanism.

A heartbeat failure does not immediately consume an additional investigation attempt.

### 4. Worker failure detection and recovery

The recovery scheduler scans nonterminal investigations.

Recovery decisions follow this order:

1. If the absolute deadline has expired, mark the investigation `failed`.
2. If the investigation is `running` and its heartbeat is stale, check the attempt budget.
3. If attempts are exhausted, mark it `failed`.
4. Otherwise, atomically revoke the current attempt, transition to `queued`, and insert a new outbox event.

A stale heartbeat is defined as:

`last_heartbeat_at <= NOW() - INTERVAL '60 seconds'`

A stale heartbeat indicates an unresponsive worker, not necessarily a crashed process.

The scheduler must not assume that the old worker has stopped executing.

All recovery transitions use conditional PostgreSQL updates and appropriate row-level locking.

The scheduler must verify the current status, attempt number, heartbeat timestamp, and deadline in the same transaction that performs recovery.

Concurrent scheduler instances must not create duplicate retry transitions.

The recovery transaction:

- Changes `running` to `queued`.
- Revokes the previous attempt's ownership.
- Clears the active heartbeat timestamp.
- Preserves `attempt_number`, `started_at`, and `deadline_at`.
- Inserts an outbox event for `expected_attempt = attempt_number + 1`.

The next worker increments `attempt_number` only when it successfully claims the investigation.

**The investigation's existing attempt number is the fencing generation.** Recovery revokes ownership through the state transition; the next successful claim advances the generation.

### 5. Retry policy

Maximum execution attempts: **3**, including the initial attempt.

| Failure | Handling |
|---|---|
| HTTP 429 | Retry individual LLM call |
| Transient HTTP 5xx | Retry individual LLM call |
| Transient network failure | Retry individual LLM call |
| Individual request timeout | Retry if safe and sufficient time remains |
| Worker heartbeat timeout | Requeue and resume/restart according to checkpoint readiness |
| Permanent provider error | Mark `failed` |
| Overall deadline exceeded | Mark `failed` |
| Maximum attempts exhausted | Mark `failed` |

LLM retries occur within the active execution attempt.

They use bounded exponential backoff with jitter, with configurable retry limits.

Retries and backoff delays must fit within the remaining overall deadline.

SDK automatic retries are disabled or explicitly configured so that application-level retry limits remain authoritative.

An individual LLM request timeout is distinct from the overall investigation deadline.

An external request may have succeeded at the provider despite timing out locally. Exactly-once provider execution and billing are not guaranteed.

Worker-driven failure transitions must validate:

`status = 'running' AND attempt_number = :my_attempt`

They do not require `deadline_at > NOW()`. A failure recorded after the deadline is still terminal and preserves the actual error instead of a generic deadline error. The deadline restriction applies only to successful completion.

The existing `error` field stores the latest failure information and is cleared when the investigation succeeds.

### 6. No investigation-level retry backoff

We will not introduce:

- `next_attempt_at`
- `outbox_events.available_at`

Investigation recovery is immediately eligible for publication after the recovery transaction commits.

Transient LLM API errors are handled through per-call retries with backoff.

Worker failure recovery does not require an additional investigation-level delay.

The outbox relay publishes unpublished events without scheduled delivery delays.

### 7. Transactional outbox

Investigation creation and initial event insertion occur in the same PostgreSQL transaction.

The initial execution event is inserted only when `create_investigation` returns `created=True`.

Idempotent replays must not create additional initial execution events.

The outbox table contains the information required for reliable publication, including:

| Field | Purpose |
|---|---|
| `id` | Stable event identifier |
| `aggregate_id` | Investigation identifier |
| `event_type` | Event classification |
| `payload` | Versioned message payload |
| `created_at` | Event creation timestamp |
| `published_at` | Successful publication timestamp |

The exact column naming follows the existing schema and migration conventions.

The relay's selection of unpublished events uses a partial index:

```sql
CREATE INDEX idx_outbox_events_unpublished
ON outbox_events (created_at)
WHERE published_at IS NULL;
```

The outbox relay:

1. Selects unpublished events in small batches.
2. Uses `FOR UPDATE SKIP LOCKED` to support concurrent relays.
3. Publishes to RabbitMQ with publisher confirms and mandatory routing.
4. Marks an event published only after confirmed, successfully routed delivery.

A broker confirmation does not imply successful routing.

A returned unroutable message is treated as a failed publication, even if RabbitMQ confirms acceptance.

If publication succeeds but marking the event published fails, the relay may publish it again.

Duplicate publication is expected.

The outbox retention period is **7 days** after successful publication.

Unpublished events are not deleted by retention cleanup.

### 8. RabbitMQ topology

The messaging topology includes:

- A dedicated investigation exchange.
- A durable quorum queue for investigation execution.
- A dead-letter exchange and dead-letter queue.
- An explicit delivery limit for repeated unsuccessful deliveries.
- Persistent messages.
- Publisher confirms and mandatory routing.

Exchange and queue names are defined in PR 1, which introduces RabbitMQ.

The quorum queue's delivery limit must be configured explicitly rather than relying on broker defaults.

Dead-lettered messages are retained for investigation and operational remediation.

The dead-letter queue is monitored.

### 9. Pull-based worker execution

Each worker executes at most one investigation at a time.

Workers use `basic.get` rather than maintaining a push consumer with long-lived unacknowledged deliveries.

The execution loop is:

1. Fetch one RabbitMQ message.
2. Attempt to claim the investigation in PostgreSQL.
3. ACK the message after the claim transaction commits.
4. Execute or resume LangGraph if the claim succeeded.
5. Persist the outcome.
6. Return to polling only after execution ends.

Duplicate or stale messages that cannot claim an investigation are acknowledged and ignored.

When no message is available, the worker sleeps briefly before polling again.

Initial idle polling interval: **1 second**.

This design avoids relying on RabbitMQ prefetch settings to control investigation concurrency.

It also avoids holding deliveries unacknowledged for the duration of an investigation.

#### Uncertain claim outcome

If PostgreSQL connectivity fails during COMMIT, the worker must not assume the claim succeeded or failed.

Once connectivity is restored, it reads the investigation row.

- If the investigation is still `queued` with `attempt_number = expected_attempt - 1`, the claim did not commit. The worker may retry the claim or NACK and requeue the message.
- If the investigation is `running` with `attempt_number = expected_attempt`, ownership cannot be attributed reliably to this worker. The worker must not execute the investigation.
- If the investigation is already in another state or generation, the message is stale.

In the ambiguous running case, the worker acknowledges the message without executing it.

If the claim belonged to this worker, no execution heartbeats will arrive and the recovery scheduler will eventually recover the investigation, consuming an attempt.

This avoids introducing a separate worker ownership identifier.

If PostgreSQL remains unavailable, the worker must not acknowledge the message. It explicitly closes its RabbitMQ channel, which returns the unacknowledged delivery to the queue for redelivery, rather than holding the message indefinitely.

### 10. Attempt fencing and stale messages

Every execution message contains:

```json
{
  "version": 1,
  "investigation_id": "...",
  "expected_attempt": 2
}
```

The outbox event ID is used as the RabbitMQ message ID.

A worker may claim an investigation only if:

```sql
WHERE id = :investigation_id
  AND status = 'queued'
  AND attempt_number = :expected_attempt - 1
  AND attempt_number < :max_attempts
  AND (deadline_at IS NULL OR deadline_at > NOW())
```

A successful claim atomically:

- Changes status to `running`.
- Increments `attempt_number`.
- Sets `last_heartbeat_at`.
- Initializes `started_at` and `deadline_at` on the first attempt.
- Preserves the existing deadline on subsequent attempts.
- Returns `deadline_at - NOW()` as the remaining budget.

This protects against:

- Duplicate RabbitMQ deliveries.
- Stale events from earlier attempts.
- Concurrent claims for the same attempt.
- Old messages bypassing recovery scheduling.

Every worker-driven state transition must validate the current attempt number.

A stale worker cannot successfully finalize an investigation after its ownership has been revoked.

### 11. LangGraph checkpointing

RootScope uses PostgreSQL-backed LangGraph checkpoints to persist intermediate agent state.

The objective is to resume interrupted investigations from the latest valid committed checkpoint rather than repeat the entire workflow.

#### Checkpoint correctness

Checkpoint writes must enforce ownership fencing atomically.

A separate ownership check followed by an ordinary checkpoint write is insufficient because ownership may change between those operations.

Checkpoint persistence and ownership revocation must be serialized using appropriate PostgreSQL transactional guarantees.

The implementation must ensure:

- Stale workers cannot commit authoritative checkpoints after ownership revocation.
- Checkpoints committed before revocation remain eligible for recovery.
- Checkpoints committed after revocation cannot become the recovery starting point.
- Recovery selects the latest valid committed checkpoint.
- Concurrent stale and active attempts cannot corrupt checkpoint history.

#### Checkpoint identity

Two approaches remain under consideration:

**Option A:** One stable LangGraph `thread_id` per investigation, with transactionally fenced checkpoint writes.

**Option B:** One checkpoint thread per execution attempt, with the latest valid checkpoint copied or otherwise transferred when a new attempt is claimed.

The final approach will be selected through a dedicated checkpoint spike.

The spike must demonstrate atomic fencing, safe recovery, and compatibility with LangGraph's PostgreSQL checkpointer.

#### Behavior before checkpoint validation

Until the checkpoint implementation passes its correctness gate, recovery remains enabled but restarts the investigation from the beginning using a fresh checkpoint thread.

Checkpoint-based resume is enabled only after the spike validates the required guarantees.

#### External side effects

LangGraph checkpointing does not guarantee exactly-once execution of external operations.

An interrupted node may run again after recovery.

Therefore, investigation tools must be read-only or idempotent.

Any future mutating tools require explicit idempotency and side-effect handling.

### 12. Checkpoint dependencies and lifecycle

Checkpointing uses:

- `langgraph`
- `langgraph-checkpoint-postgres`
- `psycopg` 3

The application continues using `asyncpg` for its existing database access where applicable.

LangGraph checkpoint schema initialization through the checkpointer's `setup()` operation runs as a deployment step.

LangGraph-managed checkpoint tables are excluded from Alembic autogeneration and application schema migrations.

Checkpoint retention is **7 days**, subject to preserving data needed by active or recoverable investigations.

Cleanup must not remove checkpoints required by nonterminal investigations.

### 13. Recovery scheduler and database indexes

The recovery scheduler runs every 15 seconds.

It scans for:

- Running investigations with expired deadlines.
- Running investigations with stale heartbeats.
- Queued investigations whose established deadlines have expired.

The scheduler uses bounded batches and conditional transactions.

Recommended partial indexes include:

```sql
CREATE INDEX idx_investigations_running_heartbeat
ON investigations (last_heartbeat_at)
WHERE status = 'running';

CREATE INDEX idx_investigations_active_deadline
ON investigations (deadline_at)
WHERE status IN ('queued', 'running');
```

Final index definitions must be verified against the actual schema and query plans.

The scheduler must tolerate concurrent instances and repeated scans without creating duplicate recovery events.

### 14. Monitoring and observability

Monitor:

- Stale investigation heartbeats.
- Investigation deadline expirations.
- Worker self-fencing events.
- Recovery attempts and attempt exhaustion.
- Oldest unpublished outbox event.
- RabbitMQ queue depth.
- Dead-lettered messages.
- Checkpoint persistence and restoration failures.
- Investigations stuck in `queued`.
- LLM retry counts and provider failures.

#### Stuck-queued detection

A candidate stuck investigation is one where:

- `status = 'queued'`.
- Its newest execution outbox event was published more than a configurable threshold of **N minutes** ago.

Queue depth and available worker capacity are used to distinguish legitimate queueing from possible delivery failures.

An empty RabbitMQ queue strengthens evidence of a lost or unprocessable message but is not a mandatory alert condition.

The initial alert threshold will be configured based on observed queueing and execution behavior.

Operational remediation is manual in the initial implementation.

Detailed LLM usage and cost accounting remains deferred to PR 4.

## Correctness invariants

The implementation must preserve the following invariants:

1. **Single claim:** Only one worker can successfully claim a particular investigation attempt.
2. **Monotonic fencing:** Attempt numbers increase only through successful claims.
3. **Stale-message protection:** An event from an earlier attempt cannot claim a later attempt.
4. **Atomic recovery:** Investigation requeue transitions and retry outbox events are committed together.
5. **Worker fencing:** Revoked workers cannot modify authoritative investigation state.
6. **Checkpoint fencing:** Revoked workers cannot commit authoritative checkpoints after revocation.
7. **Checkpoint recovery:** Recovery may use checkpoints committed before ownership revocation.
8. **Deadline enforcement:** Investigations cannot successfully complete after the absolute deadline.
9. **Terminal-state safety:** Terminal investigations cannot be restarted by stale messages or scheduler scans.
10. **At-least-once compatibility:** Duplicate broker deliveries do not produce duplicate successful claims.
11. **Self-fencing:** Workers stop execution when they cannot establish continued ownership within the configured threshold.
12. **Retry bounds:** No investigation exceeds its configured maximum number of successfully claimed attempts.

## Validation

Integration testing must cover:

- Duplicate RabbitMQ messages and simultaneous claims.
- Stale messages arriving after recovery.
- Atomic investigation creation and initial outbox insertion.
- Outbox publication confirmation and unroutable returns.
- Worker crash after claim and ACK.
- PostgreSQL connectivity loss during claim COMMIT.
- Worker heartbeat timeout and recovery.
- Worker self-fencing after PostgreSQL connectivity loss.
- Original worker resuming after ownership revocation.
- Concurrent checkpoint persistence and recovery.
- Checkpoint restoration from a previously revoked attempt's valid checkpoint.
- Rejection of checkpoint writes after ownership revocation.
- Deadline expiry during LLM execution.
- Deadline expiry during checkpoint recovery.
- Deadline expiry while queued.
- Scheduler racing with worker completion.
- Maximum-attempt exhaustion.
- RabbitMQ dead-letter handling.
- Checkpoint and outbox retention cleanup.

**Checkpoint implementation gate:** Automatic checkpoint-based resume must not be enabled until atomic fencing and recovery behavior are demonstrated through integration tests.

## Alternatives considered

### Kafka instead of RabbitMQ

Rejected for the initial implementation.

Kafka provides durable event streaming and replay capabilities, but RootScope requires a comparatively simple work queue with low throughput.

RabbitMQ is sufficient and introduces less operational complexity for this workload.

### Debezium-based CDC instead of a polling outbox relay

Rejected for the initial implementation.

Debezium would provide change-data-capture-based event publication, but requires additional infrastructure and operational management.

A PostgreSQL polling outbox is adequate for RootScope's expected volume.

### DynamoDB and AWS Lambda

Not selected.

A serverless architecture could reduce worker infrastructure management, but would introduce a different persistence model and additional constraints around long-running execution, checkpointing, and recovery.

The PostgreSQL and RabbitMQ design better matches the current application architecture.

### ACK after investigation completion

Rejected.

Long-running unacknowledged deliveries complicate broker timeout handling and worker recovery.

PostgreSQL ownership and heartbeats provide a more explicit recovery model.

### Fixed execution lease without heartbeats

Rejected.

A fixed lease cannot distinguish a potentially crashed worker from an actively progressing investigation.

Heartbeats support earlier failure detection and recovery.

### Investigation-level retry backoff

Rejected for the initial implementation.

Transient provider failures are handled through per-call retries.

Worker failure recovery is immediately eligible once ownership has been revoked.

## Consequences

### Positive

- PostgreSQL provides authoritative investigation ownership.
- RabbitMQ delivery timeouts are decoupled from execution duration.
- Heartbeats enable timely recovery of unresponsive workers.
- Worker self-fencing reduces concurrent execution during database connectivity failures.
- Checkpoints reduce repeated LLM work after interruptions.
- Absolute deadlines bound total wall-clock execution time.
- Attempt fencing prevents stale workers from committing investigation outcomes.
- Transactional outbox publication preserves state/event consistency.
- Pull-based workers provide simple concurrency control.
- Recovery requires no delayed-message scheduling infrastructure.

### Negative

- Heartbeats increase PostgreSQL write volume.
- A dedicated recovery scheduler is required.
- Checkpoint fencing requires custom integration and validation.
- Network partitions may temporarily allow stale workers to continue external LLM calls.
- External provider execution and billing cannot be guaranteed exactly once.
- A strict deadline may discard otherwise valid results that finish too late.
- An uncertain claim outcome can consume an attempt without useful execution.
- Before checkpoint-based recovery is validated, worker failure recovery may repeat previously completed LLM work.

## Final configuration

| Parameter | Decision |
|---|---|
| Maximum attempts | 3, configuration setting |
| Overall execution deadline | 30 minutes wall-clock |
| LLM finalization margin | 30 seconds |
| Heartbeat interval | 15 seconds |
| Worker self-fencing threshold | 45 seconds |
| Heartbeat staleness threshold | 60 seconds |
| Recovery scheduler interval | 15 seconds |
| Worker concurrency | 1 investigation per worker |
| RabbitMQ consumption | Pull-based `basic.get` |
| Idle polling interval | 1 second |
| Investigation-level retry backoff | None |
| LLM call retries | Bounded exponential backoff with jitter |
| Checkpoint backend | PostgreSQL |
| Checkpoint identity | Determined by checkpoint spike |
| Checkpoint recovery before validation | Restart from beginning |
| Checkpoint recovery after validation | Resume latest valid checkpoint |
| Outbox retention | 7 days |
| Checkpoint retention | 7 days, with active-investigation protection |
| LLM usage accounting | PR 4 |

## Implementation status

**Proposed — pending implementation and checkpoint spike.**

The architecture is the target design for RootScope.

The checkpoint spike must establish a concrete implementation that guarantees atomic ownership fencing and correct recovery semantics.

Until that validation passes, investigations may be retried after worker failure, but each retry starts from the beginning with an isolated checkpoint thread.
