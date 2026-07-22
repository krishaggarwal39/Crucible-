'use client';

import { useState } from 'react';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import { api } from '@/lib/api';
import { Star, Loader2, CheckCircle2 } from 'lucide-react';

import { useAuth } from '@/contexts/AuthContext';

export default function BaselineToggle({ runId, isBaseline }: { runId: string; isBaseline: boolean }) {
  const queryClient = useQueryClient();
  const [success, setSuccess] = useState(false);
  const { user } = useAuth();

  const mutation = useMutation({
    mutationFn: async () => {
      const res = await api.post(`/api/v1/evaluations/${runId}/baseline`);
      return res.data;
    },
    onSuccess: () => {
      setSuccess(true);
      queryClient.invalidateQueries({ queryKey: ['evaluations'] });
      queryClient.invalidateQueries({ queryKey: ['evaluation', runId] });
      setTimeout(() => setSuccess(false), 3000);
    },
  });

  if (user?.role !== 'admin') {
    return null;
  }

  if (isBaseline) {
    return (
      <div style={{ display: 'flex', alignItems: 'center', gap: '8px', padding: '8px 16px', backgroundColor: 'var(--warning-color)', color: '#fff', borderRadius: '8px', fontSize: '0.875rem', fontWeight: 600 }}>
        <Star size={16} fill="currentColor" />
        Current Golden Baseline
      </div>
    );
  }

  return (
    <button 
      onClick={() => mutation.mutate()} 
      disabled={mutation.isPending}
      style={{ 
        display: 'flex', 
        alignItems: 'center', 
        gap: '8px', 
        padding: '8px 16px', 
        backgroundColor: success ? 'var(--success-color)' : 'transparent', 
        color: success ? '#fff' : 'var(--warning-color)', 
        border: success ? 'none' : '1px solid var(--warning-color)', 
        borderRadius: '8px', 
        fontSize: '0.875rem', 
        fontWeight: 600,
        cursor: mutation.isPending ? 'not-allowed' : 'pointer',
        transition: 'all 0.2s'
      }}
    >
      {mutation.isPending ? (
        <Loader2 size={16} className="animate-spin" />
      ) : success ? (
        <CheckCircle2 size={16} />
      ) : (
        <Star size={16} />
      )}
      {success ? 'Baseline Updated' : 'Mark as Golden Baseline'}
    </button>
  );
}
