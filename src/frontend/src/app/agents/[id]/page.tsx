'use client';

import Link from 'next/link';
import { useParams } from 'next/navigation';
import { useQuery } from '@tanstack/react-query';
import { ArrowLeft, Bot, Loader2, Play, ShieldCheck } from 'lucide-react';

import ProtectedRoute from '@/components/auth/ProtectedRoute';
import { api } from '@/lib/api';
import { queryKeys } from '@/lib/queryKeys';

/**
 * Agent detail page.
 *
 * The agents list linked every card to /agents/{id}, but this route did not
 * exist — "View Details" was a guaranteed 404.
 */

type AgentConfig = {
  id: string;
  name: string;
  description: string | null;
  connector_type: string;
  endpoint_url: string | null;
  system_prompt: string | null;
  tool_definitions: Record<string, unknown> | null;
  created_at: string;
  updated_at: string | null;
};

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div>
      <div style={{ fontSize: '0.75rem', color: 'var(--text-tertiary)', marginBottom: '4px' }}>
        {label}
      </div>
      <div style={{ fontSize: '0.9rem', wordBreak: 'break-word' }}>{children}</div>
    </div>
  );
}

export default function AgentDetailPage() {
  const params = useParams();
  const agentId = params?.id as string;

  const { data: agent, isLoading, isError } = useQuery<AgentConfig>({
    queryKey: queryKeys.agents.detail(agentId),
    queryFn: async () => {
      const res = await api.get(`/api/v1/agent-configs/${agentId}`);
      return res.data;
    },
    enabled: !!agentId,
  });

  if (isLoading) {
    return (
      <ProtectedRoute>
        <div style={{ display: 'flex', justifyContent: 'center', alignItems: 'center', height: '60vh' }}>
          <Loader2 className="animate-spin" style={{ color: 'var(--primary-color)' }} size={32} />
          <span className="sr-only">Loading agent</span>
        </div>
      </ProtectedRoute>
    );
  }

  if (isError || !agent) {
    return (
      <ProtectedRoute>
        <div style={{ maxWidth: '800px', margin: '0 auto' }}>
          <Link
            href="/agents"
            style={{ color: 'var(--text-tertiary)', display: 'inline-flex', alignItems: 'center', gap: '4px', fontSize: '0.875rem', marginBottom: '16px' }}
          >
            <ArrowLeft size={16} /> Back to Agents
          </Link>
          <div className="glass" style={{ padding: '24px', borderRadius: '12px', color: 'var(--danger-color)' }}>
            Could not load this agent configuration. It may have been deleted.
          </div>
        </div>
      </ProtectedRoute>
    );
  }

  return (
    <ProtectedRoute>
      <div style={{ maxWidth: '900px', margin: '0 auto' }}>
        <Link
          href="/agents"
          style={{ color: 'var(--text-tertiary)', display: 'inline-flex', alignItems: 'center', gap: '4px', fontSize: '0.875rem', marginBottom: '16px' }}
        >
          <ArrowLeft size={16} /> Back to Agents
        </Link>

        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', gap: '16px', flexWrap: 'wrap', marginBottom: '24px' }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: '16px' }}>
            <div
              style={{
                width: '48px',
                height: '48px',
                borderRadius: 'var(--radius-md)',
                background: 'var(--primary-light)',
                color: 'var(--primary-color)',
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'center',
              }}
            >
              <Bot size={28} />
            </div>
            <div>
              <h1 style={{ fontSize: '1.5rem', fontWeight: 700 }}>{agent.name}</h1>
              <div style={{ fontSize: '0.8rem', color: 'var(--text-tertiary)' }}>
                {agent.connector_type} · created {new Date(agent.created_at).toLocaleDateString()}
              </div>
            </div>
          </div>

          <Link
            href={`/evaluations/new?agent=${agent.id}`}
            style={{
              display: 'inline-flex',
              alignItems: 'center',
              gap: '8px',
              backgroundColor: 'var(--success-color)',
              color: 'white',
              padding: '10px 20px',
              borderRadius: 'var(--radius-md)',
              fontWeight: 500,
            }}
          >
            <Play size={16} /> Evaluate
          </Link>
        </div>

        <div className="glass" style={{ padding: '24px', borderRadius: '12px', display: 'grid', gap: '20px', marginBottom: '24px' }}>
          <Field label="Description">{agent.description || '—'}</Field>
          <Field label="Endpoint URL">
            <code style={{ fontFamily: 'var(--font-mono)', fontSize: '0.85rem' }}>
              {agent.endpoint_url || '—'}
            </code>
          </Field>
          <Field label="Credentials">
            <span style={{ display: 'inline-flex', alignItems: 'center', gap: '6px', color: 'var(--text-secondary)' }}>
              <ShieldCheck size={14} style={{ color: 'var(--success-color)' }} />
              Stored encrypted; never returned by the API.
            </span>
          </Field>
        </div>

        <div className="glass" style={{ padding: '24px', borderRadius: '12px', marginBottom: '24px' }}>
          <h2 style={{ fontSize: '1rem', fontWeight: 600, marginBottom: '12px' }}>System Prompt</h2>
          <pre
            style={{
              background: 'rgba(0,0,0,0.25)',
              padding: '16px',
              borderRadius: '8px',
              fontSize: '0.85rem',
              whiteSpace: 'pre-wrap',
              overflowX: 'auto',
              fontFamily: 'var(--font-mono)',
            }}
          >
            {agent.system_prompt || '(none set)'}
          </pre>
        </div>

        {agent.tool_definitions && (
          <div className="glass" style={{ padding: '24px', borderRadius: '12px' }}>
            <h2 style={{ fontSize: '1rem', fontWeight: 600, marginBottom: '12px' }}>Tool Definitions</h2>
            <pre
              style={{
                background: 'rgba(0,0,0,0.25)',
                padding: '16px',
                borderRadius: '8px',
                fontSize: '0.8rem',
                whiteSpace: 'pre-wrap',
                overflowX: 'auto',
                fontFamily: 'var(--font-mono)',
              }}
            >
              {JSON.stringify(agent.tool_definitions, null, 2)}
            </pre>
          </div>
        )}
      </div>
    </ProtectedRoute>
  );
}
