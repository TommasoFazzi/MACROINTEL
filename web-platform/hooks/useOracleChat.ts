'use client';

import { useState, useCallback, useRef } from 'react';
import type {
  OracleChatMessage,
  OracleActiveFilters,
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
  const [byokError, setByokError] = useState<string | null>(null);
  const [activeFilters, setActiveFilters] = useState<OracleActiveFilters>({
    mode: 'both',
    search_type: 'hybrid',
  });
  const abortControllerRef = useRef<AbortController | null>(null);

  // Gemini API key stored in localStorage — never sent to server logs
  const [geminiApiKey, setGeminiApiKeyState] = useState<string>(() => {
    if (typeof window !== 'undefined') {
      return localStorage.getItem('oracle_gemini_key') ?? '';
    }
    return '';
  });

  const setGeminiApiKey = useCallback((key: string) => {
    setGeminiApiKeyState(key);
    if (typeof window !== 'undefined') {
      if (key) {
        localStorage.setItem('oracle_gemini_key', key);
      } else {
        localStorage.removeItem('oracle_gemini_key');
      }
    }
  }, []);

  const sendMessage = useCallback(
    async (query: string) => {
      if (!query.trim() || isLoading) return;

      setError(null);
      setByokError(null);

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
            mode: activeFilters.mode ?? 'both',
            search_type: activeFilters.search_type ?? 'hybrid',
            start_date: activeFilters.start_date ?? null,
            end_date: activeFilters.end_date ?? null,
            categories: null,
            gpe_filter: activeFilters.gpe_filter ?? null,
            gemini_api_key: geminiApiKey || null,
          }),
          signal: abortControllerRef.current.signal,
        });

        clearTimeout(timeoutId);

        if (!response.ok) {
          const err = await response.json().catch(() => ({}));
          const detail = err.detail ?? `HTTP ${response.status}`;
          if (response.status === 402 || response.status === 422) {
            setByokError(detail);
            return;
          }
          throw new Error(detail);
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
          setError('Richiesta scaduta. Riprova.');
        } else {
          setError(err instanceof Error ? err.message : 'Errore sconosciuto');
        }
      } finally {
        setIsLoading(false);
        abortControllerRef.current = null;
      }
    },
    [isLoading, geminiApiKey, activeFilters]
  );

  const clearMessages = useCallback(() => {
    setMessages([]);
    setError(null);
    setByokError(null);
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
    byokError,
    sendMessage,
    clearMessages,
    lastAssistantMessage,
    geminiApiKey,
    setGeminiApiKey,
    activeFilters,
    setActiveFilters,
  };
}
