'use client';

import { useEffect, useState, useRef } from 'react';
import { FixedSizeList as List } from 'react-window';
import { api, getAccessToken } from '@/lib/api';
import { useQueryClient } from '@tanstack/react-query';

type LogViewerProps = {
  runId: string;
  status: string;
};

// Maximum string length to render before truncating
const MAX_LOG_LENGTH = 5000;

export default function LogViewer({ runId, status }: LogViewerProps) {
  const [logs, setLogs] = useState<string[]>([]);
  const queryClient = useQueryClient();
  const listRef = useRef<List>(null);
  const eventSourceRef = useRef<EventSource | null>(null);

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

        const sseUrl = `http://localhost:8000/api/v1/evaluations/${runId}/stream?ticket=${data.ticket}`;
        const eventSource = new EventSource(sseUrl);
        eventSourceRef.current = eventSource;

        eventSource.onmessage = (event) => {
          let rawData = event.data;
          
          // Truncate massive strings to prevent UI freeze
          if (rawData.length > MAX_LOG_LENGTH) {
            rawData = rawData.slice(0, MAX_LOG_LENGTH) + '\n... [TRUNCATED]';
          }

          setLogs((prev) => {
            const newLogs = [...prev, rawData].slice(-1000); // Prevent O(N^2) memory leak
            // Auto-scroll
            setTimeout(() => {
              if (listRef.current) {
                listRef.current.scrollToItem(newLogs.length - 1, 'end');
              }
            }, 10);
            return newLogs;
          });

          // Terminal event check
          if (rawData.includes('EVALUATION_COMPLETED') || rawData.includes('EVALUATION_FAILED')) {
            eventSource.close();
            queryClient.invalidateQueries({ queryKey: ['evaluation', runId] });
          }
        };

        eventSource.onerror = () => {
          console.error('SSE Error or disconnection');
          eventSource.close();
          // On silent drop or error, fallback to REST to reconcile any missed terminal events
          queryClient.invalidateQueries({ queryKey: ['evaluation', runId] });
          
          // Exponential backoff or simple delay before reconnecting with a fresh ticket
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

  const Row = ({ index, style }: { index: number; style: React.CSSProperties }) => (
    <div style={{ ...style, borderBottom: '1px solid #333', padding: '4px 8px', wordBreak: 'break-all', overflowWrap: 'break-word', whiteSpace: 'pre-wrap' }}>
      {logs[index]}
    </div>
  );

  return (
    <div style={{ height: '600px', width: '100%', backgroundColor: '#0d1117', color: '#c9d1d9', borderRadius: '12px', border: '1px solid var(--border-color)', overflow: 'hidden', fontFamily: 'var(--font-mono)', fontSize: '0.875rem' }}>
      {logs.length === 0 ? (
        <div style={{ padding: '24px', color: '#8b949e', textAlign: 'center' }}>
          {status === 'pending' || status === 'running' 
            ? 'Connecting to stream...' 
            : 'No logs streamed. Download raw trace for details.'}
        </div>
      ) : (
        <List
          ref={listRef}
          height={600}
          itemCount={logs.length}
          itemSize={40} // Estimated height, can be dynamic with VariableSizeList
          width="100%"
        >
          {Row}
        </List>
      )}
    </div>
  );
}
