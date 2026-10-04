/** Generated from Python management DTOs. Run npm run contracts; do not edit. */

/**
 * Circuit breaker states.
 */
export type CircuitState = 'closed' | 'open' | 'half_open';
export type BrokerStatus = 'healthy' | 'degraded' | 'stopped' | 'unknown';
export type ObservationAttribute = string | number | boolean;

/**
 * The management reader responses, serialized without omitted defaults.
 */
export interface DashboardContracts {
  history: PerformancePoint[];
  snapshot: ManagementSnapshot;
  trace: TraceDetail;
  traces: TraceSummary[];
}
/**
 * Measured fleet traffic and paired delivery timings for an interval.
 */
export interface PerformancePoint {
  accepted_messages: number;
  accepted_rate: number | null;
  clock_scope: string;
  complete: boolean;
  counter_complete: boolean;
  delivery_acks: number;
  delivery_nacks: number;
  end_to_end_p95_ms: number | null;
  finished_at: number;
  latency_coverage: number | null;
  latency_samples: number;
  redis_errors: number;
  rejected_messages: number;
  started_at: number;
}
/**
 * Typed management response, ready for presentation.
 */
export interface ManagementSnapshot {
  agents: AgentSummary[];
  backlog: number | null;
  broker_id: string | null;
  circuits: {
    [k: string]: CircuitStatus;
  } | null;
  dead_letters: number | null;
  exporters: ExportHealth[];
  features: {
    [k: string]: boolean;
  };
  fleet: FleetSnapshot | null;
  generated_at: number;
  health: HealthReport;
  queues: QueueSummary[] | null;
  queues_complete: boolean;
  recent_activity: ActivitySummary[] | null;
  scope: string;
  telemetry: TelemetrySnapshot;
  uptime_seconds: number;
}
/**
 * Shared agent availability and this broker's local connected instances.
 */
export interface AgentSummary {
  agent_id: string;
  capabilities: string[];
  sessions: SessionSummary[];
  status: 'active' | 'inactive' | 'degraded' | 'unknown';
}
/**
 * Live instance and delivery-worker state.
 */
export interface SessionSummary {
  inflight: number;
  instance_id: string;
  outbound: number;
  worker_running: boolean;
}
/**
 * Validated circuit state and its admission decision.
 */
export interface CircuitStatus {
  allowed: boolean;
  failure_count: number;
  last_failure_time: number | null;
  opened_at: number | null;
  state: CircuitState;
  success_count: number;
}
/**
 * Actual exporter outcomes for one telemetry signal.
 */
export interface ExportHealth {
  age_seconds: number | null;
  attempts: number;
  configured: boolean;
  exported_items: number;
  failures: number;
  last_attempt_at: number | null;
  last_error: string | null;
  last_success_at: number | null;
  signal: 'traces' | 'metrics';
  status: 'disabled' | 'pending' | 'healthy' | 'degraded' | 'stale';
  successes: number;
}
/**
 * A render-ready fleet view including retained conditions and SLO scope.
 */
export interface FleetSnapshot {
  alerts: OperationalAlert[];
  brokers: FleetMember[];
  complete: boolean;
  generated_at: number;
  latency_window_seconds: number;
  performance: PerformancePoint;
  retention_seconds: number;
  scope: string;
  stale_after_seconds: number;
  status: BrokerStatus;
  targets: SloTargets;
  trace_limit: number;
  trace_sample_every: number;
}
/**
 * An active or resolved operational condition, without notifications.
 */
export interface OperationalAlert {
  alert_id: string;
  detail: string;
  kind: 'broker_health' | 'broker_stale' | 'export_failure' | 'latency_slo' | 'throughput_slo' | 'coverage_gap';
  opened_at: number;
  resolved_at: number | null;
  severity: 'critical' | 'warning' | 'info';
  status: 'active' | 'resolved';
  title: string;
  updated_at: number;
}
/**
 * A broker heartbeat with server-evaluated freshness.
 */
export interface FleetMember {
  age_seconds: number;
  fresh: boolean;
  observation: BrokerObservation;
  status: 'healthy' | 'degraded' | 'stopped' | 'unknown' | 'stale';
}
/**
 * A sequenced heartbeat from a specific broker incarnation.
 */
export interface BrokerObservation {
  broker_id: string;
  counters: BrokerCounters;
  exporters: ExportHealth[];
  grpc_address: string;
  instance_id: string;
  issues: string[];
  management_url: string | null;
  observed_at: number;
  redis_available: boolean;
  redis_latency_ms: number | null;
  sequence: number;
  sessions: BrokerSession[];
  started_at: number;
  status: BrokerStatus;
}
/**
 * Counters attributable to one broker incarnation.
 */
export interface BrokerCounters {
  accepted_messages: number;
  delivery_acks: number;
  delivery_nacks: number;
  dropped_scope_updates: number;
  dropped_spans: number;
  redis_errors: number;
  rejected_messages: number;
  scope_complete: boolean;
}
/**
 * Agent transport and worker state on a fleet member.
 */
export interface BrokerSession {
  agent_id: string;
  inflight: number;
  instance_id: string;
  outbound: number;
  worker_running: boolean;
}
/**
 * The user's capacity and delivery latency targets.
 */
export interface SloTargets {
  accepted_rate: number;
  end_to_end_p95_ms: number;
}
/**
 * Readiness and dependency health, without backend exception details.
 */
export interface HealthReport {
  issues: string[];
  redis_available: boolean;
  redis_latency_ms: number | null;
  status: 'healthy' | 'degraded' | 'stopped';
}
/**
 * Durable work waiting for delivery or acknowledgement.
 */
export interface QueueSummary {
  pending: number;
  stream: string;
  waiting: number | null;
}
/**
 * Policy decision metadata; message payloads and hashes are excluded.
 */
export interface ActivitySummary {
  correlation_id: string | null;
  decision: string;
  latency_ms: number;
  message_id: string;
  message_type: string | null;
  sender_id: string;
  target_id: string;
  timestamp: number;
  violations: string[];
}
/**
 * Bounded process counters available even when OTLP export is disabled.
 */
export interface TelemetrySnapshot {
  active_sessions: number;
  dead_letter_errors: number;
  dead_letter_writes: number;
  delivery_acks: number;
  delivery_nacks: number;
  dropped_scope_updates: number;
  export_enabled: boolean;
  ingress: {
    [k: string]: number;
  };
  policy_latency_max_ms: number;
  policy_latency_mean_ms: number;
  policy_samples: number;
  redis_errors: number;
  retryable_nacks: number;
  scope_complete: boolean;
}
/**
 * A bounded trace waterfall prepared for direct presentation.
 */
export interface TraceDetail {
  clock_scope: string;
  spans: TraceSpan[];
  summary: TraceSummary;
}
/**
 * One span with backend-computed order, nesting and waterfall geometry.
 */
export interface TraceSpan {
  depth: number;
  duration_ms: number;
  offset_ms: number;
  span: ObservedSpan;
}
/**
 * Safe span metadata, without business payloads, headers or exceptions.
 */
export interface ObservedSpan {
  attributes: {
    [k: string]: ObservationAttribute;
  };
  failed: boolean;
  finished_unix_ns: number;
  name: string;
  parent_span_id: string | null;
  service_name: string;
  span_id: string;
  started_unix_ns: number;
  trace_id: string;
}
/**
 * Payload-free trace identity and measured span coverage.
 */
export interface TraceSummary {
  clock_skew_detected: boolean;
  complete: boolean;
  end_to_end_ms: number | null;
  error_count: number;
  finished_at: number;
  message_ids: string[];
  services: string[];
  span_count: number;
  started_at: number;
  trace_id: string;
}
