/** Generated guards share the exact schema used to generate contracts.ts. */
import type { ManagementSnapshot, PerformancePoint, TraceSummary, TraceDetail } from './contracts';
export declare function isSnapshot(data: unknown): data is ManagementSnapshot;
export declare function isHistory(data: unknown): data is PerformancePoint[];
export declare function isTraces(data: unknown): data is TraceSummary[];
export declare function isTrace(data: unknown): data is TraceDetail;
