import { createContext, useCallback, useContext, useEffect, useRef, type ReactNode } from "react";

// The client half of graph reactivity (ADR 0008). One app-wide EventSource on
// /api/events; Cards subscribe and re-query their own scope when a relevant
// change arrives — the backend broadcasts *what changed*, each Card decides if
// it cares. Distinct from the per-turn /api/chat stream.

export type ChangeEvent = {
  type: string; // "change"
  resource?: string; // "document" | "domain" | "entity" | ...
  path?: string; // document path
  domain?: string;
  entity?: string;
  [key: string]: unknown;
};

type Listener = (e: ChangeEvent) => void;
type Subscribe = (listener: Listener) => () => void;

const ChangeStreamContext = createContext<Subscribe>(() => () => {});

export function useChangeStream(): Subscribe {
  return useContext(ChangeStreamContext);
}

export function ChangeStreamProvider({ children }: { children: ReactNode }) {
  const listeners = useRef<Set<Listener>>(new Set());

  useEffect(() => {
    // EventSource auto-reconnects on transient drops, which is exactly what we want
    // for a long-lived broadcast channel.
    const es = new EventSource("/api/events");
    es.onmessage = (m) => {
      try {
        const e = JSON.parse(m.data) as ChangeEvent;
        listeners.current.forEach((l) => l(e));
      } catch {
        /* ignore malformed frames (e.g. comments never reach onmessage) */
      }
    };
    return () => es.close();
  }, []);

  const subscribe = useCallback<Subscribe>((listener) => {
    listeners.current.add(listener);
    return () => {
      listeners.current.delete(listener);
    };
  }, []);

  return <ChangeStreamContext.Provider value={subscribe}>{children}</ChangeStreamContext.Provider>;
}
