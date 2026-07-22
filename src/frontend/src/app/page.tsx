'use client';

import ProtectedRoute from '@/components/auth/ProtectedRoute';
import { useAuth } from '@/contexts/AuthContext';
import EvaluationTable from '@/components/dashboard/EvaluationTable';
import { useQuery } from '@tanstack/react-query';
import { api } from '@/lib/api';

export default function Home() {
  const { user } = useAuth();

  const { data: agents } = useQuery({
    queryKey: ['agents'],
    queryFn: async () => { const res = await api.get('/api/v1/agent-configs/'); return res.data; },
  });

  const { data: evaluations } = useQuery({
    queryKey: ['evaluations'],
    queryFn: async () => { const res = await api.get('/api/v1/evaluations'); return res.data; },
  });

  const agentCount = Array.isArray(agents) ? agents.length : 0;
  const evalCount = Array.isArray(evaluations) ? evaluations.length : 0;

  return (
    <ProtectedRoute>
      <div>
        <h1 style={{ fontSize: '1.5rem', fontWeight: 600, marginBottom: '24px' }}>
          Welcome back, {user?.full_name || 'Developer'}
        </h1>
      
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3, 1fr)', gap: '24px', marginBottom: '32px' }}>
        <div className="glass" style={{ padding: '24px', borderRadius: '12px' }}>
          <div style={{ color: 'var(--text-tertiary)', fontSize: '0.875rem', marginBottom: '8px' }}>Total Agents</div>
          <div style={{ fontSize: '2rem', fontWeight: 700 }}>{agentCount}</div>
        </div>
        <div className="glass" style={{ padding: '24px', borderRadius: '12px' }}>
          <div style={{ color: 'var(--text-tertiary)', fontSize: '0.875rem', marginBottom: '8px' }}>Evaluations Run</div>
          <div style={{ fontSize: '2rem', fontWeight: 700 }}>{evalCount}</div>
        </div>
        <div className="glass" style={{ padding: '24px', borderRadius: '12px' }}>
          <div style={{ color: 'var(--text-tertiary)', fontSize: '0.875rem', marginBottom: '8px' }}>Drift Alerts</div>
          <div style={{ fontSize: '2rem', fontWeight: 700, color: 'var(--danger-color)' }}>0</div>
        </div>
      </div>

      <EvaluationTable />
    </div>
    </ProtectedRoute>
  );
}

