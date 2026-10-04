import type { ManagementSnapshot, PerformancePoint, TraceSummary, TraceDetail } from './contracts';
import { ApiError, getSnapshot, getHistory, getTraces, getTrace } from './api';
import type { Route } from './router';

/** Page-scoped, cancellable async reads; credentials exist only in this instance. */
export class Dashboard {
  snapshot = $state.raw<ManagementSnapshot | null>(null);
  history = $state.raw<PerformancePoint[] | null>(null);
  traces = $state.raw<TraceSummary[] | null>(null);
  detail = $state.raw<TraceDetail | null>(null);
  snapshotError = $state('');
  historyError = $state('');
  tracesError = $state('');
  detailError = $state('');
  loading = $state(false);
  accessRequired = $state(false);
  accessMessage = $state('');
  #token = '';
  #controller: AbortController | null = null;
  #generation = 0;
  #route: Route = { page: 'overview', traceId: null };
  #historyLimit = 120;
  #traceLimit = 100;

  select(route: Route, historyLimit: number, traceLimit: number): void {
    if (
      route.page === this.#route.page &&
      route.traceId === this.#route.traceId &&
      historyLimit === this.#historyLimit &&
      traceLimit === this.#traceLimit
    )
      return;
    const traceChanged = route.traceId !== this.#route.traceId;
    this.stop();
    this.#route = route;
    this.#historyLimit = historyLimit;
    this.#traceLimit = traceLimit;
    if (traceChanged) {
      this.detail = null;
      this.detailError = '';
    }
    if (!this.accessRequired) void this.refresh();
  }

  connect(token: string): void {
    this.stop();
    this.#token = token;
    this.accessRequired = false;
    this.accessMessage = '';
    this.clear();
    void this.refresh();
  }

  disconnect(): void {
    this.stop();
    this.#token = '';
    this.clear();
    this.accessRequired = true;
    this.accessMessage = 'Enter a management reader token to connect.';
  }

  private clear(): void {
    this.snapshot = null;
    this.history = null;
    this.traces = null;
    this.detail = null;
    this.snapshotError = '';
    this.historyError = '';
    this.tracesError = '';
    this.detailError = '';
  }

  stop(): void {
    this.#generation++;
    this.#controller?.abort();
    this.#controller = null;
    this.loading = false;
  }

  async refresh(): Promise<void> {
    if (this.loading || this.accessRequired || document.hidden) return;
    const controller = new AbortController();
    this.#controller = controller;
    const generation = ++this.#generation,
      token = this.#token;
    const signal = AbortSignal.any([controller.signal, AbortSignal.timeout(8000)]);
    this.loading = true;
    const read = async <T>(
      fetcher: () => Promise<T>,
      receive: (value: T) => void,
      reject: (message: string) => void,
    ): Promise<void> => {
      try {
        const value = await fetcher();
        if (generation === this.#generation) {
          receive(value);
          reject('');
        }
      } catch (error: unknown) {
        if (generation !== this.#generation) return;
        if (error instanceof ApiError && (error.status === 401 || error.status === 403)) {
          this.stop();
          this.clear();
          this.accessRequired = true;
          this.accessMessage =
            error.status === 403
              ? 'This identity lacks the required management reader scope or operator role.'
              : 'Authentication is required or your reader token has expired.';
        } else if (!controller.signal.aborted) {
          reject(
            error instanceof ApiError
              ? error.message
              : 'The management request failed or timed out.',
          );
        }
      }
    };
    const reads = [
      read(
        () => getSnapshot(token, signal),
        (value) => (this.snapshot = value),
        (message) => (this.snapshotError = message),
      ),
    ];
    if (this.#route.page === 'performance')
      reads.push(
        read(
          () => getHistory(token, this.#historyLimit, signal),
          (value) => (this.history = value),
          (message) => (this.historyError = message),
        ),
      );
    if (this.#route.page === 'traces') {
      const id = this.#route.traceId;
      reads.push(
        id
          ? read(
              () => getTrace(token, id, signal),
              (value) => (this.detail = value),
              (message) => (this.detailError = message),
            )
          : read(
              () => getTraces(token, this.#traceLimit, signal),
              (value) => (this.traces = value),
              (message) => (this.tracesError = message),
            ),
      );
    }
    await Promise.all(reads);
    if (generation === this.#generation) {
      this.loading = false;
      this.#controller = null;
    }
  }
}
