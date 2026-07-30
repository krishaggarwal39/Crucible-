'use client';

import { useState } from 'react';
import { keepPreviousData, useQuery } from '@tanstack/react-query';
import { api } from '@/lib/api';
import Link from 'next/link';
import {
  AlertTriangle,
  ChevronLeft,
  ChevronRight,
  Loader2,
  Play,
  ShieldCheck,
} from 'lucide-react';
import { formatDistanceToNow } from 'date-fns';

import { isActiveStatus, queryKeys } from '@/lib/queryKeys';

type DriftProfile = {
  status: string;
  score?: number;
  band?: string;
  baseline_run_id?: string;
  reason?: string;
};

type EvaluationRun = {
  id: string;
  name: string;
  status: string;
  avg_score: number | null;
  pass_rate: number | null;
  created_at: string;
  drift_profile: DriftProfile | null;
  is_baseline: boolean;
};

const LIMIT = 10;

export default function EvaluationTable() {
  const [page, setPage] = useState(0);

  const { data: runs, isLoading, isError, isFetching } = useQuery<EvaluationRun[]>({
    queryKey: queryKeys.evaluations.list(page, LIMIT),
    queryFn: async () => {
      const res = await api.get(`/api/v1/evaluations/?skip=${page * LIMIT}&limit=${LIMIT}`);
      return res.data;
    },
    // Poll only while something is actually in flight. Polling unconditionally
    // every 5s burned 12 req/min against a 60 req/min tier even when idle.
    refetchInterval: (query) => {
      const rows = query.state.data as EvaluationRun[] | undefined;
      return rows?.some((r) => isActiveStatus(r.status)) ? 5000 : false;
    },
    placeholderData: keepPreviousData,
  });

  if (isLoading) {
    return (
      <div className="glass" style={{ borderRadius: '12px', padding: '48px', display: 'flex', justifyContent: 'center' }}>
        <Loader2 className="animate-spin" style={{ color: 'var(--primary-color)' }} />
      </div>
    );
  }

  if (isError || !runs) {
    return (
      <div className="glass" style={{ borderRadius: '12px', padding: '24px', color: 'var(--danger-color)' }}>
        Failed to load evaluations.
      </div>
    );
  }

  const getStatusBadge = (status: string) => {
    switch (status) {
      case 'completed': return <span style={{ color: 'var(--success-color)', backgroundColor: 'rgba(16, 185, 129, 0.1)', padding: '4px 8px', borderRadius: '4px', fontSize: '0.75rem', fontWeight: 600 }}>Completed</span>;
      case 'running': return <span style={{ color: 'var(--primary-color)', backgroundColor: 'var(--primary-light)', padding: '4px 8px', borderRadius: '4px', fontSize: '0.75rem', fontWeight: 600, display: 'flex', alignItems: 'center', gap: '4px' }}><Loader2 size={12} className="animate-spin"/> Running</span>;
      case 'failed': return <span style={{ color: 'var(--danger-color)', backgroundColor: 'rgba(239, 68, 68, 0.1)', padding: '4px 8px', borderRadius: '4px', fontSize: '0.75rem', fontWeight: 600 }}>Failed</span>;
      case 'cancelled': return <span style={{ color: 'var(--warning-color)', backgroundColor: 'rgba(245, 158, 11, 0.1)', padding: '4px 8px', borderRadius: '4px', fontSize: '0.75rem', fontWeight: 600 }}>Cancelled</span>;
      default: return <span style={{ color: 'var(--text-tertiary)', backgroundColor: 'rgba(156, 163, 175, 0.1)', padding: '4px 8px', borderRadius: '4px', fontSize: '0.75rem', fontWeight: 600, textTransform: 'capitalize' }}>{status}</span>;
    }
  };

  const getDriftBadge = (drift: DriftProfile | null) => {
    if (!drift) return <span style={{ color: 'var(--text-tertiary)' }}>—</span>;
    
    if (drift.status === 'baseline_missing' || drift.status === 'insufficient_overlap') {
      return <span style={{ color: 'var(--text-tertiary)', fontSize: '0.875rem' }}>No Baseline</span>;
    }
    
    if (drift.band === 'Stable') {
      return <span style={{ color: 'var(--success-color)', display: 'flex', alignItems: 'center', gap: '4px', fontSize: '0.875rem' }}><ShieldCheck size={14}/> Stable</span>;
    } else if (drift.band === 'Minor Drift') {
      return <span style={{ color: 'var(--warning-color)', display: 'flex', alignItems: 'center', gap: '4px', fontSize: '0.875rem' }}><AlertTriangle size={14}/> Minor</span>;
    } else {
      return <span style={{ color: 'var(--danger-color)', display: 'flex', alignItems: 'center', gap: '4px', fontSize: '0.875rem', fontWeight: 600 }}><AlertTriangle size={14}/> Severe</span>;
    }
  };

  return (
    <div className="glass" style={{ borderRadius: '12px', overflow: 'hidden' }}>
      <div style={{ padding: '20px 24px', borderBottom: '1px solid var(--border-color)', display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
        <h2 style={{ fontSize: '1.125rem', fontWeight: 600 }}>Recent Evaluations</h2>
        <Link href="/evaluations/new" style={{ backgroundColor: 'var(--primary-color)', color: 'white', border: 'none', padding: '8px 16px', borderRadius: '6px', fontSize: '0.875rem', fontWeight: 500, cursor: 'pointer', display: 'flex', alignItems: 'center', gap: '6px', textDecoration: 'none' }}>
          <Play size={14} /> New Run
        </Link>
      </div>
      
      <div style={{ overflowX: 'auto' }}>
        <table style={{ width: '100%', borderCollapse: 'collapse', textAlign: 'left' }}>
          <thead>
            <tr style={{ borderBottom: '1px solid var(--border-color)', color: 'var(--text-secondary)', fontSize: '0.875rem' }}>
              <th style={{ padding: '16px 24px', fontWeight: 500 }}>Name</th>
              <th style={{ padding: '16px 24px', fontWeight: 500 }}>Status</th>
              <th style={{ padding: '16px 24px', fontWeight: 500 }}>Pass Rate</th>
              <th style={{ padding: '16px 24px', fontWeight: 500 }}>Drift Score</th>
              <th style={{ padding: '16px 24px', fontWeight: 500 }}>Time</th>
              <th style={{ padding: '16px 24px', fontWeight: 500 }}></th>
            </tr>
          </thead>
          <tbody>
            {runs.length === 0 ? (
              <tr>
                <td colSpan={6} style={{ padding: '32px', textAlign: 'center', color: 'var(--text-tertiary)' }}>
                  No evaluations found.
                </td>
              </tr>
            ) : runs.map((run) => (
              <tr key={run.id} style={{ borderBottom: '1px solid var(--border-color)', transition: 'background-color 0.2s' }} className="hover-row">
                <td style={{ padding: '16px 24px' }}>
                  <div style={{ fontWeight: 500, color: 'var(--text-primary)', display: 'flex', alignItems: 'center', gap: '8px' }}>
                    {run.name}
                    {run.is_baseline && (
                      <span style={{ fontSize: '0.65rem', backgroundColor: 'var(--warning-color)', color: '#fff', padding: '2px 6px', borderRadius: '4px', textTransform: 'uppercase', fontWeight: 700 }}>Baseline</span>
                    )}
                  </div>
                  <div style={{ fontSize: '0.75rem', color: 'var(--text-tertiary)', marginTop: '4px' }}>ID: {run.id.substring(0, 8)}...</div>
                </td>
                <td style={{ padding: '16px 24px' }}>
                  {getStatusBadge(run.status)}
                </td>
                <td style={{ padding: '16px 24px', fontSize: '0.875rem' }}>
                  {run.pass_rate !== null ? `${(run.pass_rate * 100).toFixed(1)}%` : '—'}
                </td>
                <td style={{ padding: '16px 24px' }}>
                  {getDriftBadge(run.drift_profile)}
                </td>
                <td style={{ padding: '16px 24px', fontSize: '0.875rem', color: 'var(--text-secondary)' }}>
                  {formatDistanceToNow(new Date(run.created_at), { addSuffix: true })}
                </td>
                <td style={{ padding: '16px 24px', textAlign: 'right' }}>
                  <Link
                    href={`/evaluations/${run.id}`}
                    aria-label={`View evaluation ${run.name}`}
                    style={{ color: 'var(--text-secondary)', display: 'inline-flex', padding: '6px', borderRadius: '6px' }}
                    className="hover-btn"
                  >
                    <ChevronRight size={18} />
                  </Link>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {/* Pagination controls. `page` state existed but nothing ever rendered a
          way to change it, so the table was permanently pinned to page 0. */}
      <div
        style={{
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'space-between',
          gap: '12px',
          padding: '12px 24px',
          borderTop: '1px solid var(--border-color)',
          fontSize: '0.875rem',
          color: 'var(--text-secondary)',
        }}
      >
        <span>
          Page {page + 1}
          {isFetching ? ' · updating…' : ''}
        </span>
        <div style={{ display: 'flex', gap: '8px' }}>
          <button
            type="button"
            onClick={() => setPage((p) => Math.max(0, p - 1))}
            disabled={page === 0}
            aria-label="Previous page"
            style={{
              display: 'inline-flex',
              alignItems: 'center',
              gap: '4px',
              padding: '6px 12px',
              borderRadius: 'var(--radius-md)',
              border: '1px solid var(--border-color)',
              background: 'var(--bg-surface)',
              color: 'var(--text-primary)',
              cursor: page === 0 ? 'not-allowed' : 'pointer',
              opacity: page === 0 ? 0.5 : 1,
            }}
          >
            <ChevronLeft size={14} /> Previous
          </button>
          <button
            type="button"
            onClick={() => setPage((p) => p + 1)}
            disabled={runs.length < LIMIT}
            aria-label="Next page"
            style={{
              display: 'inline-flex',
              alignItems: 'center',
              gap: '4px',
              padding: '6px 12px',
              borderRadius: 'var(--radius-md)',
              border: '1px solid var(--border-color)',
              background: 'var(--bg-surface)',
              color: 'var(--text-primary)',
              cursor: runs.length < LIMIT ? 'not-allowed' : 'pointer',
              opacity: runs.length < LIMIT ? 0.5 : 1,
            }}
          >
            Next <ChevronRight size={14} />
          </button>
        </div>
      </div>

      <style jsx>{`
        .hover-row:hover {
          background-color: rgba(255, 255, 255, 0.02);
        }
        .hover-btn:hover {
          background-color: var(--border-color);
          color: var(--text-primary);
        }
      `}</style>
    </div>
  );
}
