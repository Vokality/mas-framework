import "@testing-library/jest-dom/vitest";

class TestRequest extends Request {
  readonly signal: AbortSignal;

  constructor(input: RequestInfo | URL, init: RequestInit = {}) {
    const { signal, ...rest } = init;
    super(input, rest);
    this.signal = signal ?? new AbortController().signal;
  }
}

vi.stubGlobal("Request", TestRequest);

Object.assign(window, {
  AbortController: globalThis.AbortController,
  AbortSignal: globalThis.AbortSignal,
  Headers: globalThis.Headers,
  Request: TestRequest,
  Response: globalThis.Response,
  fetch: globalThis.fetch.bind(globalThis),
});
