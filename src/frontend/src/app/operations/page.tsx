'use client';

import { useQuery } from '@tanstack/react-query';
import { api } from '@/lib/api';
import { ScatterChart, Scatter, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer, Cell } from 'recharts';

export default function OperationsPage() {
  // We'll simulate fetching drift embeddings since the endpoint might not be fully wired up yet
  const { data: driftData, isLoading } = useQuery({
    queryKey: ['drift-analysis'],
    queryFn: async () => {
      try {
        const { data } = await api.get('/api/v1/operations/drift');
        return data;
      } catch (err) {
        // Fallback mock data for UI demonstration
        return Array.from({ length: 50 }).map((_, i) => ({
          x: (Math.random() - 0.5) * 10,
          y: (Math.random() - 0.5) * 10,
          drift_score: Math.random(),
          run_id: `run-${i}`
        }));
      }
    }
  });

  const getDotColor = (score: number) => {
    if (score > 0.7) return 'var(--danger-color)';
    if (score > 0.4) return 'var(--warning-color)';
    return 'var(--success-color)';
  };

  const CustomTooltip = ({ active, payload }: any) => {
    if (active && payload && payload.length) {
      const data = payload[0].payload;
      return (
        <div style={{ backgroundColor: 'var(--bg-card)', padding: '12px', border: '1px solid var(--border-color)', borderRadius: '8px' }}>
          <p style={{ margin: 0, fontWeight: 600 }}>Run: {data.run_id}</p>
          <p style={{ margin: '4px 0 0 0', color: 'var(--text-secondary)' }}>
            Drift Score: {data.drift_score.toFixed(2)}
          </p>
        </div>
      );
    }
    return null;
  };

  return (
    <div>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '24px' }}>
        <div>
          <h1 style={{ fontSize: '1.5rem', fontWeight: 600 }}>AI Operations</h1>
          <p style={{ color: 'var(--text-tertiary)', fontSize: '0.875rem' }}>Monitor behavioral drift and system anomalies.</p>
        </div>
      </div>

      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3, 1fr)', gap: '24px', marginBottom: '24px' }}>
        <div className="glass" style={{ padding: '24px', borderRadius: '12px' }}>
          <div style={{ color: 'var(--text-tertiary)', fontSize: '0.875rem', marginBottom: '8px' }}>Average Drift Score</div>
          <div style={{ fontSize: '2rem', fontWeight: 700 }}>0.32</div>
        </div>
        <div className="glass" style={{ padding: '24px', borderRadius: '12px' }}>
          <div style={{ color: 'var(--text-tertiary)', fontSize: '0.875rem', marginBottom: '8px' }}>Anomalous Runs</div>
          <div style={{ fontSize: '2rem', fontWeight: 700, color: 'var(--danger-color)' }}>7</div>
        </div>
        <div className="glass" style={{ padding: '24px', borderRadius: '12px' }}>
          <div style={{ color: 'var(--text-tertiary)', fontSize: '0.875rem', marginBottom: '8px' }}>Baseline Stability</div>
          <div style={{ fontSize: '2rem', fontWeight: 700, color: 'var(--success-color)' }}>92%</div>
        </div>
      </div>

      <div style={{ display: 'grid', gridTemplateColumns: '2fr 1fr', gap: '24px' }}>
        <div className="glass" style={{ padding: '24px', borderRadius: '12px', height: '500px' }}>
          <h2 style={{ fontSize: '1.125rem', fontWeight: 600, marginBottom: '24px' }}>Behavioral Drift Embeddings (PCA)</h2>
          
          {isLoading ? (
            <div style={{ display: 'flex', height: '100%', alignItems: 'center', justifyContent: 'center', color: 'var(--text-tertiary)' }}>
              Analyzing embeddings...
            </div>
          ) : (
            <div style={{ width: '100%', height: 'calc(100% - 48px)' }}>
              <ResponsiveContainer width="100%" height="100%">
                <ScatterChart margin={{ top: 20, right: 20, bottom: 20, left: 20 }}>
                  <CartesianGrid strokeDasharray="3 3" stroke="#333" />
                  <XAxis type="number" dataKey="x" name="PCA 1" stroke="#888" tick={{fill: '#888'}} />
                  <YAxis type="number" dataKey="y" name="PCA 2" stroke="#888" tick={{fill: '#888'}} />
                  <Tooltip content={<CustomTooltip />} cursor={{ strokeDasharray: '3 3' }} />
                  <Scatter name="Runs" data={driftData}>
                    {driftData?.map((entry: any, index: number) => (
                      <Cell key={`cell-${index}`} fill={getDotColor(entry.drift_score)} />
                    ))}
                  </Scatter>
                </ScatterChart>
              </ResponsiveContainer>
            </div>
          )}
        </div>

        <div style={{ display: 'flex', flexDirection: 'column', gap: '24px' }}>
          <div className="glass" style={{ padding: '24px', borderRadius: '12px' }}>
            <h2 style={{ fontSize: '1.125rem', fontWeight: 600, marginBottom: '16px' }}>Drift Profile</h2>
            <div style={{ display: 'flex', alignItems: 'center', gap: '12px', padding: '16px', background: 'var(--bg-surface)', borderRadius: 'var(--radius-md)' }}>
              <div style={{ width: '12px', height: '12px', borderRadius: '50%', backgroundColor: 'var(--warning-color)' }}></div>
              <div style={{ fontWeight: 600, fontSize: '1.125rem' }}>Minor Drift Detected</div>
            </div>
            <p style={{ marginTop: '16px', color: 'var(--text-secondary)', fontSize: '0.875rem', lineHeight: 1.5 }}>
              Agents are occasionally exhibiting prompt decay on complex multi-step reasoning tasks.
            </p>
          </div>

          <div className="glass" style={{ padding: '24px', borderRadius: '12px', flex: 1 }}>
            <h2 style={{ fontSize: '1.125rem', fontWeight: 600, marginBottom: '16px' }}>Evolution Suggestion</h2>
            <div style={{ padding: '16px', background: 'var(--bg-surface)', borderRadius: 'var(--radius-md)', border: '1px solid var(--border-color)', marginBottom: '24px' }}>
              <h3 style={{ fontSize: '0.875rem', color: 'var(--text-tertiary)', marginBottom: '8px' }}>PROPOSED SYSTEM PROMPT</h3>
              <p style={{ fontFamily: 'var(--font-mono)', fontSize: '0.875rem', color: '#a8c7fa', lineHeight: 1.5 }}>
                ... Added explicit constraint: "Always decompose multi-step tasks into sub-tasks before execution."
              </p>
            </div>
            <button style={{ 
              width: '100%', 
              padding: '10px', 
              backgroundColor: 'var(--primary-color)', 
              color: 'white', 
              border: 'none', 
              borderRadius: 'var(--radius-md)',
              fontWeight: 500,
              cursor: 'pointer'
            }}>
              Accept Evolution (Create V2)
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}
