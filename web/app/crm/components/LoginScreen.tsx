'use client';

import { useEffect, useState } from 'react';
import type { ApiFn } from '../types';

const IDP_MAIL_COPY = 'Отправили вам письмо! Проверьте вашу почту!';

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
  const [ssoEnabled, setSsoEnabled] = useState(false);
  const [cutover, setCutover] = useState(false);
  const [showLocal, setShowLocal] = useState(false);
  const [recoveryPath, setRecoveryPath] = useState('/api/crm/oidc/login?recover=1');

  useEffect(() => {
    let cancelled = false;
    fetch('/api/crm/oidc/status', { credentials: 'same-origin' })
      .then((response) => (response.ok ? response.json() : { enabled: false }))
      .then((result: { enabled?: boolean; cutover?: boolean; recovery_path?: string | null }) => {
        if (cancelled) return;
        const enabled = result.enabled === true;
        const nextCutover = result.cutover === true;
        setSsoEnabled(enabled);
        setCutover(nextCutover);
        setShowLocal(!enabled || !nextCutover);
        if (result.recovery_path) setRecoveryPath(result.recovery_path);
        if (enabled) {
          setRecover(false);
          setCode('');
        }
      })
      .catch(() => {
        if (!cancelled) {
          setSsoEnabled(false);
          setCutover(false);
          setShowLocal(true);
        }
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const localRecover = !ssoEnabled && recover;

  return (
    <main className="login-screen">
      <section className="login-card">
        <div className="brandmark">С</div>
        <p className="eyebrow">СОЛО-PR / CRM</p>
        <h1>{localRecover ? 'Доступ к аккаунту' : 'Вход в CRM'}</h1>
        <p className="muted">Клиенты, сделки и платежи. Обучение — в LMS, боты — в Voronka.</p>
        {ssoEnabled && !localRecover && (
          <div>
            <a className="primary full" href="/api/crm/oidc/login">
              Войти через единый аккаунт SIGNAL
            </a>
            <p className="fine">
              {cutover
                ? 'Штатный вход — через Keycloak. Локальный пароль только для аварии.'
                : 'Если учётная запись ещё не привязана, используйте прежний вход.'}
            </p>
            <a className="link" href={recoveryPath}>
              Забыли пароль?
            </a>
            <p className="fine">
              Восстановление только через единый аккаунт SIGNAL. После запроса:{' '}
              «{IDP_MAIL_COPY}»
            </p>
          </div>
        )}
        {cutover && ssoEnabled && !showLocal && (
          <button className="link" type="button" onClick={() => setShowLocal(true)}>
            Аварийный вход по локальному паролю
          </button>
        )}
        {(showLocal || localRecover) && (
          <form
            onSubmit={(e) => {
              e.preventDefault();
              void onBusy(async () => {
                if (localRecover) {
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
            {localRecover && (
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
            {(!localRecover || (purpose === 'reset' && code)) && (
              <label>
                {localRecover ? 'Новый пароль' : 'Пароль'}
                <input
                  type="password"
                  autoComplete={localRecover ? 'new-password' : 'current-password'}
                  required
                  value={password}
                  onChange={(e) => setPassword(e.target.value)}
                />
              </label>
            )}
            <button className="primary full" disabled={busy}>
              {localRecover
                ? code
                  ? 'Подтвердить'
                  : 'Получить код'
                : cutover
                  ? 'Аварийный вход'
                  : 'Войти в кабинет'}
            </button>
          </form>
        )}
        {!ssoEnabled && (showLocal || recover) && (
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
        )}
        {error && <p role="alert" className="error">{error}</p>}
        {notice && <p role="status" className="notice">{notice}</p>}
        <p className="fine">Нет аккаунта? Обратитесь к владельцу проекта.</p>
      </section>
    </main>
  );
}
