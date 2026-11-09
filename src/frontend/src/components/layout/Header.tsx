'use client';

import { useState, useRef, useEffect } from 'react';
import { useRouter } from 'next/navigation';
import { useQuery } from '@tanstack/react-query';
import { api } from '@/lib/api';
import styles from './Header.module.css';
import { queryKeys } from '@/lib/queryKeys';
import { Bell, Search, User, X } from 'lucide-react';

type SearchableRecord = { id: string; name: string; status?: string };

type SearchResult = {
  type: 'agent' | 'evaluation';
  id: string;
  name: string;
  status?: string;
};

export default function Header() {
  const [query, setQuery] = useState('');
  const [isOpen, setIsOpen] = useState(false);
  const router = useRouter();
  const inputRef = useRef<HTMLInputElement>(null);
  const dropdownRef = useRef<HTMLDivElement>(null);

  // Fetch agents and evaluations for search
  const { data: agents } = useQuery({
    queryKey: queryKeys.agents.list(),
    queryFn: async () => { const res = await api.get('/api/v1/agent-configs/'); return res.data; },
  });

  const { data: evaluations } = useQuery({
    queryKey: queryKeys.evaluations.list(0, 50),
    queryFn: async () => { const res = await api.get('/api/v1/evaluations/?skip=0&limit=50'); return res.data; },
  });

  // Filter results based on query
  const results: SearchResult[] = [];
  if (query.trim().length >= 2) {
    const lowerQuery = query.toLowerCase();
    
    if (Array.isArray(agents)) {
      (agents as SearchableRecord[])
        .filter((a) => a.name.toLowerCase().includes(lowerQuery) || a.id.includes(lowerQuery))
        .slice(0, 5)
        .forEach((a) => results.push({ type: 'agent', id: a.id, name: a.name }));
    }

    if (Array.isArray(evaluations)) {
      (evaluations as SearchableRecord[])
        .filter((e) => e.name.toLowerCase().includes(lowerQuery) || e.id.includes(lowerQuery))
        .slice(0, 5)
        .forEach((e) => results.push({ type: 'evaluation', id: e.id, name: e.name, status: e.status }));
    }
  }

  // Close dropdown on outside click
  useEffect(() => {
    const handleClickOutside = (e: MouseEvent) => {
      if (dropdownRef.current && !dropdownRef.current.contains(e.target as Node)) {
        setIsOpen(false);
      }
    };
    document.addEventListener('mousedown', handleClickOutside);
    return () => document.removeEventListener('mousedown', handleClickOutside);
  }, []);

  const handleSelect = (result: SearchResult) => {
    setQuery('');
    setIsOpen(false);
    if (result.type === 'agent') {
      router.push(`/agents/${result.id}`);
    } else {
      router.push(`/evaluations/${result.id}`);
    }
  };

  return (
    <header className={styles.header}>
      <div className={styles.search} ref={dropdownRef} style={{ position: 'relative' }}>
        <Search size={18} className={styles.searchIcon} />
        <input 
          ref={inputRef}
          type="search"
          aria-label="Search agents and evaluations"
          placeholder="Search agents, evaluations, or runs..." 
          className={styles.searchInput}
          value={query}
          onChange={(e) => { setQuery(e.target.value); setIsOpen(true); }}
          onFocus={() => setIsOpen(true)}
        />
        {query && (
          <button 
            type="button"
            aria-label="Clear search"
            onClick={() => { setQuery(''); setIsOpen(false); }}
            style={{ position: 'absolute', right: '12px', top: '50%', transform: 'translateY(-50%)', background: 'none', border: 'none', cursor: 'pointer', color: 'var(--text-tertiary)', padding: '4px' }}
          >
            <X size={14} />
          </button>
        )}
        
        {isOpen && results.length > 0 && (
          <div style={{
            position: 'absolute',
            top: '100%',
            left: 0,
            right: 0,
            marginTop: '4px',
            backgroundColor: 'var(--bg-card)',
            border: '1px solid var(--border-color)',
            borderRadius: '8px',
            boxShadow: '0 8px 32px rgba(0,0,0,0.3)',
            zIndex: 100,
            overflow: 'hidden',
          }}>
            {results.map((result) => (
              <button
                key={`${result.type}-${result.id}`}
                onClick={() => handleSelect(result)}
                style={{
                  display: 'flex',
                  alignItems: 'center',
                  gap: '12px',
                  width: '100%',
                  padding: '10px 16px',
                  background: 'none',
                  border: 'none',
                  borderBottom: '1px solid var(--border-color)',
                  cursor: 'pointer',
                  textAlign: 'left',
                  color: 'var(--text-primary)',
                  fontSize: '0.875rem',
                }}
              >
                <span style={{ 
                  fontSize: '0.65rem', 
                  padding: '2px 6px', 
                  borderRadius: '4px',
                  backgroundColor: result.type === 'agent' ? 'var(--primary-light)' : 'rgba(16, 185, 129, 0.1)',
                  color: result.type === 'agent' ? 'var(--primary-color)' : 'var(--success-color)',
                  fontWeight: 600,
                  textTransform: 'uppercase',
                }}>
                  {result.type}
                </span>
                <span style={{ flex: 1 }}>{result.name}</span>
                {result.status && (
                  <span style={{ fontSize: '0.75rem', color: 'var(--text-tertiary)', textTransform: 'capitalize' }}>
                    {result.status}
                  </span>
                )}
              </button>
            ))}
          </div>
        )}
        
        {isOpen && query.trim().length >= 2 && results.length === 0 && (
          <div style={{
            position: 'absolute',
            top: '100%',
            left: 0,
            right: 0,
            marginTop: '4px',
            backgroundColor: 'var(--bg-card)',
            border: '1px solid var(--border-color)',
            borderRadius: '8px',
            padding: '16px',
            textAlign: 'center',
            color: 'var(--text-tertiary)',
            fontSize: '0.875rem',
            zIndex: 100,
          }}>
            No results found for &ldquo;{query}&rdquo;
          </div>
        )}
      </div>
      
      <div className={styles.actions}>
        <button type="button" className={styles.iconButton} aria-label="Notifications">
          <Bell size={20} />
        </button>
        <div className={styles.avatar}>
          <User size={20} />
        </div>
      </div>
    </header>
  );
}
