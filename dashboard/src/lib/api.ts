/** Validated, read-only management requests. Tokens are passed from memory only. */
import type { ManagementSnapshot, PerformancePoint, TraceDetail, TraceSummary } from './contracts';
import { isHistory, isSnapshot, isTrace, isTraces } from './validators.js';

export type ApiErrorKind =
  | 'unauthorized'
  | 'forbidden'
  | 'not_found'
  | 'unavailable'
  | 'invalid_response'
  | 'invalid_request'
  | 'network'
  | 'timeout'
  | 'http';

export class ApiError extends Error {
  readonly status: number | null;
  readonly kind: ApiErrorKind;

  constructor(kind: ApiErrorKind, message: string, status: number | null = null) {
    super(message);
    this.name = 'ApiError';
    this.kind = kind;
    this.status = status;
  }
}

const REQUEST_TIMEOUT_MS = 10_000;

async function read<T>(
  path: string,
  token: string,
  validate: (value: unknown) => value is T,
  signal?: AbortSignal,
): Promise<T> {
  if (token !== '' && !/^[!-~]+$/.test(token)) {
    throw new ApiError('invalid_request', 'The reader token has an invalid format.');
  }
  const timeout = AbortSignal.timeout(REQUEST_TIMEOUT_MS);
  const requestSignal = AbortSignal.any(signal ? [signal, timeout] : [timeout]);
  try {
    requestSignal.throwIfAborted();
    const headers = new Headers({ Accept: 'application/json' });
    if (token) headers.set('Authorization', `Bearer ${token}`);
    const response = await fetch(path, {
      headers,
      signal: requestSignal,
      cache: 'no-store',
      credentials: 'omit',
      redirect: 'error',
    });
    if (!response.ok) {
      if (response.status === 401)
        throw new ApiError('unauthorized', 'Reader authentication is required.', 401);
      if (response.status === 403)
        throw new ApiError('forbidden', 'This identity does not have management read access.', 403);
      if (response.status === 404)
        throw new ApiError('not_found', 'The retained trace is no longer available.', 404);
      if (response.status === 503)
        throw new ApiError('unavailable', 'Management data is temporarily unavailable.', 503);
      throw new ApiError(
        'http',
        `The management request failed (HTTP ${response.status}).`,
        response.status,
      );
    }
    const mediaType = response.headers.get('content-type')?.split(';', 1)[0]?.trim().toLowerCase();
    if (mediaType !== 'application/json')
      throw new ApiError(
        'invalid_response',
        'Management returned an invalid response.',
        response.status,
      );
    let value: unknown;
    try {
      value = await response.json();
    } catch {
      if (requestSignal.aborted) requestSignal.throwIfAborted();
      throw new ApiError(
        'invalid_response',
        'Management returned an invalid response.',
        response.status,
      );
    }
    if (!validate(value))
      throw new ApiError(
        'invalid_response',
        'Management returned data that does not match its API contract.',
        response.status,
      );
    return value;
  } catch (error: unknown) {
    if (signal?.aborted) throw new DOMException('Management request cancelled.', 'AbortError');
    if (timeout.aborted) throw new ApiError('timeout', 'The management request timed out.');
    if (error instanceof ApiError) throw error;
    throw new ApiError('network', 'The management service could not be reached.');
  }
}

function readLimit(limit: number): number {
  if (!Number.isInteger(limit) || limit < 1 || limit > 500) {
    throw new ApiError('invalid_request', 'Choose between 1 and 500 retained records.');
  }
  return limit;
}

export function getSnapshot(token: string, signal?: AbortSignal): Promise<ManagementSnapshot> {
  return read('/api/snapshot', token, isSnapshot, signal);
}

export async function getHistory(
  token: string,
  limit: number,
  signal?: AbortSignal,
): Promise<PerformancePoint[]> {
  return read(`/api/history?limit=${readLimit(limit)}`, token, isHistory, signal);
}

export async function getTraces(
  token: string,
  limit: number,
  signal?: AbortSignal,
): Promise<TraceSummary[]> {
  return read(`/api/traces?limit=${readLimit(limit)}`, token, isTraces, signal);
}

export async function getTrace(
  token: string,
  id: string,
  signal?: AbortSignal,
): Promise<TraceDetail> {
  if (!/^[0-9a-f]{32}$/.test(id))
    throw new ApiError('invalid_request', 'Choose a valid retained trace.');
  return read(`/api/traces/${id}`, token, isTrace, signal);
}
