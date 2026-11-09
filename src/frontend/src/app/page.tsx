'use client';

import { useQuery } from '@tanstack/react-query';

import ProtectedRoute from '@/components/auth/ProtectedRoute';
import EvaluationTable from '@/components/dashboard/EvaluationTable';
import { useAuth } from '@/contexts/AuthContext';
import { api } from '@/lib/api';
import { queryKeys } from '@/lib/queryKeys';

type DriftProfile = { score?: number | null } | null;
type EvaluationRun = { id: string; status: string; drift_profile: DriftProfile };

// Matches the backend's anomaly threshold in operations.get_operations_summary.
const DRIFT_ALERT_THRESHOLD = 0.25;

function StatCard({
  label,
  value,
  accent,
}: {
  label: string;
  value: number | string;
  accent?: string;
}) {
  return (
    <div className="glass" style={{ padding: '24px', borderRadius: '12px' }}>
      <div style={{ color: 'var(--text-tertiary)', fontSize: '0.875rem', marginBottom: '8px' }}>
        {label}
      </div>
      <div style={{ fontSize: '2rem', fontWeight: 700, color: accent }}>{value}</div>
    </div>
  );
}

export default function Home() {
  const { user } = useAuth();

  const { data: agents } = useQuery({
    queryKey: queryKeys.agents.list(),
    queryFn: async () => {
      const res = await api.get('/api/v1/agent-configs/');
      return res.data;
    },
  });

  // Shares its cache entry with EvaluationTable's first page instead of using a
  // separate key for the same endpoint.
  const { data: evaluations } = useQuery<EvaluationRun[]>({
    queryKey: queryKeys.evaluations.list(0, 100),
    queryFn: async () => {
      const res = await api.get('/api/v1/evaluations/?skip=0&limit=100');
      return res.data;
    },
  });

  const agentCount = Array.isArray(agents) ? agents.length : 0;
  const evalCount = Array.isArray(evaluations) ? evaluations.length : 0;

  // Derived from real drift profiles. This card was hardcoded to 0, so it never
  // reflected anything. Computing it client-side keeps it available to members,
  // who are not permitted to call the admin-only /operations/summary endpoint.
  const driftAlerts = Array.isArray(evaluations)
    ? evaluations.filter((run) => {
        const score = run.drift_profile?.score;
        return typeof score === 'number' && score > DRIFT_ALERT_THRESHOLD;
      }).length
    : 0;

  return (
    <ProtectedRoute>
      <div>
        <h1 style={{ fontSize: '1.5rem', fontWeight: 600, marginBottom: '24px' }}>
          Welcome back, {user?.full_name || 'Developer'}
        </h1>

        <div
          style={{
            display: 'grid',
            gridTemplateColumns: 'repeat(auto-fit, minmax(220px, 1fr))',
            gap: '24px',
            marginBottom: '32px',
          }}
        >
          <StatCard label="Total Agents" value={agentCount} />
          <StatCard label="Evaluations Run" value={evalCount} />
          <StatCard
            label="Drift Alerts"
            value={driftAlerts}
            accent={driftAlerts > 0 ? 'var(--danger-color)' : 'var(--success-color)'}
          />
        </div>

        <EvaluationTable />
      </div>
    </ProtectedRoute>
  );
}
