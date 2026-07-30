'use client';

import { useQuery } from '@tanstack/react-query';
import { api } from '@/lib/api';
import { ScatterChart, Scatter, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer, Cell } from 'recharts';
import { useAuth } from '@/contexts/AuthContext';
import { useEffect } from 'react';
import { useRouter } from 'next/navigation';
import ProtectedRoute from '@/components/auth/ProtectedRoute';
import { queryKeys } from '@/lib/queryKeys';

type DriftPoint = {
  run_id: string;
  run_name: string;
  drift_score: number;
  band: string;
  overlap_count: number;
  created_at: string | null;
  x: number;
  y: number;
};

type OperationsSummary = {
  // avg_drift_score is now the real mean drift; avg_quality_score is the judge
  // score that used to be mislabelled as drift.
  avg_drift_score: number | null;
  avg_quality_score: number | null;
  anomalous_runs: number;
  total_drift_runs: number;
  drift_alert_count: number;
  baseline_count: number;
};

// Defined at module scope: creating a component inside render gives it a new
// identity every pass, which forces React to remount it and trips the
// react-hooks/static-components rule.
function DriftTooltip({
  active,
  payload,
}: {
  active?: boolean;
  payload?: { payload: DriftPoint }[];
}) {
  if (!active || !payload || payload.length === 0) return null;
  const data = payload[0].payload;
  return (
    <div style={{ backgroundColor: 'var(--bg-card)', padding: '12px', border: '1px solid var(--border-color)', borderRadius: '8px' }}>
      <p style={{ margin: 0, fontWeight: 600 }}>{data.run_name}</p>
      <p style={{ margin: '4px 0 0 0', color: 'var(--text-secondary)' }}>
        Drift: {(data.drift_score * 100).toFixed(1)}% ({data.band})
      </p>
      <p style={{ margin: '4px 0 0 0', color: 'var(--text-tertiary)', fontSize: '0.75rem' }}>
        {data.overlap_count} overlapping scenarios
      </p>
    </div>
  );
}

export default function OperationsPage() {
  const { user } = useAuth();
  const router = useRouter();

  useEffect(() => {
    if (user && user.role !== 'admin') {
      router.push('/');
    }
  }, [user, router]);

  const { data: driftData, isLoading: driftLoading } = useQuery<DriftPoint[]>({
    queryKey: queryKeys.operations.drift(50),
    queryFn: async () => {
      const { data } = await api.get('/api/v1/operations/drift');
      return data;
    },
  });

  const { data: summary, isLoading: summaryLoading } = useQuery<OperationsSummary>({
    queryKey: queryKeys.operations.summary(),
    queryFn: async () => {
      const { data } = await api.get('/api/v1/operations/summary');
      return data;
    },
  });

  const getDotColor = (score: number) => {
    if (score > 0.25) return 'var(--danger-color)';
    if (score > 0.1) return 'var(--warning-color)';
    return 'var(--success-color)';
  };

  const isLoading = driftLoading || summaryLoading;

  return (
    <ProtectedRoute>
      <div>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '24px' }}>
          <div>
            <h1 style={{ fontSize: '1.5rem', fontWeight: 600 }}>AI Operations</h1>
            <p style={{ color: 'var(--text-tertiary)', fontSize: '0.875rem' }}>Monitor behavioral drift and system anomalies.</p>
          </div>
        </div>

        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3, 1fr)', gap: '24px', marginBottom: '24px' }}>
          <div className="glass" style={{ padding: '24px', borderRadius: '12px' }}>
            <div style={{ color: 'var(--text-tertiary)', fontSize: '0.875rem', marginBottom: '8px' }}>Average Drift</div>
            <div style={{ fontSize: '2rem', fontWeight: 700 }}>
              {summary?.avg_drift_score != null ? `${(summary.avg_drift_score * 100).toFixed(1)}%` : '—'}
            </div>
            <div style={{ color: 'var(--text-tertiary)', fontSize: '0.75rem', marginTop: '4px' }}>
              Mean quality score: {summary?.avg_quality_score != null ? summary.avg_quality_score.toFixed(1) : '—'}
            </div>
          </div>
          <div className="glass" style={{ padding: '24px', borderRadius: '12px' }}>
            <div style={{ color: 'var(--text-tertiary)', fontSize: '0.875rem', marginBottom: '8px' }}>Anomalous Runs</div>
            <div style={{ fontSize: '2rem', fontWeight: 700, color: summary?.anomalous_runs ? 'var(--danger-color)' : 'var(--text-primary)' }}>
              {summary?.anomalous_runs ?? 0}
            </div>
          </div>
          <div className="glass" style={{ padding: '24px', borderRadius: '12px' }}>
            <div style={{ color: 'var(--text-tertiary)', fontSize: '0.875rem', marginBottom: '8px' }}>Pinned Baselines</div>
            <div style={{ fontSize: '2rem', fontWeight: 700, color: 'var(--success-color)' }}>
              {summary?.baseline_count ?? 0}
            </div>
          </div>
        </div>

        <div className="glass" style={{ padding: '24px', borderRadius: '12px', height: '500px' }}>
          <h2 style={{ fontSize: '1.125rem', fontWeight: 600, marginBottom: '4px' }}>Behavioral Drift by Run</h2>
          <p style={{ color: 'var(--text-tertiary)', fontSize: '0.8rem', marginBottom: '20px' }}>
            Each point is one completed run, most recent first. The x axis is run order, not elapsed time.
          </p>
          
          {isLoading ? (
            <div style={{ display: 'flex', height: '100%', alignItems: 'center', justifyContent: 'center', color: 'var(--text-tertiary)' }}>
              Loading drift data...
            </div>
          ) : !driftData || driftData.length === 0 ? (
            <div style={{ display: 'flex', height: '100%', alignItems: 'center', justifyContent: 'center', color: 'var(--text-tertiary)', flexDirection: 'column', gap: '8px' }}>
              <p>No drift data available yet.</p>
              <p style={{ fontSize: '0.875rem' }}>Run evaluations with a baseline set to see drift analysis here.</p>
            </div>
          ) : (
            <div style={{ width: '100%', height: 'calc(100% - 48px)' }}>
              <ResponsiveContainer width="100%" height="100%">
                <ScatterChart margin={{ top: 20, right: 20, bottom: 20, left: 20 }}>
                  <CartesianGrid strokeDasharray="3 3" stroke="#333" />
                  <XAxis type="number" dataKey="x" name="Run Index" stroke="#888" tick={{fill: '#888'}} label={{ value: 'Run order (newest → oldest)', position: 'bottom', fill: '#888' }} />
                  <YAxis type="number" dataKey="y" name="Drift Score" stroke="#888" tick={{fill: '#888'}} domain={[0, 1]} label={{ value: 'Drift Score', angle: -90, position: 'insideLeft', fill: '#888' }} />
                  <Tooltip content={<DriftTooltip />} cursor={{ strokeDasharray: '3 3' }} />
                  <Scatter name="Runs" data={driftData}>
                    {driftData?.map((entry, index) => (
                      <Cell key={`cell-${index}`} fill={getDotColor(entry.drift_score)} />
                    ))}
                  </Scatter>
                </ScatterChart>
              </ResponsiveContainer>
            </div>
          )}
        </div>
      </div>
    </ProtectedRoute>
  );
}
