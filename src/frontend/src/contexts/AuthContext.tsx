'use client';

import React, { createContext, useContext, useState, useEffect } from 'react';
import { api, setAccessToken } from '@/lib/api';
import { useRouter, usePathname } from 'next/navigation';
import { useQueryClient } from '@tanstack/react-query';

type User = {
  id: string;
  email: string;
  full_name: string | null;
  tenant_id: string;
  role: 'admin' | 'member';
};

type AuthContextType = {
  user: User | null;
  isLoading: boolean;
  login: (token: string, userData: User) => void;
  logout: () => void;
};

const AuthContext = createContext<AuthContextType | undefined>(undefined);

export function AuthProvider({ children }: { children: React.ReactNode }) {
  const [user, setUser] = useState<User | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const router = useRouter();
  const queryClient = useQueryClient();

  useEffect(() => {
    const checkAuth = async () => {
      try {
        const res = await api.get<{id: string; email: string; full_name: string | null; tenant_id: string; role: 'admin' | 'member'}>('/api/v1/auth/me');
        setUser(res.data);
      } catch (err) {
        setUser(null);
      } finally {
        setIsLoading(false);
      }
    };

    checkAuth();

    const handleAuthExpired = () => {
      setUser(null);
      queryClient.clear();
      router.push('/login');
    };

    window.addEventListener('auth-expired', handleAuthExpired);
    return () => window.removeEventListener('auth-expired', handleAuthExpired);
  }, [router, queryClient]);

  const login = (token: string, userData: User) => {
    setAccessToken(token);
    setUser(userData);
    router.push('/');
  };

  const logout = async () => {
    try {
      await api.post('/api/v1/auth/logout');
    } catch (e) {
      console.error('Logout failed', e);
    } finally {
      setAccessToken(null);
      setUser(null);
      queryClient.clear();
      router.push('/login');
    }
  };

  return (
    <AuthContext.Provider value={{ user, isLoading, login, logout }}>
      {children}
    </AuthContext.Provider>
  );
}

export function useAuth() {
  const context = useContext(AuthContext);
  if (context === undefined) {
    throw new Error('useAuth must be used within an AuthProvider');
  }
  return context;
}
