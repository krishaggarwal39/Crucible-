'use client';

import { useState } from 'react';
import { useForm } from 'react-hook-form';
import { zodResolver } from '@hookform/resolvers/zod';
import * as z from 'zod';
import { api } from '@/lib/api';
import { useRouter } from 'next/navigation';
import styles from './register.module.css';
import Link from 'next/link';
import { Loader2 } from 'lucide-react';
import { AxiosError } from 'axios';

const registerSchema = z.object({
  email: z.string().email('Please enter a valid email address'),
  password: z.string().min(8, 'Password must be at least 8 characters'),
  name: z.string().min(2, 'Name must be at least 2 characters'),
  company_name: z.string().min(2, 'Company name is required'),
});

type RegisterFormValues = z.infer<typeof registerSchema>;

export default function RegisterPage() {
  const router = useRouter();
  const [globalError, setGlobalError] = useState<string | null>(null);

  const {
    register,
    handleSubmit,
    formState: { errors, isSubmitting },
  } = useForm<RegisterFormValues>({
    resolver: zodResolver(registerSchema),
  });

  const onSubmit = async (data: RegisterFormValues) => {
    setGlobalError(null);
    try {
      await api.post('/api/v1/auth/register', data);
      
      // Successfully registered, redirect to login
      router.push('/login');
    } catch (err) {
      if (err instanceof AxiosError && err.response?.status === 400) {
        setGlobalError(err.response.data.detail || 'Email already registered');
      } else {
        setGlobalError('An error occurred during registration. Please try again.');
      }
    }
  };

  return (
    <div className={styles.container}>
      <div className={`glass animate-fade-in ${styles.card}`}>
        <h1 className={styles.title}>Create an account</h1>
        <p className={styles.subtitle}>Start evaluating your AI agents</p>

        {globalError && (
          <div className={styles.alert}>
            {globalError}
          </div>
        )}

        <form className={styles.form} onSubmit={handleSubmit(onSubmit)}>
          <div className={styles.field}>
            <label className={styles.label} htmlFor="name">Full Name</label>
            <input
              id="name"
              type="text"
              placeholder="Ada Lovelace"
              className={`${styles.input} ${errors.name ? styles.inputError : ''}`}
              {...register('name')}
            />
            {errors.name && <span className={styles.errorText}>{errors.name.message}</span>}
          </div>

          <div className={styles.field}>
            <label className={styles.label} htmlFor="company_name">Company Name</label>
            <input
              id="company_name"
              type="text"
              placeholder="Acme Corp"
              className={`${styles.input} ${errors.company_name ? styles.inputError : ''}`}
              {...register('company_name')}
            />
            {errors.company_name && <span className={styles.errorText}>{errors.company_name.message}</span>}
          </div>

          <div className={styles.field}>
            <label className={styles.label} htmlFor="email">Work Email</label>
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
              autoComplete="new-password"
              placeholder="••••••••"
              className={`${styles.input} ${errors.password ? styles.inputError : ''}`}
              {...register('password')}
            />
            {errors.password && <span className={styles.errorText}>{errors.password.message}</span>}
          </div>

          <button type="submit" disabled={isSubmitting} className={styles.submitBtn}>
            {isSubmitting ? <Loader2 className="animate-spin" size={18} /> : 'Create Account'}
          </button>
        </form>

        <div className={styles.footer}>
          Already have an account? <Link href="/login" className={styles.link}>Sign in</Link>
        </div>
      </div>
    </div>
  );
}
