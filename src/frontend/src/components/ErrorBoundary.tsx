'use client';

import React from 'react';
import { AlertTriangle } from 'lucide-react';

/**
 * Top-level error boundary.
 *
 * Without one, any render-time throw in a client component blanked the entire
 * page with no explanation.
 */

type Props = { children: React.ReactNode };
type State = { error: Error | null };

export default class ErrorBoundary extends React.Component<Props, State> {
  state: State = { error: null };

  static getDerivedStateFromError(error: Error): State {
    return { error };
  }

  componentDidCatch(error: Error, info: React.ErrorInfo) {
    console.error('Unhandled UI error:', error, info.componentStack);
  }

  handleReset = () => {
    this.setState({ error: null });
  };

  render() {
    if (this.state.error) {
      return (
        <div
          role="alert"
          className="glass"
          style={{
            margin: '40px auto',
            maxWidth: '640px',
            padding: '32px',
            borderRadius: '12px',
            textAlign: 'center',
          }}
        >
          <AlertTriangle
            size={40}
            style={{ color: 'var(--danger-color)', margin: '0 auto 16px' }}
          />
          <h2 style={{ fontSize: '1.125rem', fontWeight: 600, marginBottom: '8px' }}>
            Something went wrong
          </h2>
          <p style={{ color: 'var(--text-secondary)', fontSize: '0.9rem', marginBottom: '24px' }}>
            This part of the dashboard failed to render. The rest of the app is unaffected.
          </p>
          <button
            type="button"
            onClick={this.handleReset}
            style={{
              padding: '10px 20px',
              borderRadius: 'var(--radius-md)',
              border: 'none',
              background: 'var(--primary-color)',
              color: 'white',
              fontWeight: 500,
              cursor: 'pointer',
            }}
          >
            Try again
          </button>
        </div>
      );
    }

    return this.props.children;
  }
}
