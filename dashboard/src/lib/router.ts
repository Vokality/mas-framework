/** Small history router for the Python-hosted management pages. */
export const pages = [
  { id: 'overview', label: 'Overview', group: 'System' },
  { id: 'fleet', label: 'Broker fleet', group: 'System' },
  { id: 'performance', label: 'Performance', group: 'Observe' },
  { id: 'traces', label: 'Message traces', group: 'Observe' },
  { id: 'alerts', label: 'Alerts', group: 'Observe' },
  { id: 'agents', label: 'Agents', group: 'Operate' },
  { id: 'queues', label: 'Delivery queues', group: 'Operate' },
  { id: 'activity', label: 'Activity', group: 'Operate' },
  { id: 'telemetry', label: 'Telemetry', group: 'Operate' },
] as const;
export type PageId = (typeof pages)[number]['id'];
export interface Route {
  page: PageId;
  traceId: string | null;
}
export function readRoute(pathname: string): Route | null {
  if (pathname === '/') return { page: 'overview', traceId: null };
  const page = pages.find((page) => pathname === `/${page.id}`);
  if (page) return { page: page.id, traceId: null };
  const match = /^\/traces\/([0-9a-f]{32})$/.exec(pathname);
  if (match && /[1-9a-f]/.test(match[1])) return { page: 'traces', traceId: match[1] };
  return null;
}
