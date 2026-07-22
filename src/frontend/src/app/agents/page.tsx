'use client';

import { useQuery } from '@tanstack/react-query';
import { api } from '@/lib/api';
import Link from 'next/link';
import { Plus, Bot, MoreVertical } from 'lucide-react';
import { useState, useEffect } from 'react';
import { useAuth } from '@/contexts/AuthContext';
import { useRouter } from 'next/navigation';

type AgentConfig = {
  id: string;
  name: string;
  description: string;
  connector_type: string;
  created_at: string;
};

export default function AgentsPage() {
  const { user } = useAuth();
  const router = useRouter();

  useEffect(() => {
    if (user && user.role !== 'admin') {
      router.push('/');
    }
  }, [user, router]);

  const { data: agents, isLoading, error } = useQuery<AgentConfig[]>({
    queryKey: ['agent-configs'],
    queryFn: async () => {
      const { data } = await api.get('/api/v1/agent-configs/');
      return data;
    },
  });

  return (
    <div>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '24px' }}>
        <h1 style={{ fontSize: '1.5rem', fontWeight: 600 }}>Agent Configurations</h1>
        <Link 
          href="/agents/new"
          style={{ 
            display: 'flex', 
            alignItems: 'center', 
            gap: '8px',
            backgroundColor: 'var(--primary-color)',
            color: 'white',
            padding: '8px 16px',
            borderRadius: 'var(--radius-md)',
            fontWeight: 500,
            transition: 'background 0.2s'
          }}
        >
          <Plus size={18} />
          <span>New Agent</span>
        </Link>
      </div>

      {isLoading ? (
        <div style={{ padding: '40px', textAlign: 'center', color: 'var(--text-tertiary)' }}>Loading agents...</div>
      ) : error ? (
        <div style={{ padding: '24px', backgroundColor: 'var(--danger-color)', color: 'white', borderRadius: 'var(--radius-md)' }}>
          Failed to load agents. Ensure you are logged in.
        </div>
      ) : agents?.length === 0 ? (
        <div className="glass" style={{ padding: '60px', textAlign: 'center', borderRadius: '12px' }}>
          <Bot size={48} style={{ margin: '0 auto 16px', color: 'var(--text-tertiary)' }} />
          <h3 style={{ fontSize: '1.125rem', fontWeight: 500, marginBottom: '8px' }}>No Agents Found</h3>
          <p style={{ color: 'var(--text-secondary)' }}>Get started by creating your first AI agent configuration.</p>
        </div>
      ) : (
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(350px, 1fr))', gap: '24px' }}>
          {agents?.map(agent => (
            <div key={agent.id} className="glass" style={{ padding: '24px', borderRadius: '12px', display: 'flex', flexDirection: 'column' }}>
              <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', marginBottom: '12px' }}>
                <div style={{ display: 'flex', alignItems: 'center', gap: '12px' }}>
                  <div style={{ 
                    width: '40px', height: '40px', borderRadius: 'var(--radius-sm)', 
                    background: 'var(--primary-light)', color: 'var(--primary-color)',
                    display: 'flex', alignItems: 'center', justifyContent: 'center'
                  }}>
                    <Bot size={24} />
                  </div>
                  <div>
                    <h3 style={{ fontWeight: 600, fontSize: '1.125rem' }}>{agent.name}</h3>
                    <div style={{ fontSize: '0.75rem', color: 'var(--text-tertiary)' }}>
                      {agent.connector_type} • Created {new Date(agent.created_at).toLocaleDateString()}
                    </div>
                  </div>
                </div>
                <button style={{ background: 'transparent', border: 'none', color: 'var(--text-tertiary)', cursor: 'pointer' }}>
                  <MoreVertical size={20} />
                </button>
              </div>
              
              <p style={{ color: 'var(--text-secondary)', fontSize: '0.875rem', marginBottom: '24px', flex: 1 }}>
                {agent.description || "No description provided."}
              </p>
              
              <div style={{ display: 'flex', gap: '12px', borderTop: '1px solid var(--border-color)', paddingTop: '16px' }}>
                <Link 
                  href={`/evaluations/new?agent=${agent.id}`}
                  style={{ flex: 1, textAlign: 'center', padding: '8px', background: 'var(--bg-surface)', borderRadius: 'var(--radius-md)', fontSize: '0.875rem', fontWeight: 500 }}
                >
                  Evaluate
                </Link>
                <Link 
                  href={`/agents/${agent.id}`}
                  style={{ flex: 1, textAlign: 'center', padding: '8px', background: 'var(--bg-surface)', borderRadius: 'var(--radius-md)', fontSize: '0.875rem', fontWeight: 500 }}
                >
                  View Details
                </Link>
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
