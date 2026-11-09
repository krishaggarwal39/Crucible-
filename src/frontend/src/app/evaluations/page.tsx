'use client';

import { useQuery } from '@tanstack/react-query';
import { api } from '@/lib/api';
import Link from 'next/link';
import { Play, Loader2 } from 'lucide-react';
import { formatDistanceToNow } from 'date-fns';
import ProtectedRoute from '@/components/auth/ProtectedRoute';
import { queryKeys } from '@/lib/queryKeys';

type EvaluationRun = {
  id: string;
  name: string;
  status: string;
  avg_score: number | null;
  created_at: string;
};

export default function EvaluationsPage() {
  const { data: runs, isLoading } = useQuery<EvaluationRun[]>({
    queryKey: queryKeys.evaluations.list(0, 100),
    queryFn: async () => {
      const res = await api.get('/api/v1/evaluations/?skip=0&limit=100');
      return res.data;
    },
  });

  const getStatusColor = (status: string) => {
    switch (status) {
      case 'completed': return 'var(--success-color)';
      case 'running': return 'var(--primary-color)';
      case 'failed': return 'var(--danger-color)';
      case 'cancelled': return 'var(--warning-color)';
      default: return 'var(--text-tertiary)';
    }
  };

  return (
    <ProtectedRoute>
      <div>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '24px' }}>
          <h1 style={{ fontSize: '1.5rem', fontWeight: 600 }}>Evaluation Runs</h1>
          <Link
            href="/evaluations/new"
            style={{
              display: 'flex', alignItems: 'center', gap: '8px',
              backgroundColor: 'var(--success-color)', color: 'white',
              padding: '8px 16px', borderRadius: 'var(--radius-md)',
              fontWeight: 500,
            }}
          >
            <Play size={18} /> New Run
          </Link>
        </div>

        {isLoading ? (
          <div style={{ padding: '40px', textAlign: 'center', color: 'var(--text-tertiary)' }}>
            <Loader2 className="animate-spin" style={{ margin: '0 auto' }} />
          </div>
        ) : !runs || runs.length === 0 ? (
          <div className="glass" style={{ padding: '60px', textAlign: 'center', borderRadius: '12px' }}>
            <h3 style={{ fontSize: '1.125rem', fontWeight: 500, marginBottom: '8px' }}>No Evaluations Yet</h3>
            <p style={{ color: 'var(--text-secondary)' }}>Run your first evaluation to see results here.</p>
          </div>
        ) : (
          <div className="glass" style={{ borderRadius: '12px', overflow: 'hidden' }}>
            <table style={{ width: '100%', borderCollapse: 'collapse', textAlign: 'left' }}>
              <thead>
                <tr style={{ borderBottom: '1px solid var(--border-color)', color: 'var(--text-secondary)', fontSize: '0.875rem' }}>
                  <th style={{ padding: '16px 24px', fontWeight: 500 }}>Name</th>
                  <th style={{ padding: '16px 24px', fontWeight: 500 }}>Status</th>
                  <th style={{ padding: '16px 24px', fontWeight: 500 }}>Score</th>
                  <th style={{ padding: '16px 24px', fontWeight: 500 }}>Time</th>
                </tr>
              </thead>
              <tbody>
                {runs.map((run) => (
                  <tr key={run.id} style={{ borderBottom: '1px solid var(--border-color)' }}>
                    <td style={{ padding: '16px 24px' }}>
                      <Link href={`/evaluations/${run.id}`} style={{ fontWeight: 500, color: 'var(--text-primary)' }}>
                        {run.name}
                      </Link>
                    </td>
                    <td style={{ padding: '16px 24px' }}>
                      <span style={{ color: getStatusColor(run.status), fontWeight: 600, fontSize: '0.875rem', textTransform: 'capitalize' }}>
                        {run.status}
                      </span>
                    </td>
                    <td style={{ padding: '16px 24px', fontSize: '0.875rem' }}>
                      {run.avg_score !== null ? run.avg_score.toFixed(1) : '—'}
                    </td>
                    <td style={{ padding: '16px 24px', fontSize: '0.875rem', color: 'var(--text-secondary)' }}>
                      {formatDistanceToNow(new Date(run.created_at), { addSuffix: true })}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </ProtectedRoute>
  );
}
