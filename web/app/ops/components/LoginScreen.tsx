'use client';

import { useState } from 'react';
import type { ApiFn } from '../types';

export function LoginScreen({
  api,
  busy,
  error,
  notice,
  onBusy,
  onError,
  onNotice,
  onToken,
}: {
  api: ApiFn;
  busy: boolean;
  error: string;
  notice: string;
  onBusy: (fn: () => Promise<void>) => Promise<void>;
  onError: (v: string) => void;
  onNotice: (v: string) => void;
  onToken: (token: string) => void;
}) {
  const [login, setLogin] = useState('');
  const [password, setPassword] = useState('');

  return (
    <main className="login-screen">
      <section className="login-card">
        <div className="brandmark">В</div>
        <p className="eyebrow">СОЛО-PR / VORONKA</p>
        <h1>Вход в ops</h1>
        <p className="muted">Боты · Сценарии · Рассылки · Поддержка · AI. Клиенты и платежи — в CRM.</p>
        <form
          onSubmit={(e) => {
            e.preventDefault();
            void onBusy(async () => {
              // Stub login reuses staff Account (same as CRM); zone = voronka UI.
              const r = await api('/api/crm/login', 'POST', { login, password });
              onToken(r.token);
            });
          }}
        >
          <label>
            Логин
            <input autoComplete="username" required value={login} onChange={(e) => setLogin(e.target.value)} />
          </label>
          <label>
            Пароль
            <input
              type="password"
              autoComplete="current-password"
              required
              value={password}
              onChange={(e) => setPassword(e.target.value)}
            />
          </label>
          <button className="primary full" disabled={busy}>
            Войти в ops
          </button>
        </form>
        {error && <p role="alert" className="error">{error}</p>}
        {notice && <p role="status" className="notice">{notice}</p>}
        <p className="fine">Менеджеры продаж работают в CRM; ops — owner/editor.</p>
      </section>
    </main>
  );
}
