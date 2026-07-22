'use client';

import { useState, useEffect } from 'react';
import { useRouter, useSearchParams } from 'next/navigation';
import { api } from '@/lib/api';
import { useMutation, useQuery } from '@tanstack/react-query';
import { ArrowLeft, Play } from 'lucide-react';
import Link from 'next/link';

export default function NewEvaluationPage() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const initialAgentId = searchParams.get('agent') || '';
  
  const [formData, setFormData] = useState({
    name: '',
    agent_config_id: initialAgentId,
    dataset_id: ''
  });

  const { data: agents } = useQuery({
    queryKey: ['agent-configs'],
    queryFn: async () => {
      const { data } = await api.get('/api/v1/agent-configs/');
      return data;
    },
  });

  const mutation = useMutation({
    mutationFn: async (data: any) => {
      const res = await api.post('/api/v1/evaluations/', data);
      return res.data;
    },
    onSuccess: (data) => {
      router.push(`/evaluations/${data.id}`);
    }
  });

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    mutation.mutate({
      name: formData.name,
      agent_config_id: formData.agent_config_id,
      dataset_id: formData.dataset_id || null // send null if empty
    });
  };

  const handleChange = (e: React.ChangeEvent<HTMLSelectElement | HTMLInputElement>) => {
    setFormData({
      ...formData,
      [e.target.name]: e.target.value
    });
  };

  return (
    <div style={{ maxWidth: '600px', margin: '0 auto' }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: '16px', marginBottom: '32px' }}>
        <Link href="/agents" style={{ display: 'flex', alignItems: 'center', color: 'var(--text-secondary)' }}>
          <ArrowLeft size={20} />
        </Link>
        <div>
          <h1 style={{ fontSize: '1.5rem', fontWeight: 600 }}>Start Evaluation Run</h1>
          <p style={{ color: 'var(--text-tertiary)', fontSize: '0.875rem' }}>Triggers LangGraph execution pipeline.</p>
        </div>
      </div>

      <form onSubmit={handleSubmit} className="glass" style={{ padding: '32px', borderRadius: '12px', display: 'flex', flexDirection: 'column', gap: '24px' }}>
        
        <div>
          <label style={{ display: 'block', marginBottom: '8px', fontWeight: 500 }}>Run Name *</label>
          <input 
            required
            type="text"
            name="name"
            value={formData.name}
            onChange={handleChange}
            style={{ width: '100%', padding: '10px 12px', borderRadius: 'var(--radius-md)', border: '1px solid var(--border-color)', background: 'var(--bg-surface)', color: 'var(--text-primary)' }}
            placeholder="e.g. Nightly benchmark run"
          />
        </div>

        <div>
          <label style={{ display: 'block', marginBottom: '8px', fontWeight: 500 }}>Agent Configuration *</label>
          <select 
            required
            name="agent_config_id"
            value={formData.agent_config_id}
            onChange={handleChange}
            style={{ width: '100%', padding: '10px 12px', borderRadius: 'var(--radius-md)', border: '1px solid var(--border-color)', background: 'var(--bg-surface)', color: 'var(--text-primary)' }}
          >
            <option value="" disabled>Select an agent configuration</option>
            {agents?.map((agent: any) => (
              <option key={agent.id} value={agent.id}>{agent.name} (v1)</option>
            ))}
          </select>
        </div>

        <div>
          <label style={{ display: 'block', marginBottom: '8px', fontWeight: 500 }}>Dataset ID (Optional)</label>
          <input 
            type="text"
            name="dataset_id"
            value={formData.dataset_id}
            onChange={handleChange}
            style={{ width: '100%', padding: '10px 12px', borderRadius: 'var(--radius-md)', border: '1px solid var(--border-color)', background: 'var(--bg-surface)', color: 'var(--text-primary)' }}
            placeholder="e.g. ds-1234 (Leave blank for default scenario)"
          />
        </div>

        <div style={{ display: 'flex', justifyContent: 'flex-end', marginTop: '16px' }}>
          <button 
            type="submit"
            disabled={mutation.isPending || !formData.agent_config_id}
            style={{ 
              display: 'flex', 
              alignItems: 'center', 
              gap: '8px',
              backgroundColor: 'var(--success-color)',
              color: 'white',
              padding: '10px 24px',
              borderRadius: 'var(--radius-md)',
              fontWeight: 500,
              cursor: (mutation.isPending || !formData.agent_config_id) ? 'not-allowed' : 'pointer',
              border: 'none',
              opacity: (mutation.isPending || !formData.agent_config_id) ? 0.7 : 1
            }}
          >
            <Play size={18} />
            <span>{mutation.isPending ? 'Starting...' : 'Run Evaluation'}</span>
          </button>
        </div>
        
        {mutation.isError && (
          <div style={{ color: 'var(--danger-color)', fontSize: '0.875rem', textAlign: 'right' }}>
            Failed to start evaluation run.
          </div>
        )}
      </form>
    </div>
  );
}
