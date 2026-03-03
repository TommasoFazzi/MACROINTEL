'use client';

import { useState, useCallback, useRef } from 'react';
import type {
  OracleChatMessage,
  OracleChatFilters,
  OracleResponse,
} from '../types/oracle';

// Generate a stable session ID per browser session
let _sessionId: string | null = null;
function getSessionId(): string {
  if (!_sessionId) {
    _sessionId =
      typeof crypto !== 'undefined' && crypto.randomUUID
        ? crypto.randomUUID()
        : Math.random().toString(36).slice(2);
  }
  return _sessionId;
}

export function useOracleChat() {
  const [messages, setMessages] = useState<OracleChatMessage[]>([]);
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const abortControllerRef = useRef<AbortController | null>(null);

  const sendMessage = useCallback(
    async (query: string, filters?: OracleChatFilters) => {
      if (!query.trim() || isLoading) return;

      setError(null);

      // Optimistic user message
      const userMsg: OracleChatMessage = {
        id: crypto.randomUUID ? crypto.randomUUID() : Date.now().toString(),
        role: 'user',
        content: query.trim(),
        timestamp: new Date().toISOString(),
      };
      setMessages((prev) => [...prev, userMsg]);
      setIsLoading(true);

      // Cancel any in-flight request
      if (abortControllerRef.current) {
        abortControllerRef.current.abort();
      }
      abortControllerRef.current = new AbortController();

      try {
        const timeoutId = setTimeout(
          () => abortControllerRef.current?.abort(),
          120000
        );

        const response = await fetch('/api/proxy/oracle/chat', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            query: query.trim(),
            session_id: getSessionId(),
            mode: filters?.mode ?? 'both',
            search_type: filters?.search_type ?? 'hybrid',
            start_date: filters?.start_date ?? null,
            end_date: filters?.end_date ?? null,
            categories: filters?.categories ?? null,
            gpe_filter: filters?.gpe_filter ?? null,
          }),
          signal: abortControllerRef.current.signal,
        });

        clearTimeout(timeoutId);

        if (!response.ok) {
          const err = await response.json().catch(() => ({}));
          throw new Error(err.detail ?? `HTTP ${response.status}`);
        }

        const json = await response.json();
        const data: OracleResponse = json.data;

        const assistantMsg: OracleChatMessage = {
          id: crypto.randomUUID ? crypto.randomUUID() : Date.now().toString() + 'a',
          role: 'assistant',
          content: data.answer,
          timestamp: new Date().toISOString(),
          sources: data.sources,
          query_plan: data.query_plan,
          metadata: data.metadata,
        };
        setMessages((prev) => [...prev, assistantMsg]);
      } catch (err) {
        if (err instanceof Error && err.name === 'AbortError') {
          setError('Request timed out. Please try again.');
        } else {
          setError(err instanceof Error ? err.message : 'Unknown error');
        }
      } finally {
        setIsLoading(false);
        abortControllerRef.current = null;
      }
    },
    [isLoading]
  );

  const clearMessages = useCallback(() => {
    setMessages([]);
    setError(null);
    // Rotate session ID so new conversation starts fresh
    _sessionId = null;
  }, []);

  const lastAssistantMessage = messages
    .slice()
    .reverse()
    .find((m) => m.role === 'assistant');

  return {
    messages,
    isLoading,
    error,
    sendMessage,
    clearMessages,
    lastAssistantMessage,
  };
}
