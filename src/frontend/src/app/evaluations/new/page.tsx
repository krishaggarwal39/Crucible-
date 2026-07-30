'use client';

import { Suspense, useState } from 'react';
import { useRouter, useSearchParams } from 'next/navigation';
import { useMutation, useQuery } from '@tanstack/react-query';
import { ArrowLeft, Play } from 'lucide-react';
import Link from 'next/link';

import ProtectedRoute from '@/components/auth/ProtectedRoute';
import { api } from '@/lib/api';
import { queryKeys } from '@/lib/queryKeys';

/**
 * Start an evaluation run.
 *
 * This form previously collected a `dataset_id` — a field the API has no concept
 * of, which it silently discarded — and never sent `run_config` at all, so every
 * dashboard-initiated run used server defaults. The inputs below map exactly to
 * the RunConfig schema the backend validates.
 */

const CATEGORIES = [
  'prompt_injection',
  'tool_misuse',
  'data_exfiltration',
  'jailbreak',
  'hallucination',
  'safety',
  'correctness',
  'general',
] as const;

const SEVERITIES = ['low', 'medium', 'high', 'critical'] as const;

const inputStyle: React.CSSProperties = {
  width: '100%',
  padding: '10px 12px',
  borderRadius: 'var(--radius-md)',
  border: '1px solid var(--border-color)',
  background: 'var(--bg-surface)',
  color: 'var(--text-primary)',
};

const labelStyle: React.CSSProperties = {
  display: 'block',
  marginBottom: '8px',
  fontWeight: 500,
};

type AgentOption = { id: string; name: string };

function NewEvaluationForm() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const initialAgentId = searchParams.get('agent') || '';

  const [name, setName] = useState('');
  const [agentConfigId, setAgentConfigId] = useState(initialAgentId);
  const [scenarioCount, setScenarioCount] = useState(10);
  const [budgetUsd, setBudgetUsd] = useState(5);
  const [passThreshold, setPassThreshold] = useState(70);
  const [categories, setCategories] = useState<string[]>([]);
  const [severities, setSeverities] = useState<string[]>([]);
  const [redTeamEnabled, setRedTeamEnabled] = useState(true);

  const { data: agents } = useQuery<AgentOption[]>({
    queryKey: queryKeys.agents.list(),
    queryFn: async () => {
      const { data } = await api.get('/api/v1/agent-configs/');
      return data;
    },
  });

  const mutation = useMutation({
    mutationFn: async () => {
      const res = await api.post('/api/v1/evaluations/', {
        name,
        agent_config_id: agentConfigId,
        run_config: {
          scenario_count: scenarioCount,
          eval_budget_usd: budgetUsd,
          pass_threshold: passThreshold,
          categories,
          severity_levels: severities,
          red_team_enabled: redTeamEnabled,
        },
      });
      return res.data;
    },
    onSuccess: (data) => {
      router.push(`/evaluations/${data.id}`);
    },
  });

  const toggle = (
    value: string,
    list: string[],
    setList: (next: string[]) => void,
  ) => {
    setList(list.includes(value) ? list.filter((v) => v !== value) : [...list, value]);
  };

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    mutation.mutate();
  };

  const errorDetail =
    (mutation.error as { response?: { data?: { detail?: unknown } } } | null)?.response?.data
      ?.detail;

  return (
    <div style={{ maxWidth: '640px', margin: '0 auto' }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: '16px', marginBottom: '32px' }}>
        <Link href="/agents" aria-label="Back to agents" style={{ display: 'flex', alignItems: 'center', color: 'var(--text-secondary)' }}>
          <ArrowLeft size={20} />
        </Link>
        <div>
          <h1 style={{ fontSize: '1.5rem', fontWeight: 600 }}>Start Evaluation Run</h1>
          <p style={{ color: 'var(--text-tertiary)', fontSize: '0.875rem' }}>
            Triggers the LangGraph evaluation pipeline.
          </p>
        </div>
      </div>

      <form
        onSubmit={handleSubmit}
        className="glass"
        style={{ padding: '32px', borderRadius: '12px', display: 'flex', flexDirection: 'column', gap: '24px' }}
      >
        <div>
          {/* htmlFor/id pairs: every label here is now programmatically associated
              with its control, which the previous version omitted entirely. */}
          <label htmlFor="run-name" style={labelStyle}>Run Name *</label>
          <input
            required
            id="run-name"
            type="text"
            value={name}
            onChange={(e) => setName(e.target.value)}
            style={inputStyle}
            placeholder="e.g. Nightly benchmark run"
          />
        </div>

        <div>
          <label htmlFor="agent-config" style={labelStyle}>Agent Configuration *</label>
          <select
            required
            id="agent-config"
            value={agentConfigId}
            onChange={(e) => setAgentConfigId(e.target.value)}
            style={inputStyle}
          >
            <option value="" disabled>Select an agent configuration</option>
            {agents?.map((agent) => (
              <option key={agent.id} value={agent.id}>{agent.name}</option>
            ))}
          </select>
        </div>

        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(170px, 1fr))', gap: '16px' }}>
          <div>
            <label htmlFor="scenario-count" style={labelStyle}>Scenarios</label>
            <input
              id="scenario-count"
              type="number"
              min={1}
              max={500}
              value={scenarioCount}
              onChange={(e) => setScenarioCount(Number(e.target.value))}
              style={inputStyle}
              aria-describedby="scenario-count-help"
            />
            <p id="scenario-count-help" style={{ fontSize: '0.75rem', color: 'var(--text-tertiary)', marginTop: '4px' }}>
              1–500
            </p>
          </div>

          <div>
            <label htmlFor="budget" style={labelStyle}>Budget (USD)</label>
            <input
              id="budget"
              type="number"
              min={0.1}
              max={100}
              step={0.1}
              value={budgetUsd}
              onChange={(e) => setBudgetUsd(Number(e.target.value))}
              style={inputStyle}
            />
          </div>

          <div>
            <label htmlFor="pass-threshold" style={labelStyle}>Pass threshold</label>
            <input
              id="pass-threshold"
              type="number"
              min={0}
              max={100}
              value={passThreshold}
              onChange={(e) => setPassThreshold(Number(e.target.value))}
              style={inputStyle}
            />
          </div>
        </div>

        <fieldset style={{ border: 'none' }}>
          <legend style={{ ...labelStyle, marginBottom: '12px' }}>
            Categories <span style={{ color: 'var(--text-tertiary)', fontWeight: 400 }}>(all if none selected)</span>
          </legend>
          <div style={{ display: 'flex', flexWrap: 'wrap', gap: '8px 16px' }}>
            {CATEGORIES.map((c) => (
              <label key={c} style={{ display: 'inline-flex', alignItems: 'center', gap: '6px', fontSize: '0.85rem' }}>
                <input
                  type="checkbox"
                  checked={categories.includes(c)}
                  onChange={() => toggle(c, categories, setCategories)}
                />
                {c.replace(/_/g, ' ')}
              </label>
            ))}
          </div>
        </fieldset>

        <fieldset style={{ border: 'none' }}>
          <legend style={{ ...labelStyle, marginBottom: '12px' }}>
            Severity levels <span style={{ color: 'var(--text-tertiary)', fontWeight: 400 }}>(all if none selected)</span>
          </legend>
          <div style={{ display: 'flex', flexWrap: 'wrap', gap: '8px 16px' }}>
            {SEVERITIES.map((s) => (
              <label key={s} style={{ display: 'inline-flex', alignItems: 'center', gap: '6px', fontSize: '0.85rem' }}>
                <input
                  type="checkbox"
                  checked={severities.includes(s)}
                  onChange={() => toggle(s, severities, setSeverities)}
                />
                {s}
              </label>
            ))}
          </div>
        </fieldset>

        <label style={{ display: 'inline-flex', alignItems: 'center', gap: '8px', fontWeight: 500 }}>
          <input
            type="checkbox"
            checked={redTeamEnabled}
            onChange={(e) => setRedTeamEnabled(e.target.checked)}
          />
          Enable adversarial red-teaming
        </label>

        <div style={{ display: 'flex', justifyContent: 'flex-end', marginTop: '8px' }}>
          <button
            type="submit"
            disabled={mutation.isPending || !agentConfigId}
            style={{
              display: 'flex',
              alignItems: 'center',
              gap: '8px',
              backgroundColor: 'var(--success-color)',
              color: 'white',
              padding: '10px 24px',
              borderRadius: 'var(--radius-md)',
              fontWeight: 500,
              cursor: mutation.isPending || !agentConfigId ? 'not-allowed' : 'pointer',
              border: 'none',
              opacity: mutation.isPending || !agentConfigId ? 0.7 : 1,
            }}
          >
            <Play size={18} />
            <span>{mutation.isPending ? 'Starting...' : 'Run Evaluation'}</span>
          </button>
        </div>

        {mutation.isError && (
          <div role="alert" style={{ color: 'var(--danger-color)', fontSize: '0.875rem', textAlign: 'right' }}>
            {typeof errorDetail === 'string'
              ? errorDetail
              : 'Failed to start evaluation run.'}
          </div>
        )}
      </form>
    </div>
  );
}

export default function NewEvaluationPage() {
  return (
    <ProtectedRoute>
      <Suspense fallback={<div style={{ textAlign: 'center', padding: '40px' }}>Loading form...</div>}>
        <NewEvaluationForm />
      </Suspense>
    </ProtectedRoute>
  );
}
