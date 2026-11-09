'use client';

import { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import {
  AlertTriangle,
  CheckCircle2,
  ChevronDown,
  ChevronRight,
  Download,
  Loader2,
  XCircle,
} from 'lucide-react';

import { api } from '@/lib/api';
import { isActiveStatus, queryKeys } from '@/lib/queryKeys';

type ScenarioDto = {
  id: string;
  title: string;
  description: string;
  category: string;
  severity: string;
  expected_behavior: string;
  tags: string[];
};

type TraceDto = {
  id: string;
  storage_key: string | null;
  status: string;
  turn_count: number | null;
  duration_ms: number | null;
  token_count: number | null;
  tool_call_count: number | null;
  error_message: string | null;
};

type JudgmentDto = {
  id: string;
  overall_score: number;
  safety_score: number | null;
  correctness_score: number | null;
  instruction_following_score: number | null;
  passed: boolean;
  reasoning: string | null;
  judge_model: string;
};

type ScenarioResult = {
  scenario: ScenarioDto;
  trace: TraceDto | null;
  judgment: JudgmentDto | null;
};

const severityColor = (severity: string): string => {
  switch (severity) {
    case 'critical':
      return 'var(--danger-color)';
    case 'high':
      return 'var(--warning-color)';
    case 'medium':
      return 'var(--primary-color)';
    default:
      return 'var(--text-tertiary)';
  }
};

function ScoreBar({ label, value }: { label: string; value: number | null }) {
  if (value === null || value === undefined) {
    return (
      <div style={{ fontSize: '0.75rem', color: 'var(--text-tertiary)' }}>
        {label}: not scored
      </div>
    );
  }
  const colour =
    value >= 70 ? 'var(--success-color)' : value >= 40 ? 'var(--warning-color)' : 'var(--danger-color)';
  return (
    <div>
      <div
        style={{
          display: 'flex',
          justifyContent: 'space-between',
          fontSize: '0.75rem',
          color: 'var(--text-secondary)',
          marginBottom: '4px',
        }}
      >
        <span>{label}</span>
        <span style={{ fontWeight: 600, color: colour }}>{value.toFixed(1)}</span>
      </div>
      <div
        style={{
          height: '4px',
          background: 'var(--border-color)',
          borderRadius: 'var(--radius-full)',
          overflow: 'hidden',
        }}
      >
        <div
          style={{
            width: `${Math.max(0, Math.min(100, value))}%`,
            height: '100%',
            background: colour,
          }}
        />
      </div>
    </div>
  );
}

export default function ScenarioResults({
  runId,
  runStatus,
}: {
  runId: string;
  runStatus: string;
}) {
  const [expanded, setExpanded] = useState<Record<string, boolean>>({});
  const [downloading, setDownloading] = useState<string | null>(null);
  const [downloadError, setDownloadError] = useState<string | null>(null);

  const { data, isLoading, isError } = useQuery<ScenarioResult[]>({
    queryKey: queryKeys.evaluations.results(runId),
    queryFn: async () => {
      const res = await api.get(`/api/v1/evaluations/${runId}/results`);
      return res.data;
    },
    enabled: !!runId,
    // Keep refreshing only while the run is still producing results.
    refetchInterval: isActiveStatus(runStatus) ? 5000 : false,
  });

  const handleDownload = async (traceId: string) => {
    setDownloading(traceId);
    setDownloadError(null);
    try {
      const res = await api.get<{ download_url: string }>(
        `/api/v1/evaluations/${runId}/traces/${traceId}/download`,
      );
      window.open(res.data.download_url, '_blank', 'noopener,noreferrer');
    } catch {
      setDownloadError('Could not generate a download link for that trace.');
    } finally {
      setDownloading(null);
    }
  };

  if (isLoading) {
    return (
      <div
        className="glass"
        style={{ borderRadius: '12px', padding: '48px', display: 'flex', justifyContent: 'center' }}
      >
        <Loader2 className="animate-spin" style={{ color: 'var(--primary-color)' }} />
        <span className="sr-only">Loading results</span>
      </div>
    );
  }

  if (isError) {
    return (
      <div className="glass" style={{ borderRadius: '12px', padding: '24px', color: 'var(--danger-color)' }}>
        Failed to load per-scenario results.
      </div>
    );
  }

  if (!data || data.length === 0) {
    return (
      <div
        className="glass"
        style={{ borderRadius: '12px', padding: '32px', textAlign: 'center', color: 'var(--text-tertiary)' }}
      >
        {isActiveStatus(runStatus)
          ? 'Scenarios will appear here as the run generates them.'
          : 'No scenarios were recorded for this run.'}
      </div>
    );
  }

  const passed = data.filter((r) => r.judgment?.passed).length;
  const judged = data.filter((r) => r.judgment).length;

  return (
    <div className="glass" style={{ borderRadius: '12px', overflow: 'hidden' }}>
      <div
        style={{
          padding: '20px 24px',
          borderBottom: '1px solid var(--border-color)',
          display: 'flex',
          justifyContent: 'space-between',
          alignItems: 'center',
          gap: '16px',
          flexWrap: 'wrap',
        }}
      >
        <h2 style={{ fontSize: '1.125rem', fontWeight: 600 }}>Scenario Results</h2>
        <div style={{ fontSize: '0.875rem', color: 'var(--text-secondary)' }}>
          {judged > 0 ? `${passed}/${judged} passed` : `${data.length} scenarios, not yet judged`}
        </div>
      </div>

      {downloadError && (
        <div style={{ padding: '12px 24px', color: 'var(--danger-color)', fontSize: '0.875rem' }} role="alert">
          {downloadError}
        </div>
      )}

      <ul style={{ listStyle: 'none' }}>
        {data.map(({ scenario, trace, judgment }) => {
          const isOpen = !!expanded[scenario.id];
          return (
            <li key={scenario.id} style={{ borderBottom: '1px solid var(--border-color)' }}>
              <button
                type="button"
                onClick={() => setExpanded((prev) => ({ ...prev, [scenario.id]: !prev[scenario.id] }))}
                aria-expanded={isOpen}
                style={{
                  width: '100%',
                  display: 'flex',
                  alignItems: 'center',
                  gap: '12px',
                  padding: '16px 24px',
                  background: 'transparent',
                  border: 'none',
                  cursor: 'pointer',
                  textAlign: 'left',
                  color: 'var(--text-primary)',
                }}
              >
                {isOpen ? <ChevronDown size={16} /> : <ChevronRight size={16} />}

                {judgment ? (
                  judgment.passed ? (
                    <CheckCircle2 size={18} style={{ color: 'var(--success-color)', flexShrink: 0 }} />
                  ) : (
                    <XCircle size={18} style={{ color: 'var(--danger-color)', flexShrink: 0 }} />
                  )
                ) : (
                  <AlertTriangle size={18} style={{ color: 'var(--text-tertiary)', flexShrink: 0 }} />
                )}

                <span style={{ flex: 1, fontWeight: 500 }}>{scenario.title}</span>

                <span
                  style={{
                    fontSize: '0.7rem',
                    textTransform: 'uppercase',
                    fontWeight: 700,
                    color: severityColor(scenario.severity),
                  }}
                >
                  {scenario.severity}
                </span>
                <span style={{ fontSize: '0.75rem', color: 'var(--text-tertiary)' }}>
                  {scenario.category}
                </span>
                <span style={{ fontWeight: 700, minWidth: '48px', textAlign: 'right' }}>
                  {judgment ? judgment.overall_score.toFixed(1) : '—'}
                </span>
              </button>

              {isOpen && (
                <div style={{ padding: '0 24px 24px 64px', display: 'grid', gap: '20px' }}>
                  <div>
                    <h3 style={{ fontSize: '0.8rem', fontWeight: 600, color: 'var(--text-secondary)', marginBottom: '6px' }}>
                      Scenario
                    </h3>
                    <p style={{ fontSize: '0.875rem', lineHeight: 1.6 }}>{scenario.description}</p>
                  </div>

                  <div>
                    <h3 style={{ fontSize: '0.8rem', fontWeight: 600, color: 'var(--text-secondary)', marginBottom: '6px' }}>
                      Expected behaviour
                    </h3>
                    <p style={{ fontSize: '0.875rem', lineHeight: 1.6 }}>
                      {scenario.expected_behavior || '—'}
                    </p>
                  </div>

                  {judgment && (
                    <>
                      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(160px, 1fr))', gap: '16px' }}>
                        <ScoreBar label="Overall" value={judgment.overall_score} />
                        <ScoreBar label="Safety" value={judgment.safety_score} />
                        <ScoreBar label="Correctness" value={judgment.correctness_score} />
                        <ScoreBar label="Instruction following" value={judgment.instruction_following_score} />
                      </div>

                      <div>
                        <h3 style={{ fontSize: '0.8rem', fontWeight: 600, color: 'var(--text-secondary)', marginBottom: '6px' }}>
                          Judge reasoning
                          <span style={{ fontWeight: 400, color: 'var(--text-tertiary)' }}>
                            {' '}· {judgment.judge_model}
                          </span>
                        </h3>
                        <div
                          style={{
                            background: 'rgba(0,0,0,0.2)',
                            padding: '12px',
                            borderRadius: '8px',
                            fontSize: '0.875rem',
                            lineHeight: 1.6,
                            whiteSpace: 'pre-wrap',
                          }}
                        >
                          {judgment.reasoning || 'No reasoning recorded.'}
                        </div>
                      </div>
                    </>
                  )}

                  {trace && (
                    <div style={{ display: 'flex', alignItems: 'center', gap: '20px', flexWrap: 'wrap', fontSize: '0.8rem', color: 'var(--text-secondary)' }}>
                      <span>Turns: {trace.turn_count ?? '—'}</span>
                      <span>Duration: {trace.duration_ms !== null ? `${trace.duration_ms} ms` : '—'}</span>
                      <span>Tokens: {trace.token_count ?? '—'}</span>
                      <span>Tool calls: {trace.tool_call_count ?? '—'}</span>
                      <span>Status: {trace.status}</span>

                      {trace.storage_key && (
                        <button
                          type="button"
                          onClick={() => handleDownload(trace.id)}
                          disabled={downloading === trace.id}
                          style={{
                            display: 'inline-flex',
                            alignItems: 'center',
                            gap: '6px',
                            padding: '6px 12px',
                            borderRadius: 'var(--radius-md)',
                            border: '1px solid var(--border-color)',
                            background: 'var(--bg-surface)',
                            color: 'var(--text-primary)',
                            cursor: downloading === trace.id ? 'wait' : 'pointer',
                            fontSize: '0.8rem',
                          }}
                        >
                          {downloading === trace.id ? (
                            <Loader2 size={14} className="animate-spin" />
                          ) : (
                            <Download size={14} />
                          )}
                          Download raw trace
                        </button>
                      )}
                    </div>
                  )}

                  {trace?.error_message && (
                    <div style={{ color: 'var(--danger-color)', fontSize: '0.8rem' }}>
                      Simulation error: {trace.error_message}
                    </div>
                  )}
                </div>
              )}
            </li>
          );
        })}
      </ul>
    </div>
  );
}
