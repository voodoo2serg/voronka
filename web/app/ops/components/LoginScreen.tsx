'use client';

import { useEffect, useState } from 'react';
import type { ApiFn } from '../types';
import { PORTAL_URLS } from '../portals';

/**
 * OIDC authorize + state cookie must start on the CRM callback host
 * (crm.solo-pr.ru), not voronka.solo-pr.ru — host-only cookie otherwise 401s.
 */
const CRM_OIDC_LOGIN =
  PORTAL_URLS.crm + '/api/crm/oidc/login?return_to=' + encodeURIComponent('/ops/');
const CRM_OIDC_RECOVER =
  PORTAL_URLS.crm +
  '/api/crm/oidc/login?recover=1&return_to=' +
  encodeURIComponent('/ops/');
const IDP_MAIL_COPY = 'Отправили вам письмо! Проверьте вашу почту!';

function opsRecoveryHref(recoveryPath: string): string {
  const absolute = recoveryPath.startsWith('http')
    ? recoveryPath
    : PORTAL_URLS.crm + recoveryPath;
  try {
    const url = new URL(absolute);
    url.searchParams.set('recover', '1');
    url.searchParams.set('return_to', '/ops/');
    return url.toString();
  } catch {
    return CRM_OIDC_RECOVER;
  }
}

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
  const [ssoEnabled, setSsoEnabled] = useState(false);
  const [cutover, setCutover] = useState(false);
  const [showLocal, setShowLocal] = useState(false);
  const [recoveryPath, setRecoveryPath] = useState(CRM_OIDC_RECOVER);

  useEffect(() => {
    let cancelled = false;
    // Same-origin status (shared backend); login/recover navigate to CRM origin.
    fetch('/api/crm/oidc/status', { credentials: 'same-origin' })
      .then((response) => (response.ok ? response.json() : { enabled: false }))
      .then((result: { enabled?: boolean; cutover?: boolean; recovery_path?: string | null }) => {
        if (cancelled) return;
        const enabled = result.enabled === true;
        const nextCutover = result.cutover === true;
        setSsoEnabled(enabled);
        setCutover(nextCutover);
        setShowLocal(!enabled || !nextCutover);
        if (result.recovery_path) {
          // Absolute CRM host so PKCE state cookie is set on the callback origin.
          // Keep return_to=/ops/ so post-reset lands back in ops, not CRM.
          setRecoveryPath(opsRecoveryHref(result.recovery_path));
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

  return (
    <main className="login-screen">
      <section className="login-card">
        <div className="brandmark">В</div>
        <p className="eyebrow">СОЛО-PR / VORONKA</p>
        <h1>Вход в ops</h1>
        <p className="muted">Боты · Сценарии · Рассылки · Поддержка · AI. Клиенты и платежи — в CRM.</p>
        {ssoEnabled && (
          <div>
            <a className="primary full" href={CRM_OIDC_LOGIN}>
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
            Аварийный вход
          </button>
        )}
        {showLocal && (
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
              {cutover ? 'Аварийный вход в ops' : 'Войти в ops'}
            </button>
          </form>
        )}
        {error && <p role="alert" className="error">{error}</p>}
        {notice && <p role="status" className="notice">{notice}</p>}
        <p className="fine">Менеджеры продаж работают в CRM; ops — owner/editor.</p>
      </section>
    </main>
  );
}
