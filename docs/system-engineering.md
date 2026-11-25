# Engineering a system

The 55 skills in the `project` group share the agent's existing catalog and execution interface. Search by keyword or request pages with `{"group":"project","offset":0,"limit":50}`. Discovery documents each skill's arguments.

The 49 design tools accept a JSON string in `text` or a workspace-relative JSON file in `path`. Add `output` to create a new workspace file. Text results such as SQL or Python are written verbatim. Structured results are written as JSON. The six repository workflows take `options`, documented in the catalog.

## Build and test a component

A loan-approval feature can use one design input for persistence and another for allowed state changes. Store the following inputs in the project workspace.

**schema-design.json**

```json
{"tables":{"member":{"pk":"id","columns":{"id":"INTEGER","name":"TEXT"},"required":["name"]},"loan":{"pk":"id","columns":{"id":"INTEGER","member_id":"INTEGER","amount_cents":"INTEGER"},"required":["member_id","amount_cents"],"references":{"member_id":"member"}}}}
```

**approval.json**

```json
{"states":["draft","submitted","approved"],"initial":"draft","terminal":["approved"],"transitions":[{"from":"draft","event":"submit","to":"submitted"},{"from":"submitted","event":"approve","to":"approved"}],"events":["submit","approve"]}
```

Issue one `skill_run` per row. Place the generated files in the same directory so tests can resolve their inputs.

| Skill | path | output |
| --- | --- | --- |
| `project_schema_ddl` | schema-design.json | schema.sql |
| `project_repository_module` | schema-design.json | repository.py |
| `project_schema_smoke_tests` | schema-design.json | test_schema.py |
| `project_state_reducer` | approval.json | state_machine.py |
| `project_state_test_module` | approval.json | test_workflow.py |

Run `python -m pytest -q test_schema.py test_workflow.py` in that directory. The repository module uses caller-owned SQLite connections and transactions. Callers enable foreign keys on each SQLite connection. The reducer checks event order; application-level authorization and lending policy are separate design inputs.

## Input conventions

- **Planning** uses `{"tasks":[{"id":"schema","hours":3,"depends":[],"owner":"Data","tests":["T1"]}]}`. Dependencies must form an acyclic graph. Earliest finish and slack use unlimited parallel workers. The release gate checks ownership and declared acceptance tests.
- **Storage** accepts lowercase table and column identifiers, one primary key per table, and INTEGER, REAL, TEXT or BLOB values. Foreign keys reference the target table's primary key. DDL targets SQLite.
- **HTTP contracts** accept OpenAPI 3.0 or 3.1 JSON. Operations inherit path parameters and top-level security unless overridden. References used by these checks must resolve within the document. The checks cover declared operation contracts.
- **State machines** accept deterministic named transitions and optional replay events. A declared terminal state has no outgoing transitions. Shortest scenarios count events.
- **Capacity and reliability** use the numeric fields listed in the catalog. Rates are per second unless otherwise stated. Target utilization is a fraction from greater than zero through one. Availability and hit rates use percentages. Queue and capacity outputs are estimates under the supplied rates.
- **Repository inspection** uses the current workspace. Git inventory, history, status and related-test lookup operate at the repository root. Python public API inspection and comparison read source without importing it.

## Complete skill list

### Planning

| Skill | Output |
| --- | --- |
| `project_build_waves` | Group implementation tasks into parallel dependency waves. |
| `project_earliest_schedule` | Calculate earliest start and finish hours with unlimited workers. |
| `project_critical_path` | Return one longest prerequisite chain and its duration in hours. |
| `project_task_slack` | Calculate how many hours each task can slip before project completion. |
| `project_dependency_impact` | Identify all downstream tasks affected by a delay to each task. |
| `project_effort_by_owner` | Aggregate estimated hours by owner and report unassigned work separately. |
| `project_acceptance_trace` | Map acceptance test IDs to the implementation tasks that require them. |
| `project_handoff_map` | Identify prerequisite handoffs between named implementation owners. |
| `project_release_gate` | Find planned tasks missing an owner or a declared acceptance test. |

### Storage

| Skill | Output |
| --- | --- |
| `project_schema_ddl` | Generate SQLite CREATE TABLE statements with primary, unique and foreign key constraints. |
| `project_fk_indexes` | Generate child-side SQLite indexes for foreign keys lacking a declared unique or primary index. |
| `project_orphan_checks` | Generate SQL checks for child rows whose non-null foreign keys lack a parent. |
| `project_unique_checks` | Generate SQL queries finding duplicate non-null values in declared unique columns. |
| `project_null_checks` | Generate null-count queries for mandatory fields before enforcing constraints. |
| `project_insert_templates` | Generate parameterized INSERT statements for application repositories. |
| `project_lookup_templates` | Generate parameterized primary-key lookups with explicit result columns. |
| `project_update_templates` | Generate primary-key-scoped UPDATE statements for non-key fields. |
| `project_data_dictionary` | Generate a field-level data dictionary with key, nullability and relationship metadata. |
| `project_repository_module` | Generate a Python SQLite repository with parameterized insert, lookup and update operations. |
| `project_schema_smoke_tests` | Generate pytest tests that apply sibling schema.sql and inspect actual SQLite table columns and keys. |

### Contracts

| Skill | Output |
| --- | --- |
| `project_route_inventory` | List documented HTTP routes, operation IDs and service tags. |
| `project_operation_id_gaps` | Find missing and repeated operation IDs that interfere with SDK generation. |
| `project_path_binding_gaps` | Find missing, optional and unused path parameters after applying operation overrides. |
| `project_response_test_matrix` | Generate contract-test cases for declared response codes and response media types. |
| `project_error_contract_gaps` | Find operations without any documented 4xx, 5xx or default error response. |
| `project_request_media_matrix` | Map request media types and required-body flags for operations accepting bodies. |
| `project_auth_contract` | Show effective security alternatives and whether anonymous access is declared. |
| `project_deprecation_inventory` | Identify deprecated operations and parameters that callers should migrate. |
| `project_required_input_cases` | Generate negative contract-test scenarios omitting one required request input at a time. |

### Lifecycle

| Skill | Output |
| --- | --- |
| `project_state_reachability` | List declared states unreachable from the initial state. |
| `project_dead_end_states` | Find reachable nonterminal states with no enabled event. |
| `project_completion_traps` | Identify reachable states with no path to any declared terminal state. |
| `project_event_permissions` | List the states in which each domain event is allowed. |
| `project_transition_test_cases` | Generate setup, action and expected state for each reachable transition. |
| `project_invalid_event_matrix` | Find defined events that must be rejected in each reachable state. |
| `project_state_replay` | Replay an ordered event trace and report the first invalid event and last state. |
| `project_terminal_scenarios` | Generate one shortest acceptance scenario reaching each attainable terminal state. |
| `project_state_table` | Generate a state-by-event decision table with explicit rejection cells. |
| `project_state_reducer` | Generate a standalone Python state reducer with an explicit invalid-transition error. |
| `project_state_test_module` | Generate pytest acceptance tests for a sibling state_machine.py reducer. |

### Capacity

| Skill | Output |
| --- | --- |
| `project_worker_sizing` | Estimate workers from arrival_rps, mean_service_ms and target_utilization (0–1). |
| `project_queue_recovery` | Estimate time to clear backlog_items at arrival_rps and processing_rps. |
| `project_retention_storage` | Estimate steady-state storage from events_per_day, bytes_per_event, retention_days and copies. |
| `project_transfer_window` | Estimate transfer seconds from payload_bytes, link_mbps and target_utilization. |
| `project_error_budget` | Calculate allowed downtime and remaining minutes from window_days, target_percent and outage_minutes. |
| `project_retry_envelope` | Bound request duration for attempts, timeout_ms, initial_backoff_ms and max_backoff_ms; no jitter. |
| `project_db_pool_budget` | Divide max_connections minus reserved_connections across peak_replicas during rollout. |
| `project_cache_origin_load` | Estimate origin request rate from request_rps and cache hit_percent. |
| `project_rollout_capacity` | Check remaining replica throughput during a rollout against peak_rps. |

### Repository

| Skill | Output |
| --- | --- |
| `project_inventory` | Summarize tracked file types and identify build and dependency manifests. |
| `project_change_summary` | Report staged, unstaged and untracked paths independently, including on an unborn branch. |
| `project_recent_changes` | Summarize recent commits with short IDs, dates and subjects, newest first. |
| `project_related_tests` | Find tracked Python and JS/TS tests named after a source file and rank nearby candidates. |
| `project_public_api` | Extract public Python classes, functions, methods and their signatures without importing code. |
| `project_api_diff` | Compare public Python declarations in two files for additions, removals and signature changes. |
