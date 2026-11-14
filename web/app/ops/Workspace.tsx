'use client';

import { useEffect, useRef, useState } from 'react';
import { createOpsApi } from './api';
import { LoginScreen } from './components/LoginScreen';
import { sections, sectionHints } from './constants';
import { Icon } from './icons';
import { crmHref, miniAppHref } from './portals';
import { AiSection } from './sections/AiSection';
import { AutomationsSection } from './sections/AutomationsSection';
import { BotsSection } from './sections/BotsSection';
import { BroadcastsSection } from './sections/BroadcastsSection';
import { MediaSection } from './sections/MediaSection';
import { ScenariosSection } from './sections/ScenariosSection';
import { SupportSection } from './sections/SupportSection';
import type { Row } from './types';

export default function Workspace() {
  const loadVersion = useRef(0);
  const [token, setToken] = useState('');
  const [ready, setReady] = useState(false);
  const [sessionChecked, setSessionChecked] = useState(false);
  const [me, setMe] = useState<Row | null>(null);
  const [pid, setPid] = useState('');
  const [section, setSection] = useState('bots');
  const [portalLinks, setPortalLinks] = useState<Row[]>([]);
  const [hub, setHub] = useState<Row[]>([]);
  const [preflight, setPreflight] = useState<Row | null>(null);
  const [funnels, setFunnels] = useState<Row[]>([]);
  const [identities, setIdentities] = useState<Row[]>([]);
  const [assets, setAssets] = useState<Row[]>([]);
  const [mediaPolicy, setMediaPolicy] = useState<Row | null>(null);
  const [campaigns, setCampaigns] = useState<Row[]>([]);
  const [aiStatus, setAiStatus] = useState<Row | null>(null);
  const [selectedBot, setSelectedBot] = useState('');
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const [busy, setBusy] = useState(false);

  const api = createOpsApi(
    () => token,
    () => {
      setToken('');
      setMe(null);
    },
  );

  useEffect(() => {
    setReady(true);
  }, []);

  async function act(action: () => Promise<void>) {
    setBusy(true);
    setError('');
    setNotice('');
    try {
      await action();
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Ошибка');
    } finally {
      setBusy(false);
    }
  }

  async function load(p = pid) {
    if (!p) return;
    const version = ++loadVersion.current;
    const [hubRes, pf, fun, idents, media, camps, ai, portalsRes] = await Promise.all([
      api(`/api/crm/projects/${p}/connections/hub`).catch(() => ({ items: [] })),
      api('/api/preflight').catch(() => null),
      api('/api/funnels').catch(() => []),
      api(`/api/ops/projects/${p}/identities`).catch(() => ({ items: [] })),
      api(`/api/crm/projects/${p}/media-assets`).catch(() => ({ items: [], admin_bot_media_policy: null })),
      api(`/api/crm/projects/${p}/campaigns`).catch(() => []),
      api('/api/crm/ai/status').catch(() => null),
      api('/api/ops/portals').catch(() => ({ links: [] })),
    ]);
    if (version !== loadVersion.current) return;
    setHub(hubRes.items || []);
    setPreflight(pf);
    setFunnels(Array.isArray(fun) ? fun : []);
    setIdentities(idents.items || []);
    setAssets(media.items || []);
    setMediaPolicy(media.admin_bot_media_policy || null);
    setCampaigns(Array.isArray(camps) ? camps : []);
    setAiStatus(ai);
    setPortalLinks(portalsRes?.links || []);
  }

  useEffect(() => {
    if (!ready) return;
    void act(async () => {
      try {
        const m = await api('/api/crm/me');
        if (m.role === 'manager') {
          setError('Менеджеры работают в CRM. Ops — для owner/editor.');
          setMe(null);
          return;
        }
        setMe(m);
        setPid((current) => (m.projects.some((p: Row) => p.id === current) ? current : m.projects[0]?.id || ''));
      } catch {
        setMe(null);
        setToken('');
      } finally {
        setSessionChecked(true);
      }
    });
  }, [ready, token]); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    if (me && pid) void act(() => load());
  }, [pid, me]); // eslint-disable-line react-hooks/exhaustive-deps

  if (!ready || !sessionChecked) return <div className="loading">Загрузка…</div>;
  if (!me) {
    return (
      <LoginScreen
        api={api}
        busy={busy}
        error={error}
        notice={notice}
        onBusy={act}
        onError={setError}
        onNotice={setNotice}
        onToken={setToken}
      />
    );
  }

  const messengerBots = hub.filter((c) => c.platform !== 'email');

  return (
    <div className="workspace">
      <aside className="sidebar">
        <div className="wordmark">
          <span className="brandmark small">В</span>
          <div>
            Voronka<small>ops</small>
          </div>
        </div>
        {portalLinks.length > 0 && (
          <div className="portal-chrome" aria-label="Порталы">
            {portalLinks.map((link: Row) => (
              <a
                key={link.id}
                href={link.url}
                className={link.id === 'voronka' ? 'current' : undefined}
                target={link.id === 'voronka' ? undefined : '_blank'}
                rel="noreferrer"
              >
                {link.label}
              </a>
            ))}
          </div>
        )}
        <label className="project-label">
          ПРОЕКТ
          <select aria-label="Проект" value={pid} onChange={(e) => setPid(e.target.value)}>
            {me.projects.map((p: Row) => (
              <option key={p.id} value={p.id}>
                {p.name}
              </option>
            ))}
          </select>
        </label>
        <nav>
          {sections.map(([id, label]) => (
            <button
              key={id}
              className={section === id ? 'nav active' : 'nav'}
              onClick={() => {
                setSection(id);
                setError('');
              }}
            >
              <span className="nav-icon">
                <Icon name={id} />
              </span>
              {label}
            </button>
          ))}
        </nav>
        <div className="sidebar-bottom">
          <a href={crmHref()} target="_blank" rel="noreferrer">
            Открыть CRM ↗
          </a>
          <a href={miniAppHref()} target="_blank" rel="noreferrer">
            Mini App (Telegram) ↗
          </a>
          <div className="user">
            <span className="avatar">{me.role === 'owner' ? 'В' : 'Р'}</span>
            <div>
              {me.role === 'owner' ? 'Владелец' : 'Партнёр'}
              <small>Ops zone</small>
            </div>
            <button
              title="Выйти"
              onClick={() =>
                void act(async () => {
                  await api('/api/crm/logout', 'POST');
                  setToken('');
                  setMe(null);
                })
              }
            >
              ↪
            </button>
          </div>
        </div>
      </aside>
      <div className="main-area">
        <div className="topbar">
          <span>{sectionHints[section]}</span>
          <span>
            <b>·</b> voronka.solo-pr.ru/ops
          </span>
        </div>
        <div className="content">
          {error && (
            <p role="alert" className="error banner">
              {error}
            </p>
          )}
          {notice && (
            <p role="status" className="notice banner">
              {notice}
            </p>
          )}
          {section === 'bots' && (
            <BotsSection
              hub={hub}
              preflight={preflight}
              busy={busy}
              onRefresh={() => void act(() => load())}
              onActivate={(id) =>
                void act(async () => {
                  await api(`/api/connections/${id}/activate`, 'POST');
                  setNotice('Webhook activate запрошен');
                  await load();
                })
              }
            />
          )}
          {section === 'scenarios' && (
            <ScenariosSection
              funnels={funnels}
              bots={messengerBots}
              selectedBot={selectedBot}
              onSelectBot={setSelectedBot}
              miniAppHref={miniAppHref()}
            />
          )}
          {section === 'broadcasts' && (
            <BroadcastsSection
              bots={messengerBots}
              identities={identities}
              assets={assets}
              campaigns={campaigns}
              busy={busy}
              onCreate={async (payload) => {
                await act(async () => {
                  await api(`/api/crm/projects/${pid}/campaigns`, 'POST', payload);
                  setNotice('Черновик рассылки создан');
                  setSection('broadcasts');
                  await load();
                });
              }}
              onPreflight={async (id) => {
                await act(async () => {
                  const r = await api(`/api/crm/campaigns/${id}/preflight`, 'POST');
                  setNotice(r.ready ? 'Preflight ok' : `Preflight: ${r.asset_blocker || 'не готово'}`);
                });
              }}
              onLaunch={async (id) => {
                await act(async () => {
                  await api(`/api/crm/campaigns/${id}/launch`, 'POST');
                  setNotice('Рассылка запущена в Outbox');
                  await load();
                });
              }}
            />
          )}
          {section === 'support' && (
            <SupportSection
              bots={messengerBots}
              busy={busy}
              onCreate={async (payload) => {
                let created: Row = {};
                await act(async () => {
                  created = await api(`/api/ops/projects/${pid}/wizard/support`, 'POST', payload);
                  setNotice('Пайплайн поддержки создан');
                  await load();
                });
                return created;
              }}
            />
          )}
          {section === 'automations' && (
            <AutomationsSection
              bots={messengerBots}
              busy={busy}
              onCreate={async (payload) => {
                let created: Row = {};
                await act(async () => {
                  created = await api(`/api/ops/projects/${pid}/wizard/channel-dm`, 'POST', payload);
                  setNotice('Автоматизация канал→DM создана');
                  await load();
                });
                return created;
              }}
            />
          )}
          {section === 'media' && <MediaSection assets={assets} policy={mediaPolicy} />}
          {section === 'ai' && (
            <AiSection
              status={aiStatus}
              bots={messengerBots}
              busy={busy}
              onSave={async (payload) => {
                await act(async () => {
                  const body = { ...payload };
                  if (body.scope === 'project') body.project_id = pid;
                  if (body.scope === 'connection') {
                    body.connection_id = payload.connection_id;
                    body.project_id = pid;
                  }
                  if (body.scope === 'global') {
                    delete body.connection_id;
                    delete body.project_id;
                  }
                  await api('/api/crm/ai/rules', 'PUT', body);
                  setNotice('Правило сохранено (draft-only)');
                  await load();
                });
              }}
            />
          )}
        </div>
      </div>
    </div>
  );
}
