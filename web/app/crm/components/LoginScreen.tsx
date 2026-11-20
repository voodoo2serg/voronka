'use client';

import { useEffect, useRef, useState } from 'react';
import { ACCESS_REQUEST } from '../portals';
import type { ApiFn } from '../types';

const IDP_MAIL_COPY = 'Отправили вам письмо! Проверьте вашу почту!';
/** Cutover: no peer «Аварийный вход» — break-glass only via hidden brandmark gesture. */
const BREAK_GLASS_CLICKS = 5;
const BREAK_GLASS_WINDOW_MS = 2800;

const ACCESS_HINTS: Record<string, string> = {
  need_staff: 'SSO прошёл, но сотрудник CRM ещё не выдан. Запросите доступ ниже.',
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
  const [recoveryPath, setRecoveryPath] = useState('/api/crm/oidc/login?recover=1');
  const [accessHint, setAccessHint] = useState('');
  const glassClicks = useRef<{ n: number; t: number }>({ n: 0, t: 0 });

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
        // Keycloak-only paint when cutover: hide local form until hidden gesture.
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

  useEffect(() => {
    if (typeof window === 'undefined') return;
    const params = new URLSearchParams(window.location.search);
    const codeKey = params.get('crm_access') || '';
    if (codeKey && ACCESS_HINTS[codeKey]) {
      setAccessHint(ACCESS_HINTS[codeKey]);
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

  const localRecover = !ssoEnabled && recover;

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
          С
        </div>
        <p className="eyebrow">СОЛО-PR / CRM</p>
        <h1>{localRecover ? 'Доступ к аккаунту' : 'Вход в CRM'}</h1>
        <p className="muted">Клиенты, сделки и платежи. Обучение — в LMS, боты — в Voronka.</p>
        {accessHint && (
          <p role="status" className="notice">
            {accessHint}
          </p>
        )}
        {ssoEnabled && !localRecover && (
          <div>
            <a className="primary full" href="/api/crm/oidc/login">
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
        <div className="access-request fine">
          <p>Нет доступа в CRM?</p>
          <a className="primary full access-cta" href={ACCESS_REQUEST.signal} target="_blank" rel="noreferrer">
            {ACCESS_REQUEST.label} в SIGNAL
          </a>
          <p>
            Администратор выдаёт staff в{' '}
            <a href={ACCESS_REQUEST.cmsStaff} target="_blank" rel="noreferrer">
              CMS → Администраторы
            </a>
            {' '}(provision CRM + Signal).
          </p>
        </div>
      </section>
    </main>
  );
}
