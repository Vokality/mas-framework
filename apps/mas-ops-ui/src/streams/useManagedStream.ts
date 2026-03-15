import {
  useEffect,
  useEffectEvent,
  useRef,
  useState,
  type MutableRefObject,
} from "react";

import { parseSseReplay } from "./parse";
import {
  clearLastEventId,
  readLastEventId,
  writeLastEventId,
} from "./storage";
import type { StreamFrame, StreamEventPayload } from "./types";

export type StreamConnectionState =
  | "idle"
  | "connecting"
  | "live"
  | "reconnecting"
  | "forbidden"
  | "error";

type UseManagedStreamOptions = {
  enabled: boolean;
  eventTypes: Array<string>;
  liveUrl: string;
  replay(lastEventId: string | null): Promise<Response>;
  reconnectDelayMs?: number;
  scopeKey: string;
  onError?(message: string): void;
  onEvent(frame: StreamFrame): void | Promise<void>;
  onForbidden?(): void;
};

type ReplayOutcome = "ok" | "forbidden" | "retry";

export function useManagedStream({
  enabled,
  eventTypes,
  liveUrl,
  replay,
  reconnectDelayMs = 1_000,
  scopeKey,
  onError,
  onEvent,
  onForbidden,
}: UseManagedStreamOptions): StreamConnectionState {
  const [connectionState, setConnectionState] =
    useState<StreamConnectionState>("idle");
  const reconnectTimeoutRef = useRef<number | null>(null);
  const sourceRef = useRef<EventSource | null>(null);
  const eventTypesKey = eventTypes.join("|");

  const handleEvent = useEffectEvent((frame: StreamFrame) => {
    if (frame.id !== null) {
      writeLastEventId(scopeKey, frame.id);
    }
    void onEvent(frame);
  });

  const handleForbidden = useEffectEvent(() => {
    clearLastEventId(scopeKey);
    onForbidden?.();
  });

  const handleError = useEffectEvent((message: string) => {
    onError?.(message);
  });

  const performReplayRequest = useEffectEvent(
    (lastEventId: string | null) => replay(lastEventId),
  );

  useEffect(() => {
    if (!enabled) {
      closeEventSource(sourceRef);
      clearReconnectTimeout(reconnectTimeoutRef);
      setConnectionState("idle");
      return;
    }

    let active = true;

    async function performReplay(): Promise<ReplayOutcome> {
      const lastEventId = readLastEventId(scopeKey);
      const response = await performReplayRequest(lastEventId);
      if (response.status === 403) {
        if (active) {
          setConnectionState("forbidden");
          handleForbidden();
        }
        return "forbidden";
      }
      if (!response.ok) {
        if (active) {
          setConnectionState("error");
          handleError(`Replay request failed with ${response.status}.`);
        }
        return "retry";
      }

      const replayFrames = parseSseReplay(await response.text());
      for (const frame of replayFrames) {
        if (!active || !eventTypes.includes(frame.event)) {
          continue;
        }
        handleEvent(frame);
      }
      return "ok";
    }

    function connectLiveStream() {
      if (!active) {
        return;
      }

      closeEventSource(sourceRef);
      const source = new EventSource(liveUrl, {
        withCredentials: true,
      });
      sourceRef.current = source;

      source.onopen = () => {
        if (active) {
          setConnectionState("live");
        }
      };

      for (const eventType of eventTypes) {
        source.addEventListener(eventType, (event) => {
          if (!active) {
            return;
          }
          const message = event as MessageEvent<string>;
          const frame: StreamFrame = {
            id: message.lastEventId || readLastEventId(scopeKey),
            event: event.type,
            retry: null,
            data: JSON.parse(message.data) as StreamEventPayload,
          };
          handleEvent(frame);
          setConnectionState("live");
        });
      }

      source.onerror = () => {
        if (!active) {
          return;
        }
        closeEventSource(sourceRef);
        scheduleReconnect();
      };
    }

    function scheduleReconnect() {
      if (!active || reconnectTimeoutRef.current !== null) {
        return;
      }
      setConnectionState("reconnecting");
      reconnectTimeoutRef.current = window.setTimeout(() => {
        reconnectTimeoutRef.current = null;
        void replayAndReconnect();
      }, reconnectDelayMs);
    }

    async function replayAndReconnect() {
      const outcome = await performReplay();
      if (!active || outcome === "forbidden") {
        return;
      }
      if (outcome === "retry") {
        scheduleReconnect();
        return;
      }
      connectLiveStream();
    }

    setConnectionState("connecting");
    void replayAndReconnect();

    return () => {
      active = false;
      clearReconnectTimeout(reconnectTimeoutRef);
      closeEventSource(sourceRef);
    };
  }, [
    enabled,
    eventTypesKey,
    liveUrl,
    reconnectDelayMs,
    scopeKey,
  ]);

  return connectionState;
}

function clearReconnectTimeout(timeoutRef: MutableRefObject<number | null>): void {
  if (timeoutRef.current === null) {
    return;
  }
  window.clearTimeout(timeoutRef.current);
  timeoutRef.current = null;
}

function closeEventSource(
  sourceRef: MutableRefObject<EventSource | null>,
): void {
  sourceRef.current?.close();
  sourceRef.current = null;
}
