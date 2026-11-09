'use client';

import { useEffect, useState, useRef } from 'react';

import { api, apiUrl, getAccessToken } from '@/lib/api';
import { useQueryClient } from '@tanstack/react-query';

type LogViewerProps = {
  runId: string;
  status: string;
};

// Maximum string length to render before truncating
const MAX_LOG_LENGTH = 5000;

// Statuses after which no further events will arrive.
// "cancelled" was missing, so a cancelled run's stream was never closed
// client-side and the component reconnected indefinitely.
const TERMINAL_STATUSES = ['completed', 'failed', 'cancelled'];

export default function LogViewer({ runId, status }: LogViewerProps) {
  const [logs, setLogs] = useState<string[]>([]);
  const queryClient = useQueryClient();
  const listRef = useRef<HTMLDivElement>(null);
  const eventSourceRef = useRef<EventSource | null>(null);
  const lastSeqRef = useRef<number>(0);

  useEffect(() => {
    // Only connect if running
    if (status !== 'pending' && status !== 'running') {
      return;
    }

    const token = getAccessToken();
    if (!token) return;

    let isSubscribed = true;
    let retryTimeoutId: NodeJS.Timeout;

    const connectStream = async () => {
      try {
        // Fetch short-lived ticket to securely open EventSource without leaking JWT
        const { data } = await api.post<{ticket: string}>(`/api/v1/evaluations/${runId}/stream-ticket`);
        
        if (!isSubscribed) return;

        // Pass last_seq on reconnection so the server replays missed events
        const lastSeq = lastSeqRef.current;
        const sseUrl = apiUrl(
          `/api/v1/evaluations/${runId}/stream?ticket=${encodeURIComponent(data.ticket)}&last_seq=${lastSeq}`,
        );
        const eventSource = new EventSource(sseUrl, { withCredentials: true });
        eventSourceRef.current = eventSource;

        eventSource.onmessage = (event) => {
          let rawData = event.data;
          let eventStatus: string | null = null;

          // Track seq_num for replay on reconnection
          try {
            const parsed = JSON.parse(rawData);
            if (parsed.seq_num && parsed.seq_num > lastSeqRef.current) {
              lastSeqRef.current = parsed.seq_num;
            }
            if (typeof parsed.status === 'string') {
              eventStatus = parsed.status;
            }
          } catch {
            // Not JSON — still display it
          }
          
          // Truncate massive strings to prevent UI freeze
          if (rawData.length > MAX_LOG_LENGTH) {
            rawData = rawData.slice(0, MAX_LOG_LENGTH) + '\n... [TRUNCATED]';
          }

          setLogs((prev) => {
            const newLogs = [...prev, rawData].slice(-1000); // Prevent O(N^2) memory leak
            // Auto-scroll
            setTimeout(() => {
              if (listRef.current) {
                listRef.current.scrollTop = listRef.current.scrollHeight;
              }
            }, 10);
            return newLogs;
          });

          // Terminal event check, driven by the parsed status rather than
          // substring matching on the raw payload.
          if (eventStatus && TERMINAL_STATUSES.includes(eventStatus)) {
            isSubscribed = false;
            if (retryTimeoutId) clearTimeout(retryTimeoutId);
            eventSource.close();
            queryClient.invalidateQueries({ queryKey: ['evaluation', runId] });
            queryClient.invalidateQueries({ queryKey: ['evaluation-results', runId] });
          }
        };

        eventSource.onerror = () => {
          console.error('SSE Error or disconnection');
          eventSource.close();
          // On silent drop or error, fallback to REST to reconcile any missed terminal events
          queryClient.invalidateQueries({ queryKey: ['evaluation', runId] });
          
          // Reconnect with exponential backoff — will replay from last_seq
          if (retryTimeoutId) clearTimeout(retryTimeoutId);
          retryTimeoutId = setTimeout(() => {
            if (isSubscribed) connectStream();
          }, 2000);
        };
      } catch (err) {
        console.error('Failed to get stream ticket:', err);
        // Retry ticket fetching
        if (retryTimeoutId) clearTimeout(retryTimeoutId);
        retryTimeoutId = setTimeout(() => {
          if (isSubscribed) connectStream();
        }, 5000);
      }
    };

    connectStream();

    return () => {
      isSubscribed = false;
      if (retryTimeoutId) clearTimeout(retryTimeoutId);
      if (eventSourceRef.current) {
        eventSourceRef.current.close();
      }
    };
  }, [runId, status, queryClient]);

  return (
    <div style={{ height: '600px', width: '100%', backgroundColor: '#0d1117', color: '#c9d1d9', borderRadius: '12px', border: '1px solid var(--border-color)', overflow: 'hidden', fontFamily: 'var(--font-mono)', fontSize: '0.875rem' }}>
      {logs.length === 0 ? (
        <div style={{ padding: '24px', color: '#8b949e', textAlign: 'center' }}>
          {status === 'pending' || status === 'running'
            ? 'Connecting to stream...'
            : 'No live logs for this run. Per-scenario results and raw trace downloads are below.'}
        </div>
      ) : (
        <div
          ref={listRef}
          style={{ width: '100%', height: '100%', overflowY: 'auto' }}
        >
          {logs.map((log, index) => (
            <div key={index} style={{ borderBottom: '1px solid #333', padding: '4px 8px', wordBreak: 'break-all', overflowWrap: 'break-word', whiteSpace: 'pre-wrap' }}>
              {log}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
