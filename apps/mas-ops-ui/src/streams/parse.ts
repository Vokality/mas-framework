import type { StreamFrame, StreamEventPayload } from "./types";

export function parseSseReplay(body: string): Array<StreamFrame> {
  return body
    .split(/\r?\n\r?\n/)
    .map((chunk) => chunk.trim())
    .filter((chunk) => chunk.length > 0)
    .flatMap((chunk) => parseSseChunk(chunk));
}

function parseSseChunk(chunk: string): Array<StreamFrame> {
  let eventName = "message";
  let eventId: string | null = null;
  let retry: number | null = null;
  const dataLines: Array<string> = [];

  for (const line of chunk.split(/\r?\n/)) {
    if (line.startsWith(":")) {
      continue;
    }
    const separatorIndex = line.indexOf(":");
    const field = separatorIndex === -1 ? line : line.slice(0, separatorIndex);
    const rawValue =
      separatorIndex === -1 ? "" : line.slice(separatorIndex + 1).replace(/^ /, "");

    if (field === "event") {
      eventName = rawValue;
      continue;
    }
    if (field === "id") {
      eventId = rawValue;
      continue;
    }
    if (field === "retry") {
      const parsedRetry = Number.parseInt(rawValue, 10);
      retry = Number.isNaN(parsedRetry) ? null : parsedRetry;
      continue;
    }
    if (field === "data") {
      dataLines.push(rawValue);
    }
  }

  if (dataLines.length === 0) {
    return [];
  }

  return [
    {
      id: eventId,
      event: eventName,
      retry,
      data: JSON.parse(dataLines.join("\n")) as StreamEventPayload,
    },
  ];
}
