# Engineering assurance skills

The project catalog adds 262 operations in four areas. Each area has individual acceptance checks, two raw-event analyzers and one review that aggregates supplied evidence.

| Area | Individual checks | Event analyzers | Reviews |
| --- | ---: | ---: | ---: |
| Scalability | 60 | 2 | 1 |
| Accuracy | 60 | 2 | 1 |
| Safety | 60 | 2 | 1 |
| Reliability | 70 | 2 | 1 |

## Using the skills

Search `skill_catalog` with `{"group":"project","query":"guarantee_capacity"}`. Each matching check documents its required measurements, units and comparison. Run it with a JSON document in `text` or a workspace-relative `path`. Use `output` to save the result as a new workspace file.

A lending application can check whether two pending commitments would exceed a guarantor's capacity:

```json
{"skill":"project_accuracy_ledger_guarantee_capacity","text":"{\"reserved_minor_units\":850000,\"proposed_minor_units\":200000,\"available_guarantee_minor_units\":1000000}"}
```

The result is `fail` because the combined amount is 1,050,000 minor units and the supplied total capacity is 1,000,000. Use one currency per check and provide capacity before subtracting the existing reservations. The calculation operates on the supplied snapshot. The lending application's transaction still needs to enforce the capacity limit when reserving funds.

## Evidence and interpretation

- Provide counters from the same population, reporting interval and unit system. Supply measured baselines and your own service targets. The skills evaluate those inputs locally.
- Zero-violation checks require a positive `observations` count to pass. An empty sample or a zero denominator on a rate gate returns `insufficient_evidence`. Missing required inputs and invalid ranges return errors.
- Aggregate checks compare the supplied counts or budgets. Equal counts alone do not establish that two sets contain the same records. Use ID-level test evidence when auditing a population.
- The event analyzers process records directly. For example, `project_reliability_outage_union` merges overlapping outages and `project_accuracy_journal_trace` reconciles entries independently per transaction and currency.
- Track reviews accept `{"evidence":{"full_skill_id":{"measurement":123}}}`. Each reports passes, failures, invalid input, insufficient evidence, and the number of policies omitted. A review covers the data you supplied and does not fetch production telemetry.
- Catalog and runner limits still apply. Split large reviews or traces into appropriately scoped inputs. Preserve complete transaction groups, retry sequences and outage windows when splitting.

## Manifest

### Scalability

- `project_scale_queue_trace` — Simulate backlog and overflow across ordered windows. Input {capacity, initial, windows:[{arrivals,service_slots}]} using whole item counts. Arrivals precede service.
- `project_scale_load_knee` — Find the first tested load step exceeding latency or error budgets. Input {max_p99_ms,max_error_ratio,steps:[{rps,p99_ms,requests,errors}]} in increasing load order.
- `project_scale_evidence_review` — review supplied gates for this track.

| Skill | Pass condition |
| --- | --- |
| `project_scale_http_peak_service_rate` | `peak_rps <= sustainable_rps` |
| `project_scale_http_burst_concurrency` | `burst_concurrent <= inflight_limit` |
| `project_scale_http_payload_limit` | `largest_request_bytes <= request_limit_bytes` |
| `project_scale_http_upstream_slots` | `worker_count * slots_per_worker <= upstream_connection_limit` |
| `project_scale_http_response_bandwidth` | `peak_rps * response_bytes * 8 <= egress_bps` |
| `project_scale_http_socket_reserve` | `open_sockets + reserved_fds <= fd_limit` |
| `project_scale_http_keepalive_budget` | `idle_connections + active_connections <= connection_limit` |
| `project_scale_http_upload_deadline` | `upload_bytes <= upload_bytes_per_sec * deadline_sec` |
| `project_scale_http_route_quota` | `tenant_peak_rps <= tenant_quota_rps` |
| `project_scale_http_load_step_latency` | `load_test_p99_ms <= latency_slo_ms` |
| `project_scale_workers_drain_before_deadline` | `backlog_items + arrival_rps * drain_window_sec <= completion_rps * drain_window_sec` |
| `project_scale_workers_lease_capacity` | `leased_jobs <= available_workers * parallel_jobs_per_worker` |
| `project_scale_workers_prefetch_memory` | `prefetch_count * max_job_bytes <= prefetch_budget_bytes` |
| `project_scale_workers_visibility_window` | `job_p99_sec + ack_margin_sec <= visibility_timeout_sec` |
| `project_scale_workers_oldest_work_age` | `oldest_job_sec <= freshness_slo_sec` |
| `project_scale_workers_partition_parallelism` | `desired_consumers <= partition_count` |
| `project_scale_workers_batch_deadline` | `batch_size * per_item_ms + commit_ms <= batch_timeout_ms` |
| `project_scale_workers_dead_letter_storage` | `daily_rejected_jobs * rejection_bytes * retention_days <= dead_letter_budget_bytes` |
| `project_scale_workers_scale_up_lag` | `arrival_rps * startup_sec <= free_queue_slots` |
| `project_scale_workers_shutdown_drain` | `remaining_job_sec + flush_sec <= shutdown_grace_sec` |
| `project_scale_database_pool_rollover` | `old_pool_connections + new_pool_connections + admin_reserve <= db_connection_limit` |
| `project_scale_database_write_iops` | `writes_per_sec * iops_per_write <= provisioned_iops` |
| `project_scale_database_replica_replay` | `wal_generation_bytes_per_sec <= replica_apply_bytes_per_sec` |
| `project_scale_database_working_set` | `hot_data_bytes + hot_index_bytes <= buffer_budget_bytes` |
| `project_scale_database_transaction_hold` | `txn_p99_ms <= transaction_budget_ms` |
| `project_scale_database_lock_wait` | `lock_wait_p99_ms <= lock_wait_budget_ms` |
| `project_scale_database_migration_space` | `table_copy_bytes + rebuild_index_bytes + reserve_bytes <= free_disk_bytes` |
| `project_scale_database_maintenance_window` | `vacuum_sec + analyze_sec <= maintenance_window_sec` |
| `project_scale_database_shard_skew` | `busiest_shard_rps <= shard_capacity_rps` |
| `project_scale_database_query_scan_budget` | `rows_examined <= rows_returned * allowed_scan_factor` |
| `project_scale_cache_resident_dataset` | `key_count * mean_entry_bytes <= cache_budget_bytes` |
| `project_scale_cache_refill_headroom` | `uncached_rps + refill_rps <= origin_capacity_rps` |
| `project_scale_cache_eviction_pressure` | `evictions_per_min <= eviction_budget_per_min` |
| `project_scale_cache_invalidation_delay` | `invalidation_p99_ms <= staleness_budget_ms` |
| `project_scale_cache_hot_key_load` | `hottest_key_rps <= single_node_key_capacity_rps` |
| `project_scale_cache_ttl_freshness` | `effective_ttl_sec <= max_stale_sec` |
| `project_scale_cache_cache_warmup` | `warmup_bytes <= warmup_bytes_per_sec * rollout_window_sec` |
| `project_scale_cache_replication_memory` | `replica_count * dataset_bytes <= replica_memory_budget_bytes` |
| `project_scale_cache_stampede_fanout` | `concurrent_refills <= refill_concurrency_limit` |
| `project_scale_cache_negative_ttl` | `negative_ttl_sec <= new_record_visibility_sec` |
| `project_scale_storage_retention_growth` | `daily_ingest_bytes * retention_days <= usable_storage_bytes` |
| `project_scale_storage_inode_headroom` | `existing_files + forecast_files <= available_total_inodes` |
| `project_scale_storage_multipart_count` | `upload_parts <= part_count_limit` |
| `project_scale_storage_compaction_space` | `live_segment_bytes + temporary_compaction_bytes <= volume_capacity_bytes` |
| `project_scale_storage_snapshot_growth` | `snapshot_count * changed_bytes_per_snapshot <= snapshot_budget_bytes` |
| `project_scale_storage_restore_bandwidth` | `restore_bytes <= restore_bytes_per_sec * restore_window_sec` |
| `project_scale_storage_export_quota` | `daily_exports * mean_export_bytes <= daily_export_quota_bytes` |
| `project_scale_storage_object_listing` | `listed_objects <= page_size * max_pages` |
| `project_scale_storage_tier_transition` | `minimum_hot_days <= tier_after_days` |
| `project_scale_storage_storage_runway` | `daily_net_growth_bytes * lead_time_days + reserve_bytes <= free_bytes` |
| `project_scale_deployment_surge_slots` | `desired_replicas + max_surge <= cluster_slots` |
| `project_scale_deployment_zone_survival` | `peak_rps <= total_capacity_rps - largest_zone_capacity_rps` |
| `project_scale_deployment_canary_budget` | `canary_rps * evaluation_sec <= canary_exposure_requests` |
| `project_scale_deployment_startup_probe` | `cold_start_p99_sec <= probe_period_sec * failure_threshold` |
| `project_scale_deployment_cpu_reserve` | `reserved_cpu_mcores + requested_cpu_mcores <= allocatable_cpu_mcores` |
| `project_scale_deployment_memory_reserve` | `reserved_memory_bytes + requested_memory_bytes <= allocatable_memory_bytes` |
| `project_scale_deployment_scale_ceiling` | `predicted_replicas <= max_replicas` |
| `project_scale_deployment_scale_floor` | `minimum_healthy_replicas <= min_replicas` |
| `project_scale_deployment_image_pull` | `image_bytes <= pull_bytes_per_sec * pull_window_sec` |
| `project_scale_deployment_disruption_floor` | `healthy_replicas - concurrent_evictions >= required_ready_replicas` |

### Accuracy

- `project_accuracy_journal_trace` — Reconcile signed integer postings by transaction and currency. Input {postings:[{transaction,currency,minor_units}]}.
- `project_accuracy_classifier_slices` — Compute confusion counts, precision and recall per cohort. Input {cases:[{slice,expected,predicted}]} with boolean labels; undefined rates are null.
- `project_accuracy_evidence_review` — review supplied gates for this track.

| Skill | Pass condition |
| --- | --- |
| `project_accuracy_ingest_record_balance` | `source_count == accepted_count + rejected_count` |
| `project_accuracy_ingest_duplicate_ids` | `duplicate_id_count == 0` |
| `project_accuracy_ingest_missing_keys` | `missing_primary_keys == 0` |
| `project_accuracy_ingest_schema_rejections` | `schema_reject_count <= schema_reject_budget` |
| `project_accuracy_ingest_watermark_lag` | `arrival_lag_p99_sec <= watermark_delay_sec` |
| `project_accuracy_ingest_unexpected_fields` | `unexpected_field_count <= field_drift_budget` |
| `project_accuracy_ingest_encoding_failures` | `decode_error_count == 0` |
| `project_accuracy_ingest_source_partition_coverage` | `received_partition_count == expected_partition_count` |
| `project_accuracy_ingest_replay_duplicates` | `side_effects_after_replay == 0` |
| `project_accuracy_ingest_checksum_mismatches` | `payload_checksum_failures == 0` |
| `project_accuracy_ledger_double_entry_balance` | `debit_minor_units == credit_minor_units` |
| `project_accuracy_ledger_payout_reconciliation` | `authorized_minor_units == paid_minor_units + pending_minor_units + reversed_minor_units` |
| `project_accuracy_ledger_currency_mismatch` | `mixed_currency_postings == 0` |
| `project_accuracy_ledger_orphan_postings` | `postings_without_account == 0` |
| `project_accuracy_ledger_idempotent_posting` | `extra_postings_on_retry == 0` |
| `project_accuracy_ledger_fee_ceiling` | `charged_fee_minor_units <= contract_fee_limit_minor_units` |
| `project_accuracy_ledger_guarantee_capacity` | `reserved_minor_units + proposed_minor_units <= available_guarantee_minor_units` |
| `project_accuracy_ledger_installment_sum` | `scheduled_principal_minor_units == issued_principal_minor_units` |
| `project_accuracy_ledger_allocation_sum` | `payment_minor_units == principal_minor_units + interest_minor_units + fee_minor_units` |
| `project_accuracy_ledger_rounding_budget` | `rounding_difference_minor_units <= allowed_difference_minor_units` |
| `project_accuracy_api_contract_examples` | `passing_examples == declared_examples` |
| `project_accuracy_api_required_fields` | `missing_required_responses == 0` |
| `project_accuracy_api_enum_drift` | `unknown_enum_values == 0` |
| `project_accuracy_api_response_status` | `undocumented_status_count == 0` |
| `project_accuracy_api_pagination_gaps` | `missing_result_ids == 0` |
| `project_accuracy_api_pagination_overlap` | `repeated_result_ids == 0` |
| `project_accuracy_api_ordering` | `out_of_order_pairs == 0` |
| `project_accuracy_api_write_read_consistency` | `roundtrip_field_mismatches == 0` |
| `project_accuracy_api_version_compatibility` | `broken_consumer_contracts == 0` |
| `project_accuracy_api_timezone_roundtrip` | `timestamp_roundtrip_errors == 0` |
| `project_accuracy_prediction_precision_floor` | `true_positive_count >= minimum_precision_ratio * predicted_positive_count` |
| `project_accuracy_prediction_recall_floor` | `true_positive_count >= minimum_recall_ratio * actual_positive_count` |
| `project_accuracy_prediction_specificity_floor` | `true_negative_count >= minimum_specificity_ratio * actual_negative_count` |
| `project_accuracy_prediction_classification_accuracy` | `correct_predictions >= minimum_accuracy_ratio * labeled_predictions` |
| `project_accuracy_prediction_mae_ceiling` | `absolute_error_sum <= max_mae * prediction_count` |
| `project_accuracy_prediction_rmse_ceiling` | `squared_error_sum <= max_rmse * max_rmse * prediction_count` |
| `project_accuracy_prediction_coverage_floor` | `answered_cases >= minimum_coverage_ratio * eligible_cases` |
| `project_accuracy_prediction_abstention_budget` | `abstained_cases <= max_abstention_ratio * eligible_cases` |
| `project_accuracy_prediction_slice_gap` | `highest_slice_accuracy_ratio - lowest_slice_accuracy_ratio <= allowed_gap_ratio` |
| `project_accuracy_prediction_evaluation_leakage` | `overlapping_train_test_ids == 0` |
| `project_accuracy_transformation_join_fanout` | `output_rows <= left_rows * max_join_fanout` |
| `project_accuracy_transformation_unmatched_join` | `unmatched_rows <= unmatched_budget` |
| `project_accuracy_transformation_aggregate_reconciliation` | `detail_total_units == aggregate_total_units` |
| `project_accuracy_transformation_canonical_collision` | `canonical_key_collisions == 0` |
| `project_accuracy_transformation_unit_conversion` | `conversion_error_units <= allowed_conversion_error_units` |
| `project_accuracy_transformation_null_injection` | `unexpected_new_nulls == 0` |
| `project_accuracy_transformation_sequence_gaps` | `missing_sequence_numbers == 0` |
| `project_accuracy_transformation_dedup_balance` | `input_rows == unique_output_rows + removed_duplicate_rows` |
| `project_accuracy_transformation_reference_age` | `reference_age_days <= reference_valid_days` |
| `project_accuracy_transformation_deterministic_rebuild` | `changed_rows_on_identical_input == 0` |
| `project_accuracy_verification_requirement_coverage` | `requirements_with_passing_tests == required_features` |
| `project_accuracy_verification_boundary_coverage` | `tested_boundary_cases == identified_boundary_cases` |
| `project_accuracy_verification_mutation_score` | `killed_mutants >= minimum_score_ratio * viable_mutants` |
| `project_accuracy_verification_flaky_budget` | `inconsistent_tests <= allowed_flaky_tests` |
| `project_accuracy_verification_snapshot_drift` | `unapproved_snapshot_changes == 0` |
| `project_accuracy_verification_migration_parity` | `changed_business_values == 0` |
| `project_accuracy_verification_cross_runtime` | `cross_runtime_mismatches == 0` |
| `project_accuracy_verification_locale_roundtrip` | `locale_parse_failures == 0` |
| `project_accuracy_verification_golden_replay` | `golden_case_failures == 0` |
| `project_accuracy_verification_invariant_violations` | `invariant_failure_count == 0` |

### Safety

- `project_safety_approval_trace` — Audit maker/checker decisions against approvals required. Input {required,transactions:[{id,maker,approvers:[identity]}]}. Repeated identities count once.
- `project_safety_isolation_matrix` — Compare intended and observed access decisions by role/resource. Input {cases:[{role,resource,expected,allowed}]} with boolean decisions.
- `project_safety_evidence_review` — review supplied gates for this track.

| Skill | Pass condition |
| --- | --- |
| `project_safety_access_tenant_boundary` | `cross_tenant_reads == 0` |
| `project_safety_access_role_boundary` | `forbidden_actions_allowed == 0` |
| `project_safety_access_revocation_delay` | `revocation_propagation_sec <= revocation_slo_sec` |
| `project_safety_access_privileged_review` | `reviewed_privileged_accounts == privileged_accounts` |
| `project_safety_access_orphan_accounts` | `enabled_accounts_without_owner == 0` |
| `project_safety_access_session_idle` | `session_idle_timeout_sec <= max_idle_sec` |
| `project_safety_access_credential_age` | `oldest_service_key_days <= key_rotation_days` |
| `project_safety_access_break_glass` | `unreviewed_emergency_sessions == 0` |
| `project_safety_access_admin_mfa` | `mfa_enrolled_admins == active_admins` |
| `project_safety_access_permission_expiry` | `expired_grants_still_active == 0` |
| `project_safety_privacy_redaction` | `unredacted_sensitive_log_values == 0` |
| `project_safety_privacy_export_minimization` | `unapproved_export_fields == 0` |
| `project_safety_privacy_retention_expiry` | `records_past_retention == 0` |
| `project_safety_privacy_deletion_propagation` | `deletion_completion_hours <= deletion_deadline_hours` |
| `project_safety_privacy_consent_coverage` | `eligible_consents >= records_requiring_consent` |
| `project_safety_privacy_masking_coverage` | `masked_sensitive_columns == identified_sensitive_columns` |
| `project_safety_privacy_residency` | `out_of_region_objects == 0` |
| `project_safety_privacy_environment_separation` | `production_records_in_test == 0` |
| `project_safety_privacy_download_expiry` | `signed_link_lifetime_sec <= max_link_lifetime_sec` |
| `project_safety_privacy_subject_request_age` | `oldest_open_request_hours <= response_deadline_hours` |
| `project_safety_changes_destructive_preview` | `unreviewed_destructive_operations == 0` |
| `project_safety_changes_approval_quorum` | `independent_approvals >= required_approvals` |
| `project_safety_changes_rollback_evidence` | `rollback_drill_age_days <= rollback_evidence_max_days` |
| `project_safety_changes_artifact_signature` | `unsigned_release_artifacts == 0` |
| `project_safety_changes_deployment_freeze` | `unapproved_freeze_window_changes == 0` |
| `project_safety_changes_blast_radius` | `affected_tenants <= approved_tenant_limit` |
| `project_safety_changes_maintenance_overrun` | `elapsed_change_min <= approved_change_window_min` |
| `project_safety_changes_migration_backup` | `backup_age_before_migration_min <= max_backup_age_min` |
| `project_safety_changes_config_drift` | `unapproved_config_changes == 0` |
| `project_safety_changes_reversible_fraction` | `reversible_steps >= minimum_reversible_ratio * deployment_steps` |
| `project_safety_transactions_maker_checker` | `self_approved_transactions == 0` |
| `project_safety_transactions_per_operation_limit` | `requested_minor_units <= per_operation_limit_minor_units` |
| `project_safety_transactions_daily_limit` | `already_spent_minor_units + requested_minor_units <= daily_limit_minor_units` |
| `project_safety_transactions_duplicate_transfer` | `duplicate_provider_references == 0` |
| `project_safety_transactions_recipient_confirmation` | `unconfirmed_destination_changes == 0` |
| `project_safety_transactions_approval_expiry` | `seconds_since_approval <= approval_validity_sec` |
| `project_safety_transactions_pending_exposure` | `pending_minor_units + new_minor_units <= pending_exposure_limit_minor_units` |
| `project_safety_transactions_compensation_limit` | `compensated_minor_units <= original_minor_units` |
| `project_safety_transactions_posting_order` | `settled_before_authorization == 0` |
| `project_safety_transactions_withdrawal_reserve` | `available_minor_units - withdrawal_minor_units >= minimum_reserve_minor_units` |
| `project_safety_artifacts_build_provenance` | `artifacts_without_provenance == 0` |
| `project_safety_artifacts_dependency_lock` | `unlocked_runtime_dependencies == 0` |
| `project_safety_artifacts_license_review` | `unreviewed_license_findings == 0` |
| `project_safety_artifacts_image_identity` | `mutable_production_image_refs == 0` |
| `project_safety_artifacts_release_checksum` | `published_hash_mismatches == 0` |
| `project_safety_artifacts_secret_scan` | `confirmed_embedded_credentials == 0` |
| `project_safety_artifacts_component_inventory` | `unlisted_shipped_components == 0` |
| `project_safety_artifacts_patch_latency` | `critical_patch_age_days <= critical_patch_slo_days` |
| `project_safety_artifacts_build_reproducibility` | `nonreproducible_artifact_count == 0` |
| `project_safety_artifacts_dependency_origin` | `unapproved_dependency_sources == 0` |
| `project_safety_automation_human_gate` | `executed_without_required_confirmation == 0` |
| `project_safety_automation_tool_scope` | `out_of_scope_tool_calls == 0` |
| `project_safety_automation_action_budget` | `executed_actions <= action_budget` |
| `project_safety_automation_token_budget` | `input_tokens + output_tokens <= run_token_budget` |
| `project_safety_automation_cost_budget` | `spent_cost_micros + reserved_cost_micros <= cost_limit_micros` |
| `project_safety_automation_write_scope` | `writes_outside_workspace == 0` |
| `project_safety_automation_egress_policy` | `unapproved_egress_destinations == 0` |
| `project_safety_automation_stop_latency` | `cancel_to_quiescence_ms <= cancel_deadline_ms` |
| `project_safety_automation_review_sampling` | `reviewed_high_impact_actions == high_impact_actions` |
| `project_safety_automation_audit_binding` | `actions_without_request_id == 0` |

### Reliability

- `project_reliability_outage_union` — Calculate observed availability without double-counting overlapping outages. Input {window_sec,outages:[{start_sec,end_sec}]} using offsets within the window.
- `project_reliability_retry_trace` — Measure request amplification and exhausted operations. Input {attempts:[{id,attempt,success}]}; numbering begins at 1 and stops on success.
- `project_reliability_evidence_review` — review supplied gates for this track.

| Skill | Pass condition |
| --- | --- |
| `project_reliability_recovery_recovery_point` | `lost_data_window_sec <= rpo_sec` |
| `project_reliability_recovery_recovery_time` | `restore_to_ready_sec <= rto_sec` |
| `project_reliability_recovery_backup_success` | `successful_backups >= minimum_backup_success_ratio * scheduled_backups` |
| `project_reliability_recovery_backup_verification` | `unverified_restore_points == 0` |
| `project_reliability_recovery_backup_isolation` | `independent_backup_copies >= required_independent_copies` |
| `project_reliability_recovery_failover_detection` | `detection_sec + promotion_sec + routing_sec <= failover_budget_sec` |
| `project_reliability_recovery_restore_integrity` | `restored_object_hash_failures == 0` |
| `project_reliability_recovery_runbook_freshness` | `days_since_recovery_exercise <= exercise_interval_days` |
| `project_reliability_recovery_recovery_dependencies` | `unavailable_recovery_dependencies == 0` |
| `project_reliability_recovery_rejoin_backlog` | `rejoin_backlog_bytes <= catchup_bytes_per_sec * rejoin_window_sec` |
| `project_reliability_messaging_ack_loss` | `acked_messages_missing == 0` |
| `project_reliability_messaging_ordering_violation` | `out_of_order_deliveries == 0` |
| `project_reliability_messaging_consumer_lag` | `lag_messages <= consumer_lag_limit` |
| `project_reliability_messaging_poison_retries` | `attempts_before_quarantine <= max_delivery_attempts` |
| `project_reliability_messaging_outbox_gap` | `committed_events_without_outbox == 0` |
| `project_reliability_messaging_redrive_success` | `successfully_redriven >= minimum_redrive_ratio * redriven_messages` |
| `project_reliability_messaging_dedup_window` | `dedup_retention_sec >= max_redelivery_sec` |
| `project_reliability_messaging_heartbeat_timeout` | `heartbeat_interval_sec + heartbeat_jitter_sec < consumer_timeout_sec` |
| `project_reliability_messaging_retention_lag` | `consumer_outage_sec + replay_duration_sec <= broker_retention_sec` |
| `project_reliability_messaging_subscriber_coverage` | `healthy_subscriptions == expected_subscriptions` |
| `project_reliability_dependencies_timeout_hierarchy` | `downstream_timeout_ms + overhead_ms <= caller_timeout_ms` |
| `project_reliability_dependencies_circuit_recovery` | `probe_interval_sec <= dependency_recovery_slo_sec` |
| `project_reliability_dependencies_retry_load` | `primary_rps + retry_rps <= dependency_safe_rps` |
| `project_reliability_dependencies_fallback_success` | `successful_fallbacks >= minimum_fallback_ratio * fallback_attempts` |
| `project_reliability_dependencies_bulkhead_slots` | `critical_reserved_slots >= required_critical_slots` |
| `project_reliability_dependencies_dns_expiry` | `dns_cache_sec <= endpoint_move_budget_sec` |
| `project_reliability_dependencies_certificate_runway` | `certificate_days_remaining >= renewal_lead_days + response_margin_days` |
| `project_reliability_dependencies_dependency_depth` | `serial_dependency_p99_ms <= end_to_end_budget_ms` |
| `project_reliability_dependencies_connection_reuse` | `new_connections_per_sec <= handshake_capacity_per_sec` |
| `project_reliability_dependencies_degraded_capacity` | `critical_path_rps <= fallback_capacity_rps` |
| `project_reliability_telemetry_scrape_gaps` | `longest_scrape_gap_sec <= max_scrape_gap_sec` |
| `project_reliability_telemetry_trace_coverage` | `traced_requests >= min_trace_ratio * observed_requests` |
| `project_reliability_telemetry_log_delivery` | `lost_log_events <= log_loss_budget` |
| `project_reliability_telemetry_metric_cardinality` | `active_time_series <= time_series_budget` |
| `project_reliability_telemetry_alert_delay` | `event_to_page_sec <= page_deadline_sec` |
| `project_reliability_telemetry_false_alerts` | `false_pages <= false_page_budget` |
| `project_reliability_telemetry_silent_alerts` | `undelivered_test_pages == 0` |
| `project_reliability_telemetry_clock_skew` | `maximum_clock_skew_ms <= clock_skew_budget_ms` |
| `project_reliability_telemetry_telemetry_buffer` | `telemetry_bytes_per_sec * collector_outage_sec <= buffer_capacity_bytes` |
| `project_reliability_telemetry_slo_coverage` | `instrumented_critical_flows == critical_flow_count` |
| `project_reliability_release_canary_error_rate` | `canary_errors <= canary_error_limit_ratio * canary_requests` |
| `project_reliability_release_baseline_regression` | `candidate_p95_ms - baseline_p95_ms <= allowed_regression_ms` |
| `project_reliability_release_rollback_time` | `rollback_completion_sec <= rollback_target_sec` |
| `project_reliability_release_health_convergence` | `healthy_after_rollout == desired_after_rollout` |
| `project_reliability_release_restart_churn` | `restarts_in_window <= restart_budget` |
| `project_reliability_release_readiness_flaps` | `readiness_transitions <= readiness_flap_budget` |
| `project_reliability_release_deployment_frequency` | `successful_deployments >= required_deployments` |
| `project_reliability_release_change_failure` | `failed_changes <= change_failure_limit_ratio * deployed_changes` |
| `project_reliability_release_lead_time` | `commit_to_production_hours <= lead_time_target_hours` |
| `project_reliability_release_rollback_compatibility` | `failed_old_version_reads == 0` |
| `project_reliability_distributed_quorum_survival` | `voting_members - unavailable_members >= commit_quorum` |
| `project_reliability_distributed_split_brain` | `simultaneous_primary_intervals == 0` |
| `project_reliability_distributed_fencing` | `stale_writer_commits == 0` |
| `project_reliability_distributed_read_your_writes` | `stale_reads_after_ack == 0` |
| `project_reliability_distributed_lost_update` | `overwritten_committed_updates == 0` |
| `project_reliability_distributed_monotonic_reads` | `observed_version_regressions == 0` |
| `project_reliability_distributed_replication_delay` | `replication_p99_ms <= replication_slo_ms` |
| `project_reliability_distributed_lease_renewal` | `renew_interval_ms + max_pause_ms + network_margin_ms < lease_duration_ms` |
| `project_reliability_distributed_convergence` | `divergent_replicas_after_window == 0` |
| `project_reliability_distributed_checkpoint_gap` | `time_since_checkpoint_sec <= checkpoint_interval_limit_sec` |
| `project_reliability_operations_incident_ack` | `incident_to_ack_min <= ack_target_min` |
| `project_reliability_operations_incident_restore` | `incident_to_restore_min <= restore_target_min` |
| `project_reliability_operations_oncall_coverage` | `uncovered_support_minutes == 0` |
| `project_reliability_operations_runbook_coverage` | `alerts_with_runbook == actionable_alerts` |
| `project_reliability_operations_action_closure` | `overdue_corrective_actions <= overdue_action_budget` |
| `project_reliability_operations_recurrent_incidents` | `repeat_incidents <= recurrence_budget` |
| `project_reliability_operations_maintenance_notice` | `notice_lead_hours >= required_notice_hours` |
| `project_reliability_operations_service_ownership` | `unowned_production_services == 0` |
| `project_reliability_operations_drill_coverage` | `exercised_critical_scenarios == defined_critical_scenarios` |
| `project_reliability_operations_unplanned_outage` | `unplanned_downtime_minutes <= outage_budget_minutes` |
