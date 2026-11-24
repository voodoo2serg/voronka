'use client';

import { useEffect, useRef, useState } from 'react';
import { ACCESS_REQUEST } from '../portals';
import type { ApiFn } from '../types';

const IDP_MAIL_COPY = 'Отправили письмо! Проверьте почту!';
/** Break-glass only via ?breakglass=1 or hidden brandmark gesture — no local auth island. */
const BREAK_GLASS_CLICKS = 5;
const BREAK_GLASS_WINDOW_MS = 2800;

const ACCESS_HINTS: Record<string, string> = {
  need_staff: 'SSO прошёл, но роль CRM ещё не выдана. Запросите доступ — администратор привяжет staff.',
  need_link: 'Аккаунт SIGNAL есть, привязка к CRM отсутствует. Запросите staff-доступ.',
  denied: 'Доступ к CRM не разрешён. Запросите выдачу в CMS или SIGNAL.',
};

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
  const [redirecting, setRedirecting] = useState(false);
  const [statusReady, setStatusReady] = useState(false);
  const [accessReason, setAccessReason] = useState('');
  const glassClicks = useRef<{ n: number; t: number }>({ n: 0, t: 0 });

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
    if (params.get('breakglass') === '1' || params.get('local') === '1') {
      setShowLocal(true);
    }
  }, []);

  useEffect(() => {
    let cancelled = false;
    fetch('/api/crm/oidc/status', { credentials: 'same-origin' })
      .then((response) => (response.ok ? response.json() : { enabled: false }))
      .then(
        (result: {
          enabled?: boolean;
          cutover?: boolean;
        }) => {
          if (cancelled) return;
          const enabled = result.enabled === true;
          const nextCutover = result.cutover === true;
          setSsoEnabled(enabled);
          setCutover(nextCutover);
          setStatusReady(true);
        },
      )
      .catch(() => {
        if (!cancelled) {
          setSsoEnabled(false);
          setCutover(false);
          setShowLocal(true);
          setStatusReady(true);
        }
      });
    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    if (!statusReady || typeof window === 'undefined') return;
    if (accessReason && ACCESS_HINTS[accessReason]) return;
    if (showLocal) return;
    if (!ssoEnabled) {
      setShowLocal(true);
      return;
    }
    // CRM-A1: no local auth island — immediate Keycloak.
    // Remember hash (#overview / #today) — OIDC return_to is path-only (/crm/).
    try {
      const hash = window.location.hash || '';
      if (hash && hash !== '#') sessionStorage.setItem('crm-return-hash', hash);
    } catch {}
    setRedirecting(true);
    const params = new URLSearchParams(window.location.search);
    let href = '/api/crm/oidc/login?return_to=' + encodeURIComponent('/crm/');
    if (params.get('register') === '1') href = '/api/crm/oidc/login?register=1&return_to=' + encodeURIComponent('/crm/');
    else if (params.get('recover') === '1') href = '/api/crm/oidc/login?recover=1&return_to=' + encodeURIComponent('/crm/');
    window.location.replace(href);
  }, [statusReady, ssoEnabled, showLocal, accessReason]);

  function onBrandmarkGesture() {
    if (!ssoEnabled || showLocal) return;
    const now = Date.now();
    if (now - glassClicks.current.t > BREAK_GLASS_WINDOW_MS) glassClicks.current = { n: 0, t: now };
    glassClicks.current.n += 1;
    glassClicks.current.t = now;
    if (glassClicks.current.n >= BREAK_GLASS_CLICKS) {
      glassClicks.current = { n: 0, t: 0 };
      setShowLocal(true);
      setRedirecting(false);
    }
  }

  const localRecover = !ssoEnabled && recover;
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
            Администратор выдаёт staff в{' '}
            <a href={ACCESS_REQUEST.cmsStaff} target="_blank" rel="noreferrer">
              CMS → Администраторы
            </a>{' '}
            (provision CRM + Signal).
          </p>
          <a className="link" href="/api/crm/oidc/login">
            Войти
          </a>
        </section>
      </main>
    );
  }

  if (!statusReady || (ssoEnabled && !showLocal && redirecting)) {
    return (
      <main className="login-screen">
        <section className="login-card" aria-labelledby="crm-login-title">
          <div
            className="brandmark"
            onClick={onBrandmarkGesture}
            onKeyDown={(e) => {
              if (e.key === 'Enter' || e.key === ' ') onBrandmarkGesture();
            }}
            role="presentation"
          >
            С
          </div>
          <p className="eyebrow">СОЛО-PR / CRM</p>
          <h1 id="crm-login-title">Вход</h1>
          <p className="muted" role="status">
            Переход к единому входу…
          </p>
          <a className="link" href="/api/crm/oidc/login">
            Продолжить
          </a>
        </section>
      </main>
    );
  }

  return (
    <main className="login-screen">
      <section className="login-card" aria-labelledby="crm-login-title">
        <div className="brandmark" role="presentation">
          С
        </div>
        <p className="eyebrow">СОЛО-PR / CRM</p>
        <h1 id="crm-login-title">{localRecover ? 'Восстановление' : cutover ? 'Аварийный вход' : 'Вход'}</h1>
        {(showLocal || localRecover) && (
          <form
            onSubmit={(e) => {
              e.preventDefault();
              void onBusy(async () => {
                if (localRecover) {
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
                  <input
                    inputMode="numeric"
                    value={code}
                    onChange={(e) => setCode(e.target.value)}
                    placeholder="Для получения кода оставьте пустым"
                  />
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
              {localRecover ? (code ? 'Подтвердить' : 'Получить код') : cutover ? 'Аварийный вход' : 'Войти'}
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
            {recover ? 'Вернуться ко входу' : 'Забыли пароль?'}
          </button>
        )}
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
