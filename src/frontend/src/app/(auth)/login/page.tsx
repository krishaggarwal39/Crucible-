'use client';

import { useState } from 'react';
import { useForm } from 'react-hook-form';
import { zodResolver } from '@hookform/resolvers/zod';
import * as z from 'zod';
import { useAuth } from '@/contexts/AuthContext';
import { api } from '@/lib/api';
import styles from './login.module.css';
import Link from 'next/link';
import { Loader2 } from 'lucide-react';
import { AxiosError } from 'axios';

const loginSchema = z.object({
  email: z.string().email('Please enter a valid email address'),
  password: z.string().min(1, 'Password is required'),
});

type LoginFormValues = z.infer<typeof loginSchema>;

export default function LoginPage() {
  const { login } = useAuth();
  const [globalError, setGlobalError] = useState<string | null>(null);

  const {
    register,
    handleSubmit,
    formState: { errors, isSubmitting },
  } = useForm<LoginFormValues>({
    resolver: zodResolver(loginSchema),
  });

  const onSubmit = async (data: LoginFormValues) => {
    setGlobalError(null);
    try {
      // Create FormData as FastAPI OAuth2PasswordRequestForm expects x-www-form-urlencoded
      const formData = new URLSearchParams();
      formData.append('username', data.email);
      formData.append('password', data.password);

      const response = await api.post('/api/v1/auth/login', formData, {
        headers: {
          'Content-Type': 'application/x-www-form-urlencoded',
        },
      });

      // The refresh token is set as HttpOnly cookie automatically by the backend.
      // We manually pass the access_token and user info to the AuthContext.
      // We need to fetch the user info first since /login only returns tokens.
      
      const userRes = await api.get('/api/v1/auth/me', {
        headers: {
          Authorization: `Bearer ${response.data.access_token}`
        }
      });
      
      login(response.data.access_token, userRes.data);
    } catch (err) {
      if (err instanceof AxiosError && err.response?.status === 400) {
        setGlobalError('Invalid email or password');
      } else {
        setGlobalError('An error occurred. Please try again later.');
      }
    }
  };

  return (
    <div className={styles.container}>
      <div className={`glass animate-fade-in ${styles.card}`}>
        <h1 className={styles.title}>Welcome back</h1>
        <p className={styles.subtitle}>Log in to your Crucible account</p>

        {globalError && (
          <div className={styles.alert}>
            {globalError}
          </div>
        )}

        <form className={styles.form} onSubmit={handleSubmit(onSubmit)}>
          <div className={styles.field}>
            <label className={styles.label} htmlFor="email">Email</label>
            <input
              id="email"
              type="email"
              autoComplete="email"
              placeholder="you@example.com"
              className={`${styles.input} ${errors.email ? styles.inputError : ''}`}
              {...register('email')}
            />
            {errors.email && <span className={styles.errorText}>{errors.email.message}</span>}
          </div>

          <div className={styles.field}>
            <label className={styles.label} htmlFor="password">Password</label>
            <input
              id="password"
              type="password"
              autoComplete="current-password"
              placeholder="••••••••"
              className={`${styles.input} ${errors.password ? styles.inputError : ''}`}
              {...register('password')}
            />
            {errors.password && <span className={styles.errorText}>{errors.password.message}</span>}
          </div>

          <button type="submit" disabled={isSubmitting} className={styles.submitBtn}>
            {isSubmitting ? <Loader2 className="animate-spin" size={18} /> : 'Sign In'}
          </button>
        </form>

        <div className={styles.footer}>
          Don&apos;t have an account? <Link href="/register" className={styles.link}>Sign up</Link>
        </div>
      </div>
    </div>
  );
}
