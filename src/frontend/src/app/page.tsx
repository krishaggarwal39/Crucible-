'use client';

import ProtectedRoute from '@/components/auth/ProtectedRoute';
import { useAuth } from '@/contexts/AuthContext';
import EvaluationTable from '@/components/dashboard/EvaluationTable';

export default function Home() {
  const { user } = useAuth();

  return (
    <ProtectedRoute>
      <div>
        <h1 style={{ fontSize: '1.5rem', fontWeight: 600, marginBottom: '24px' }}>
          Welcome back, {user?.name || 'Developer'}
        </h1>
      
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3, 1fr)', gap: '24px', marginBottom: '32px' }}>
        <div className="glass" style={{ padding: '24px', borderRadius: '12px' }}>
          <div style={{ color: 'var(--text-tertiary)', fontSize: '0.875rem', marginBottom: '8px' }}>Total Agents</div>
          <div style={{ fontSize: '2rem', fontWeight: 700 }}>12</div>
        </div>
        <div className="glass" style={{ padding: '24px', borderRadius: '12px' }}>
          <div style={{ color: 'var(--text-tertiary)', fontSize: '0.875rem', marginBottom: '8px' }}>Evaluations Run</div>
          <div style={{ fontSize: '2rem', fontWeight: 700 }}>1,248</div>
        </div>
        <div className="glass" style={{ padding: '24px', borderRadius: '12px' }}>
          <div style={{ color: 'var(--text-tertiary)', fontSize: '0.875rem', marginBottom: '8px' }}>Drift Alerts</div>
          <div style={{ fontSize: '2rem', fontWeight: 700, color: 'var(--danger-color)' }}>3</div>
        </div>
      </div>

      <EvaluationTable />
    </div>
    </ProtectedRoute>
  );
}
