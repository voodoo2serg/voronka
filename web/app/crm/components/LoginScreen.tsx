'use client';

import { useEffect, useState } from 'react';
import { ACCESS_REQUEST } from '../portals';
import type { ApiFn } from '../types';

const IDP_MAIL_COPY = 'Отправили письмо! Проверьте почту!';

const ACCESS_HINTS: Record<string, string> = {
  need_staff: 'SSO прошёл, но роль CRM ещё не выдана. Запросите доступ — администратор привяжет staff.',
  need_link: 'Аккаунт SIGNAL есть, привязка к CRM отсутствует. Запросите staff-доступ.',
  denied: 'Доступ к CRM не разрешён. Запросите выдачу в CMS или SIGNAL.',
};

function oidcLoginHref(): string {
  const params = new URLSearchParams(window.location.search);
  let href = '/api/crm/oidc/login?return_to=' + encodeURIComponent('/crm/');
  if (params.get('register') === '1') {
    href = '/api/crm/oidc/login?register=1&return_to=' + encodeURIComponent('/crm/');
  } else if (params.get('recover') === '1') {
    href = '/api/crm/oidc/login?recover=1&return_to=' + encodeURIComponent('/crm/');
  }
  return href;
}

/**
 * CRM-R1: no auth island — unauthenticated staff go straight to Keycloak.
 * Local/emergency form only with ?breakglass=1 (ops). No intermediate login flash.
 */
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
  const [breakGlass, setBreakGlass] = useState(false);
  const [accessReason, setAccessReason] = useState('');
  const [bootstrapped, setBootstrapped] = useState(false);

  useEffect(() => {
    if (typeof window === 'undefined') return;
    const params = new URLSearchParams(window.location.search);
    const codeKey = params.get('crm_access') || '';
    if (codeKey && ACCESS_HINTS[codeKey]) {
      setAccessReason(codeKey);
      params.delete('crm_access');
      const next = params.toString();
      const url = window.location.pathname + (next ? `?${next}` : '') + window.location.hash;
      window.history.replaceState({}, '', url);
    }
    // CRM-R1: break-glass only via explicit query — no brandmark gesture / ?local=.
    if (params.get('breakglass') === '1') {
      setBreakGlass(true);
    }
    setBootstrapped(true);
  }, []);

  useEffect(() => {
    if (!bootstrapped || typeof window === 'undefined') return;
    if (accessReason && ACCESS_HINTS[accessReason]) return;
    if (breakGlass) return;
    // Remember hash (#overview / #today) — OIDC return_to is path-only (/crm/).
    try {
      const hash = window.location.hash || '';
      if (hash && hash !== '#') sessionStorage.setItem('crm-return-hash', hash);
    } catch {}
    window.location.replace(oidcLoginHref());
  }, [bootstrapped, breakGlass, accessReason]);

  const noAccess = Boolean(accessReason && ACCESS_HINTS[accessReason]);

  if (noAccess) {
    return (
      <main className="login-screen">
        <section className="login-card access-denied-card" aria-labelledby="crm-no-access-title">
          <div className="brandmark" role="presentation">
            С
          </div>
          <p className="eyebrow">СОЛО-PR / CRM</p>
          <h1 id="crm-no-access-title">Нет доступа</h1>
          <p className="muted" role="status">
            {ACCESS_HINTS[accessReason]}
          </p>
          <a className="primary full access-cta" href={ACCESS_REQUEST.signal} target="_blank" rel="noreferrer">
            {ACCESS_REQUEST.label}
          </a>
          <p className="fine">
            Доступ staff запрашивается через Signal. CMS (cms.solo-pr.ru) — сайт платёжных форм, не
            конструктор цепочек и не медиатека.
          </p>
          <a className="link" href="/api/crm/oidc/login">
            Войти
          </a>
        </section>
      </main>
    );
  }

  if (!bootstrapped || !breakGlass) {
    // CRM-R1: blank while navigating to Keycloak — no login-card flash.
    return <main className="loading" aria-busy="true" />;
  }

  return (
    <main className="login-screen">
      <section className="login-card" aria-labelledby="crm-login-title">
        <div className="brandmark" role="presentation">
          С
        </div>
        <p className="eyebrow">СОЛО-PR / CRM</p>
        <h1 id="crm-login-title">{recover ? 'Восстановление' : 'Аварийный вход'}</h1>
        <form
          onSubmit={(e) => {
            e.preventDefault();
            void onBusy(async () => {
              if (recover) {
                if (code) {
                  await api('/recovery/finish', 'POST', { login, purpose, code, password });
                  onNotice(IDP_MAIL_COPY);
                  setRecover(false);
                  setCode('');
                } else {
                  await api('/recovery/start', 'POST', { login, purpose });
                  onNotice(IDP_MAIL_COPY);
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
                <input
                  inputMode="numeric"
                  value={code}
                  onChange={(e) => setCode(e.target.value)}
                  placeholder="Для получения кода оставьте пустым"
                />
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
            {recover ? (code ? 'Подтвердить' : 'Получить код') : 'Аварийный вход'}
          </button>
        </form>
        <button
          className="link"
          type="button"
          onClick={() => {
            setRecover(!recover);
            onError('');
            onNotice('');
          }}
        >
          {recover ? 'Вернуться ко входу' : 'Забыли пароль?'}
        </button>
        <a className="link" href="/api/crm/oidc/login?return_to=%2Fcrm%2F">
          Войти через Keycloak
        </a>
        {error && <p role="alert" className="error">{error}</p>}
        {notice && (
          <p role="status" className="notice">
            {notice}
          </p>
        )}
      </section>
    </main>
  );
}
