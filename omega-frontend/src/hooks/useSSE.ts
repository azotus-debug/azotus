"use client";

import { useState, useEffect, useRef, useCallback } from "react";

const API_BASE = process.env.NEXT_PUBLIC_API_URL || "";

export interface SSEEvent {
  type: string;
  data: any;
  timestamp: string;
}

type EventHandler = (data: any) => void;

export interface SSEConnection {
  connected: boolean;
  reconnecting: boolean;
  subscribe: (eventType: string, handler: EventHandler) => () => void;
}

export function useSSE(): SSEConnection {
  const [connected, setConnected] = useState(false);
  const [reconnecting, setReconnecting] = useState(false);

  const eventSourceRef = useRef<EventSource | null>(null);
  const subscribersRef = useRef<Map<string, Set<EventHandler>>>(new Map());
  const retryDelayRef = useRef(1000);
  const retryTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const mountedRef = useRef(true);

  const connect = useCallback(() => {
    // Close any existing connection
    if (eventSourceRef.current) {
      eventSourceRef.current.close();
      eventSourceRef.current = null;
    }

    if (!mountedRef.current) return;

    const url = `${API_BASE}/api/events`;
    const es = new EventSource(url);
    eventSourceRef.current = es;

    es.onopen = () => {
      if (!mountedRef.current) return;
      setConnected(true);
      setReconnecting(false);
      retryDelayRef.current = 1000; // Reset backoff on successful connection
    };

    es.onmessage = (event) => {
      if (!mountedRef.current) return;

      try {
        const parsed: SSEEvent = JSON.parse(event.data);
        const handlers = subscribersRef.current.get(parsed.type);
        if (handlers) {
          handlers.forEach((handler) => {
            try {
              handler(parsed.data);
            } catch (err) {
              console.error(`[SSE] Handler error for "${parsed.type}":`, err);
            }
          });
        }
      } catch (err) {
        console.error("[SSE] Failed to parse event:", err);
      }
    };

    es.onerror = () => {
      if (!mountedRef.current) return;

      console.warn("[SSE] Connection error, scheduling reconnect...");
      es.close();
      eventSourceRef.current = null;
      setConnected(false);
      setReconnecting(true);

      // Exponential backoff: 1s, 2s, 4s, 8s, ... max 30s
      const delay = retryDelayRef.current;
      retryTimerRef.current = setTimeout(() => {
        if (mountedRef.current) {
          connect();
        }
      }, delay);
      retryDelayRef.current = Math.min(delay * 2, 30000);
    };
  }, []);

  const subscribe = useCallback(
    (eventType: string, handler: EventHandler): (() => void) => {
      if (!subscribersRef.current.has(eventType)) {
        subscribersRef.current.set(eventType, new Set());
      }
      subscribersRef.current.get(eventType)!.add(handler);

      // Return unsubscribe function
      return () => {
        const handlers = subscribersRef.current.get(eventType);
        if (handlers) {
          handlers.delete(handler);
          if (handlers.size === 0) {
            subscribersRef.current.delete(eventType);
          }
        }
      };
    },
    []
  );

  useEffect(() => {
    mountedRef.current = true;
    connect();

    return () => {
      mountedRef.current = false;

      if (retryTimerRef.current) {
        clearTimeout(retryTimerRef.current);
        retryTimerRef.current = null;
      }

      if (eventSourceRef.current) {
        eventSourceRef.current.close();
        eventSourceRef.current = null;
      }
    };
  }, [connect]);

  return { connected, reconnecting, subscribe };
}
