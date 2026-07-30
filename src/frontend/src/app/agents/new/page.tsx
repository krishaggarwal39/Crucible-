'use client';

import { useState } from 'react';
import { useRouter } from 'next/navigation';
import { api } from '@/lib/api';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import { ArrowLeft, Save } from 'lucide-react';
import Link from 'next/link';
import { useAuth } from '@/contexts/AuthContext';
import { useEffect } from 'react';

import ProtectedRoute from '@/components/auth/ProtectedRoute';
import { queryKeys } from '@/lib/queryKeys';

function NewAgentForm() {
  const router = useRouter();
  const queryClient = useQueryClient();
  const { user } = useAuth();
  
  useEffect(() => {
    if (user && user.role !== 'admin') {
      router.push('/');
    }
  }, [user, router]);
  
  const [formData, setFormData] = useState({
    name: '',
    description: '',
    connector_type: 'rest_api',
    endpoint_url: '',
    system_prompt: '',
    tool_definitions: '{\n  "tools": []\n}',
    auth_config_plaintext: ''
  });
  
  const [jsonError, setJsonError] = useState<string | null>(null);

  const mutation = useMutation({
    mutationFn: async (data: Record<string, unknown>) => {
      const res = await api.post('/api/v1/agent-configs/', data);
      return res.data;
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: queryKeys.agents.all });
      router.push('/agents');
    }
  });

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    setJsonError(null);
    
    let parsedTools = null;
    try {
      if (formData.tool_definitions.trim()) {
        parsedTools = JSON.parse(formData.tool_definitions);
      }
    } catch (err) {
      setJsonError(
        `Invalid JSON in tools: ${err instanceof Error ? err.message : String(err)}`,
      );
      return;
    }

    mutation.mutate({
      ...formData,
      tool_definitions: parsedTools
    });
  };

  const handleChange = (e: React.ChangeEvent<HTMLInputElement | HTMLTextAreaElement | HTMLSelectElement>) => {
    setFormData({
      ...formData,
      [e.target.name]: e.target.value
    });
  };

  return (
    <div style={{ maxWidth: '800px', margin: '0 auto' }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: '16px', marginBottom: '32px' }}>
        <Link href="/agents" aria-label="Back to agents" style={{ display: 'flex', alignItems: 'center', color: 'var(--text-secondary)' }}>
          <ArrowLeft size={20} />
        </Link>
        <div>
          <h1 style={{ fontSize: '1.5rem', fontWeight: 600 }}>Create Immutable Agent Config</h1>
          <p style={{ color: 'var(--text-tertiary)', fontSize: '0.875rem' }}>Creates a V1 snapshot that can be evaluated.</p>
        </div>
      </div>

      <form onSubmit={handleSubmit} className="glass" style={{ padding: '32px', borderRadius: '12px', display: 'flex', flexDirection: 'column', gap: '24px' }}>
        
        <div style={{ display: 'flex', gap: '24px' }}>
          <div style={{ flex: 2 }}>
            <label htmlFor="name" style={{ display: 'block', marginBottom: '8px', fontWeight: 500 }}>Agent Name *</label>
            <input 
              required
              type="text" 
              id="name"
            name="name"
              value={formData.name}
              onChange={handleChange}
              style={{ width: '100%', padding: '10px 12px', borderRadius: 'var(--radius-md)', border: '1px solid var(--border-color)', background: 'var(--bg-surface)', color: 'var(--text-primary)' }}
              placeholder="e.g. Code Reviewer Bot"
            />
          </div>
          <div style={{ flex: 1 }}>
            <label htmlFor="connector_type" style={{ display: 'block', marginBottom: '8px', fontWeight: 500 }}>Connector Type</label>
            <select 
              id="connector_type"
            name="connector_type"
              value={formData.connector_type}
              onChange={handleChange}
              style={{ width: '100%', padding: '10px 12px', borderRadius: 'var(--radius-md)', border: '1px solid var(--border-color)', background: 'var(--bg-surface)', color: 'var(--text-primary)' }}
            >
              <option value="rest_api">REST API</option>
              <option value="sdk">SDK</option>
              <option value="mcp">MCP</option>
            </select>
          </div>
        </div>

        <div>
          <label htmlFor="endpoint_url" style={{ display: 'block', marginBottom: '8px', fontWeight: 500 }}>Endpoint URL</label>
          <input 
            type="url" 
            id="endpoint_url"
            name="endpoint_url"
            value={formData.endpoint_url}
            onChange={handleChange}
            style={{ width: '100%', padding: '10px 12px', borderRadius: 'var(--radius-md)', border: '1px solid var(--border-color)', background: 'var(--bg-surface)', color: 'var(--text-primary)' }}
            placeholder="https://api.youragent.com/chat"
          />
        </div>

        <div>
          <label htmlFor="description" style={{ display: 'block', marginBottom: '8px', fontWeight: 500 }}>Description</label>
          <input 
            type="text" 
            id="description"
            name="description"
            value={formData.description}
            onChange={handleChange}
            style={{ width: '100%', padding: '10px 12px', borderRadius: 'var(--radius-md)', border: '1px solid var(--border-color)', background: 'var(--bg-surface)', color: 'var(--text-primary)' }}
          />
        </div>

        <div>
          <label htmlFor="system_prompt" style={{ display: 'block', marginBottom: '8px', fontWeight: 500 }}>System Prompt</label>
          <textarea 
            id="system_prompt"
            name="system_prompt"
            value={formData.system_prompt}
            onChange={handleChange}
            rows={5}
            style={{ width: '100%', padding: '12px', borderRadius: 'var(--radius-md)', border: '1px solid var(--border-color)', background: 'var(--bg-surface)', color: 'var(--text-primary)', fontFamily: 'inherit', resize: 'vertical' }}
            placeholder="You are a helpful assistant..."
          />
        </div>

        <div>
          <label htmlFor="tool_definitions" style={{ display: 'block', marginBottom: '8px', fontWeight: 500 }}>Tool Definitions (JSON)</label>
          <textarea 
            id="tool_definitions"
            name="tool_definitions"
            value={formData.tool_definitions}
            onChange={handleChange}
            rows={8}
            style={{ width: '100%', padding: '12px', borderRadius: 'var(--radius-md)', border: '1px solid var(--border-color)', background: '#000', color: '#a8c7fa', fontFamily: 'var(--font-mono)', resize: 'vertical' }}
          />
          {jsonError && (
            <div style={{ marginTop: '8px', color: 'var(--danger-color)', fontSize: '0.875rem' }}>{jsonError}</div>
          )}
        </div>

        <div>
          <label htmlFor="auth_config_plaintext" style={{ display: 'block', marginBottom: '8px', fontWeight: 500 }}>Auth Credential <span style={{ fontWeight: 400, color: 'var(--text-tertiary)' }}>(encrypted at rest)</span></label>
          <input 
            type="password"
            autoComplete="off"
            id="auth_config_plaintext"
            name="auth_config_plaintext"
            value={formData.auth_config_plaintext}
            onChange={handleChange}
            style={{ width: '100%', padding: '10px 12px', borderRadius: 'var(--radius-md)', border: '1px solid var(--border-color)', background: 'var(--bg-surface)', color: 'var(--text-primary)' }}
            placeholder="Bearer sk-..."
          />
        </div>

        <div style={{ display: 'flex', justifyContent: 'flex-end', marginTop: '16px' }}>
          <button 
            type="submit"
            disabled={mutation.isPending}
            style={{ 
              display: 'flex', 
              alignItems: 'center', 
              gap: '8px',
              backgroundColor: 'var(--primary-color)',
              color: 'white',
              padding: '10px 24px',
              borderRadius: 'var(--radius-md)',
              fontWeight: 500,
              cursor: mutation.isPending ? 'not-allowed' : 'pointer',
              border: 'none',
              opacity: mutation.isPending ? 0.7 : 1
            }}
          >
            <Save size={18} />
            <span>{mutation.isPending ? 'Saving...' : 'Save Agent Config'}</span>
          </button>
        </div>
        
        {mutation.isError && (
          <div role="alert" style={{ color: 'var(--danger-color)', fontSize: '0.875rem', textAlign: 'right' }}>
            {(() => {
              const detail = (mutation.error as { response?: { data?: { detail?: unknown } } } | null)
                ?.response?.data?.detail;
              return typeof detail === 'string' ? detail : 'Failed to save configuration.';
            })()}
          </div>
        )}
      </form>
    </div>
  );
}

export default function NewAgentPage() {
  // Admin-only page: wrapped like every other route instead of relying solely on
  // a useEffect redirect that lets content render first.
  return (
    <ProtectedRoute>
      <NewAgentForm />
    </ProtectedRoute>
  );
}
