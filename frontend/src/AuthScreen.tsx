import { useState } from 'react';
import type { FormEvent } from 'react';
import { request } from './api';
import type { Session } from './api';

export default function AuthScreen({
  onAuthenticated,
  notice,
}: {
  onAuthenticated: (session: Session) => void;
  notice: string;
}) {
  const [mode, setMode] = useState<'login' | 'register'>('login');
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');

  async function submit(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError('');
    try {
      const session = await request<Session>(`/auth/${mode}`, {
        method: 'POST',
        body: JSON.stringify({ username, password }),
      });
      setPassword('');
      onAuthenticated(session);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : 'Unable to sign in. Please try again.');
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="auth-page">
      <section className="auth-intro">
        <div className="brand">
          <span className="brand-mark" aria-hidden="true">
            <i />
            <i />
            <i />
          </span>
          <span>
            Auction<span className="brand-light">Lab</span>
            <small>HAN2601 / RESEARCH SIMULATOR</small>
          </span>
        </div>
        <div>
          <span className="eyebrow">YOUR AUCTION WORKSPACE</span>
          <h1>
            Follow your strategy.
            <br />
            Keep your bids private.
          </h1>
          <p>
            Watch scripted bidders compete in reproducible auctions. Your account gives you access
            to your assigned bidders and their results.
          </p>
        </div>
        <small>Generic Auction Simulator · Final Year Project</small>
      </section>
      <main className="auth-main">
        <form className="auth-card panel" onSubmit={submit}>
          <span className="eyebrow">
            {mode === 'login' ? 'WELCOME BACK' : 'JOIN THE EXPERIMENT'}
          </span>
          <h2>{mode === 'login' ? 'Sign in to Auction Lab' : 'Create your account'}</h2>
          <p className="auth-description">
            {mode === 'login'
              ? 'Use your account to access the experiment workspace.'
              : 'Your administrator can assign a scripted bidder to your account.'}
          </p>
          {(error || notice) && (
            <div className="error-banner" role="alert">
              {error || notice}
            </div>
          )}
          <label htmlFor="username">
            Username
            <input
              id="username"
              value={username}
              onChange={(e) => setUsername(e.target.value)}
              autoComplete="username"
              autoCapitalize="none"
              spellCheck={false}
              required
              minLength={3}
              maxLength={32}
              pattern="[A-Za-z0-9_-]+"
              title="3–32 letters, digits, underscores or hyphens"
              disabled={busy}
            />
          </label>
          <label htmlFor="password">
            Password
            <input
              id="password"
              type="password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              autoComplete={mode === 'login' ? 'current-password' : 'new-password'}
              required
              minLength={mode === 'register' ? 10 : 1}
              maxLength={128}
              disabled={busy}
            />
          </label>
          {mode === 'register' && (
            <p className="field-note">
              Use at least 10 characters. New accounts are regular users; administrator access is
              configured separately.
            </p>
          )}
          <button className="button primary" type="submit" disabled={busy}>
            {busy ? 'Please wait…' : mode === 'login' ? 'Sign in' : 'Create account'}
          </button>
          <p className="auth-switch">
            {mode === 'login' ? 'New here?' : 'Already registered?'}{' '}
            <button
              type="button"
              className="text-button"
              disabled={busy}
              onClick={() => {
                setMode(mode === 'login' ? 'register' : 'login');
                setError('');
                setPassword('');
              }}
            >
              {mode === 'login' ? 'Create an account' : 'Sign in instead'}
            </button>
          </p>
        </form>
      </main>
    </div>
  );
}
