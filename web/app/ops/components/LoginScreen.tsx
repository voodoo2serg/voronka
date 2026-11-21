'use client';

import { useEffect, useRef, useState } from 'react';
import type { ApiFn } from '../types';
import { ACCESS_REQUEST, PORTAL_URLS } from '../portals';

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
/** Cutover: no peer «Аварийный вход» — break-glass only via hidden brandmark gesture. */
const BREAK_GLASS_CLICKS = 5;
const BREAK_GLASS_WINDOW_MS = 2800;

const ACCESS_HINTS: Record<string, string> = {
  need_staff: 'SSO прошёл, но роль ops/CRM ещё не выдана. Запросите доступ.',
  need_link: 'Аккаунт SIGNAL есть, привязка к Voronka отсутствует. Запросите staff-доступ.',
  denied: 'Доступ к ops не разрешён. Запросите выдачу в CMS или SIGNAL.',
};

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
  const [accessReason, setAccessReason] = useState('');
  const glassClicks = useRef<{ n: number; t: number }>({ n: 0, t: 0 });

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
  }, []);

  function onBrandmarkGesture() {
    if (!(cutover && ssoEnabled) || showLocal) return;
    const now = Date.now();
    if (now - glassClicks.current.t > BREAK_GLASS_WINDOW_MS) glassClicks.current = { n: 0, t: now };
    glassClicks.current.n += 1;
    glassClicks.current.t = now;
    if (glassClicks.current.n >= BREAK_GLASS_CLICKS) {
      glassClicks.current = { n: 0, t: 0 };
      setShowLocal(true);
    }
  }

  const noAccess = Boolean(accessReason && ACCESS_HINTS[accessReason]);

  if (noAccess) {
    return (
      <main className="login-screen">
        <section className="login-card access-denied-card" aria-labelledby="ops-no-access-title">
          <div className="brandmark" role="presentation">
            В
          </div>
          <p className="eyebrow">СОЛО-PR / VORONKA</p>
          <h1 id="ops-no-access-title">Нет доступа</h1>
          <p className="muted" role="status">
            {ACCESS_HINTS[accessReason]}
          </p>
          <a className="primary full access-cta" href={ACCESS_REQUEST.signal} target="_blank" rel="noreferrer">
            {ACCESS_REQUEST.label}
          </a>
          <p className="fine">
            Администратор выдаёт staff в{' '}
            <a href={ACCESS_REQUEST.cmsStaff} target="_blank" rel="noreferrer">
              CMS → Администраторы
            </a>
            . Ops — только owner/editor.
          </p>
          <button
            type="button"
            className="link"
            onClick={() => {
              setAccessReason('');
              onError('');
              onNotice('');
            }}
          >
            Вернуться ко входу
          </button>
          <a className="link" href={CRM_OIDC_LOGIN}>
            Повторить вход через SIGNAL
          </a>
        </section>
      </main>
    );
  }

  return (
    <main className="login-screen">
      <section className="login-card">
        <div
          className="brandmark"
          onClick={onBrandmarkGesture}
          onKeyDown={(e) => {
            if (e.key === 'Enter' || e.key === ' ') onBrandmarkGesture();
          }}
          role="presentation"
        >
          В
        </div>
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
                ? 'Штатный вход — через единый аккаунт SIGNAL (Keycloak).'
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
        {notice && <p role="status" className="notice banner">{notice}</p>}
        <div className="access-request fine">
          <p>Нет доступа в ops?</p>
          <a className="primary full access-cta" href={ACCESS_REQUEST.signal} target="_blank" rel="noreferrer">
            {ACCESS_REQUEST.label}
          </a>
          <p className="fine">Менеджеры продаж работают в CRM; ops — owner/editor.</p>
        </div>
      </section>
    </main>
  );
}
