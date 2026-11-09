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
  const [recover, setRecover] = useState(false);
  const [code, setCode] = useState('');
  const [purpose, setPurpose] = useState('reset');

  return (
    <main className="login-screen">
      <section className="login-card">
        <div className="brandmark">С</div>
        <p className="eyebrow">СОЛО-PR / РАБОЧИЙ КАБИНЕТ</p>
        <h1>{recover ? 'Доступ к аккаунту' : 'Добро пожаловать'}</h1>
        <p className="muted">Клиенты, экспертиза и результаты — в одном месте.</p>
        <form
          onSubmit={(e) => {
            e.preventDefault();
            void onBusy(async () => {
              if (recover) {
                if (code) {
                  await api('/recovery/finish', 'POST', { login, purpose, code, password });
                  onNotice('Операция выполнена. Можно войти.');
                  setRecover(false);
                  setCode('');
                } else {
                  await api('/recovery/start', 'POST', { login, purpose });
                  onNotice('Если аккаунт подходит для операции, код отправлен.');
                }
              } else {
                const r = await api('/login', 'POST', { login, password });
                sessionStorage.removeItem('crm-token');
                onToken(r.token);
              }
            });
          }}
        >
          <label>
            Логин
            <input autoComplete="username" required value={login} onChange={(e) => setLogin(e.target.value)} />
          </label>
          {recover && (
            <>
              <label>
                Операция
                <select value={purpose} onChange={(e) => setPurpose(e.target.value)}>
                  <option value="reset">Восстановить пароль</option>
                  <option value="verify">Подтвердить email</option>
                </select>
              </label>
              <label>
                Код из письма
                <input inputMode="numeric" value={code} onChange={(e) => setCode(e.target.value)} placeholder="Для получения кода оставьте пустым" />
              </label>
            </>
          )}
          {(!recover || (purpose === 'reset' && code)) && (
            <label>
              {recover ? 'Новый пароль' : 'Пароль'}
              <input
                type="password"
                autoComplete={recover ? 'new-password' : 'current-password'}
                required
                value={password}
                onChange={(e) => setPassword(e.target.value)}
              />
            </label>
          )}
          <button className="primary full" disabled={busy}>
            {recover ? (code ? 'Подтвердить' : 'Получить код') : 'Войти в кабинет'}
          </button>
        </form>
        <button
          className="link"
          onClick={() => {
            setRecover(!recover);
            onError('');
            onNotice('');
          }}
        >
          {recover ? 'Вернуться ко входу' : 'Забыли пароль или нужно подтвердить почту?'}
        </button>
        {error && <p role="alert" className="error">{error}</p>}
        {notice && <p role="status" className="notice">{notice}</p>}
        <p className="fine">Нет аккаунта? Обратитесь к владельцу проекта.</p>
      </section>
    </main>
  );
}
