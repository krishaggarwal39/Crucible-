'use client';

import { useQuery } from '@tanstack/react-query';
import { api } from '@/lib/api';
import { useParams } from 'next/navigation';
import ProtectedRoute from '@/components/auth/ProtectedRoute';
import BaselineToggle from '@/components/dashboard/BaselineToggle';
import ScenarioResults from '@/components/dashboard/ScenarioResults';
import { Loader2, ArrowLeft, Sparkles, BrainCircuit } from 'lucide-react';
import Link from 'next/link';
import LogViewer from '@/components/LogViewer';
import { isActiveStatus, queryKeys } from '@/lib/queryKeys';

type EvolutionSuggestion = {
  failure_pattern?: string;
  suggested_prompt?: string;
  suggested_tools?: string | null;
};

export default function EvaluationDetail() {
  const params = useParams();
  const runId = params?.id as string;

  const { data: run, isLoading, isError } = useQuery({
    queryKey: queryKeys.evaluations.detail(runId),
    queryFn: async () => {
      const res = await api.get(`/api/v1/evaluations/${runId}`);
      return res.data;
    },
    enabled: !!runId,
    refetchInterval: (query) => {
      const data = query.state?.data as { status?: string } | undefined;
      return isActiveStatus(data?.status) ? 3000 : false;
    },
  });

  if (isLoading) {
    return (
      <ProtectedRoute>
        <div style={{ display: 'flex', justifyContent: 'center', alignItems: 'center', height: '60vh' }}>
          <Loader2 className="animate-spin" style={{ color: 'var(--primary-color)' }} size={32} />
        </div>
      </ProtectedRoute>
    );
  }

  if (isError || !run) {
    return (
      <ProtectedRoute>
        <div style={{ color: 'var(--danger-color)' }}>Failed to load evaluation run.</div>
      </ProtectedRoute>
    );
  }

  return (
    <ProtectedRoute>
      <div style={{ maxWidth: '1200px', margin: '0 auto' }}>
        <div style={{ marginBottom: '24px' }}>
          <Link href="/" style={{ color: 'var(--text-tertiary)', display: 'inline-flex', alignItems: 'center', gap: '4px', fontSize: '0.875rem', textDecoration: 'none', marginBottom: '16px' }}>
            <ArrowLeft size={16} /> Back to Dashboard
          </Link>
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start' }}>
            <div>
              <h1 style={{ fontSize: '1.75rem', fontWeight: 700, marginBottom: '8px' }}>{run.name}</h1>
              <div style={{ color: 'var(--text-secondary)', fontSize: '0.875rem', display: 'flex', gap: '16px' }}>
                <span>ID: {run.id}</span>
                <span>Status: <strong style={{ textTransform: 'capitalize' }}>{run.status}</strong></span>
                <span>Score: {run.avg_score ? run.avg_score.toFixed(1) : '—'}</span>
              </div>
            </div>
            {run.status === 'completed' && run.avg_score >= 70 && (
              <BaselineToggle runId={run.id} isBaseline={run.is_baseline} />
            )}
          </div>
        </div>

        {/* AI Analyzer (Drift) */}
        {run.drift_profile && (
          <div className="glass" style={{ borderRadius: '12px', padding: '24px', marginBottom: '24px', borderLeft: '4px solid var(--primary-color)' }}>
            <h2 style={{ fontSize: '1.125rem', fontWeight: 600, display: 'flex', alignItems: 'center', gap: '8px', marginBottom: '16px' }}>
              <BrainCircuit size={20} color="var(--primary-color)" /> Behavioral Drift Analysis
            </h2>
            
            <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '24px' }}>
              <div>
                <div style={{ fontSize: '0.875rem', color: 'var(--text-tertiary)', marginBottom: '4px' }}>Status</div>
                <div style={{ fontSize: '1rem', fontWeight: 500, textTransform: 'capitalize' }}>{run.drift_profile.status.replace('_', ' ')}</div>
              </div>
              
              {run.drift_profile.status === 'success' && (
                <>
                  <div>
                    <div style={{ fontSize: '0.875rem', color: 'var(--text-tertiary)', marginBottom: '4px' }}>Drift Band</div>
                    <div style={{ fontSize: '1rem', fontWeight: 600, color: run.drift_profile.band === 'Stable' ? 'var(--success-color)' : run.drift_profile.band === 'Significant Drift' ? 'var(--danger-color)' : 'var(--warning-color)' }}>
                      {run.drift_profile.band} ({(run.drift_profile.score * 100).toFixed(1)}%)
                    </div>
                  </div>
                  <div>
                    <div style={{ fontSize: '0.875rem', color: 'var(--text-tertiary)', marginBottom: '4px' }}>Overlapping Scenarios</div>
                    <div style={{ fontSize: '1rem', fontWeight: 500 }}>{run.drift_profile.overlap_count}</div>
                  </div>
                </>
              )}
            </div>
          </div>
        )}

        {/* Evolution Agent Suggestions */}
        {run.evolution_suggestions && run.evolution_suggestions.length > 0 && (
          <div className="glass" style={{ borderRadius: '12px', padding: '24px', marginBottom: '24px', borderLeft: '4px solid var(--warning-color)' }}>
            <h2 style={{ fontSize: '1.125rem', fontWeight: 600, display: 'flex', alignItems: 'center', gap: '8px', marginBottom: '16px' }}>
              <Sparkles size={20} color="var(--warning-color)" /> Evolution Suggestions
            </h2>
            
            {run.evolution_suggestions.map((suggestion: EvolutionSuggestion, idx: number) => (
              <div key={idx} style={{ marginBottom: idx < run.evolution_suggestions.length - 1 ? '24px' : '0' }}>
                <div style={{ marginBottom: '16px' }}>
                  <div style={{ fontSize: '0.875rem', fontWeight: 600, color: 'var(--text-secondary)', marginBottom: '8px' }}>Failure Synthesis</div>
                  <div style={{ backgroundColor: 'rgba(0,0,0,0.2)', padding: '12px', borderRadius: '8px', fontSize: '0.9rem', lineHeight: 1.5 }}>
                    {suggestion.failure_pattern}
                  </div>
                </div>
                <div>
                  <div style={{ fontSize: '0.875rem', fontWeight: 600, color: 'var(--text-secondary)', marginBottom: '8px' }}>Suggested Prompt Rewrite</div>
                  <pre style={{ backgroundColor: 'rgba(0,0,0,0.3)', padding: '16px', borderRadius: '8px', fontSize: '0.875rem', overflowX: 'auto', whiteSpace: 'pre-wrap', color: 'var(--primary-color)' }}>
                    {suggestion.suggested_prompt}
                  </pre>
                </div>
              </div>
            ))}
          </div>
        )}

        {/* Per-scenario results: scores, judge reasoning and raw trace downloads.
            These were persisted to Postgres but no endpoint exposed them, so the
            product's actual output was unreachable from the UI. */}
        <div style={{ marginTop: '32px' }}>
          <ScenarioResults runId={run.id} runStatus={run.status} />
        </div>

        {/* Log Viewer for SSE Streaming if running, or just to show it exists */}
        <div style={{ marginTop: '32px' }}>
          <h2 style={{ fontSize: '1.125rem', fontWeight: 600, marginBottom: '16px' }}>Live Trace Logs</h2>
          <LogViewer runId={run.id} status={run.status} />
        </div>

      </div>
    </ProtectedRoute>
  );
}
