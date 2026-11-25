'use client';

import { useEffect, useRef, useState } from 'react';
import { createCrmApi } from './api';
import { FormModal } from './components/FormModal';
import { HistoryTimeline } from './components/HistoryTimeline';
import { LoginScreen } from './components/LoginScreen';
import { SystemHelpModal } from './components/SystemHelpModal';
import { CLIENT_PAGE_SIZES, sections, sectionHints, stateNames } from './constants';
import { Icon } from './icons';
import SignalLauncher from '../components/SignalLauncher';
import { ACCESS_REQUEST, CONTACT_SOURCE_FILTERS, voronkaOpsHref } from './portals';
import { ActionConstructorSection } from './sections/ActionConstructorSection';
import { AnalyticsSection } from './sections/AnalyticsSection';
import { InboxSection } from './sections/InboxSection';
import { ConnectionsVaultSection } from './sections/ConnectionsVaultSection';
import { MediaLibrarySection } from './sections/MediaLibrarySection';
import { PaymentsSection } from './sections/PaymentsSection';
import { ProjectCoursesPanel } from './sections/ProjectCoursesPanel';
import { ProjectSalesDashboard } from './sections/ProjectSalesDashboard';
import { ProjectsBoardSection } from './sections/ProjectsBoardSection';
import { SalesRunbookPanel } from './sections/SalesRunbookPanel';
import { SignalPersonActions } from './sections/SignalPersonActions';
import { WorkTodayPanel } from './sections/WorkTodayPanel';
import type { FormConfig, Row } from './types';
import { date, money, names, toMinor } from './utils';

export default function Workspace() {
  const loadVersion = useRef(0);
  const [token, setToken] = useState('');
  const [ready, setReady] = useState(false);
  const [sessionChecked, setSessionChecked] = useState(false);
  const [helpOpen, setHelpOpen] = useState(false);
  const [me, setMe] = useState<Row | null>(null);
  const [pid, setPid] = useState('');
  const [section, setSection] = useState('home');
  const [overview, setOverview] = useState<Row>({});
  const [projectBoard, setProjectBoard] = useState<Row[]>([]);
  const [dashboard, setDashboard] = useState<Row | null>(null);
  const [branches, setBranches] = useState<Row[]>([]);
  const [branchFilter, setBranchFilter] = useState('');
  const [pageSize, setPageSize] = useState(50);
  const [dealPageSize, setDealPageSize] = useState(50);
  const [dealOffset, setDealOffset] = useState(0);
  const [contacts, setContacts] = useState<Row[]>([]);
  const [total, setTotal] = useState(0);
  const [inbox, setInbox] = useState<Row[]>([]);
  const [inboxFilters, setInboxFilters] = useState({ connection_id: '', unread: false, delivery_errors: false, mine: false });
  const [connectionHub, setConnectionHub] = useState<Row[]>([]);
  const [mediaAssets, setMediaAssets] = useState<Row[]>([]);
  const [aiStatus, setAiStatus] = useState<Row | null>(null);
  const [deals, setDeals] = useState<Row[]>([]);
  const [products, setProducts] = useState<Row[]>([]);
  const [orders, setOrders] = useState<Row[]>([]);
  const [tasks, setTasks] = useState<Row[]>([]);
  const [campaigns, setCampaigns] = useState<Row[]>([]);
  const [promos, setPromos] = useState<Row[]>([]);
  const [staff, setStaff] = useState<Row[]>([]);
  const [query, setQuery] = useState('');
  const [sourceFilter, setSourceFilter] = useState('');
  const [offset, setOffset] = useState(0);
  const [selected, setSelected] = useState<string[]>([]);
  const [detail, setDetail] = useState<Row | null>(null);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const [busy, setBusy] = useState(false);
  const [form, setForm] = useState<FormConfig | null>(null);
  const [board, setBoard] = useState(true);
  const [file, setFile] = useState<File | null>(null);
  const [paste, setPaste] = useState('');
  const [preview, setPreview] = useState<Row | null>(null);
  const [mapping, setMapping] = useState<Record<string, string>>({});
  const [importKind, setImportKind] = useState<'clients' | 'audience'>('clients');
  const [audienceConsentMode, setAudienceConsentMode] = useState<'quarantine' | 'grant_marketing'>('quarantine');
  const [audienceEvidence, setAudienceEvidence] = useState('');
  const [audiencePreview, setAudiencePreview] = useState<Row | null>(null);
  const [portalLinks, setPortalLinks] = useState<Row[]>([]);
  const [paymentProviders, setPaymentProviders] = useState<Row | null>(null);
  const [salesRunbook, setSalesRunbook] = useState<Row | null>(null);
  const [workToday, setWorkToday] = useState<Row | null>(null);
  /** Deep-linked CMS cart id — only valid while `pid` matches `cartForProject`. */
  const [cartId, setCartId] = useState<string>('');
  const [cartForProject, setCartForProject] = useState<string>('');
  /** B-03 / CRM-R10: media drawer overlay — keep constructor draft mounted; pick attaches to square. */
  const [mediaOverlay, setMediaOverlay] = useState(false);
  const [mediaPickNodeId, setMediaPickNodeId] = useState<string | null>(null);
  /** CRM-R10: Signal return_to / overlay pick → attach asset to square. */
  const [ctorFocusNodeId, setCtorFocusNodeId] = useState<string | null>(null);
  const [ctorFocusChainId, setCtorFocusChainId] = useState<string | null>(null);
  const [ctorPendingAssetId, setCtorPendingAssetId] = useState<string | null>(null);
  const [ctorPendingContentKind, setCtorPendingContentKind] = useState<string | null>(null);
  const prevPidRef = useRef('');
  const [mailStatus, setMailStatus] = useState<Row | null>(null);
  const [cmsMatrix, setCmsMatrix] = useState<Row | null>(null);
  const [analytics, setAnalytics] = useState<Row | null>(null);
  const [periodFrom, setPeriodFrom] = useState(() => {
    const d = new Date();
    d.setDate(1);
    return d.toISOString().slice(0, 10);
  });
  const [periodTo, setPeriodTo] = useState(() => new Date().toISOString().slice(0, 10));

  const api = createCrmApi(
    () => token,
    () => {
      setToken('');
      setMe(null);
    },
  );

  useEffect(() => {
    // Migrate legacy sessionStorage tokens once; prefer HttpOnly cookie thereafter.
    const legacy = sessionStorage.getItem('crm-token') || '';
    if (legacy) sessionStorage.removeItem('crm-token');
    setToken(legacy);
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

  async function load(p = pid, filters = inboxFilters, source = sourceFilter) {
    if (!p) return;
    const version = ++loadVersion.current;
    const inboxQs = new URLSearchParams();
    if (filters.connection_id) inboxQs.set('connection_id', filters.connection_id);
    if (filters.unread) inboxQs.set('unread', 'true');
    if (filters.delivery_errors) inboxQs.set('delivery_errors', 'true');
    if (filters.mine && me?.id) inboxQs.set('owner_id', String(me.id));
    const contactQs = new URLSearchParams({
      q: query,
      offset: String(offset),
      limit: String(pageSize),
    });
    if (source) contactQs.set('source', source);
    if (branchFilter) contactQs.set('branch_id', branchFilter);
    const [o, c, d, pr, t, ord, ca, pc, st, ib, hub, assets, ai, portalsRes, pay, mail, matrix, dash, br] =
      await Promise.all([
      api(`/projects/${p}/overview`),
      api(`/projects/${p}/contacts?${contactQs.toString()}`),
      api(`/projects/${p}/deals`),
      api(`/projects/${p}/products`),
      api(`/projects/${p}/tasks`),
      api(`/projects/${p}/orders`),
      api(`/projects/${p}/campaigns`),
      api(`/projects/${p}/promos`),
      api('/staff'),
      api(`/projects/${p}/inbox?${inboxQs.toString()}`),
      api(`/projects/${p}/connections/hub`).catch(() => ({ items: [] })),
      api(`/projects/${p}/media-assets`).catch(() => ({ items: [] })),
      api('/ai/status').catch(() => null),
      api('/portals').catch(() => ({ links: [] })),
      api(`/projects/${p}/payment-providers`).catch(() => null),
      api(`/projects/${p}/mail-status`).catch(() => null),
      api('/staff/cms-matrix').catch(() => null),
      api(`/projects/${p}/dashboard`).catch(() => null),
      api(`/projects/${p}/branches`).catch(() => ({ items: [] })),
    ]);
    if (version !== loadVersion.current) return;
    setOverview(o);
    setDashboard(dash);
    setBranches(br.items || []);
    setContacts(c.items);
    setTotal(c.total);
    setDeals(d);
    setProducts(pr);
    setTasks(t);
    setOrders(ord);
    setCampaigns(ca);
    setPromos(pc);
    setStaff(st);
    setInbox(ib.items || []);
    setConnectionHub(hub.items || []);
    setMediaAssets(assets.items || []);
    setAiStatus(ai);
    setPortalLinks(portalsRes?.links || []);
    setPaymentProviders(pay);
    setMailStatus(mail);
    setCmsMatrix(matrix);
    void loadWorkToday(p);
  }

  async function loadAnalytics() {
    if (!pid) return;
    const qs = new URLSearchParams({ from_date: periodFrom, to_date: periodTo });
    setAnalytics(await api(`/projects/${pid}/analytics?${qs.toString()}`));
  }

  async function loadHomeBoard() {
    const board = await api('/home/projects');
    setProjectBoard(board.items || []);
  }

  /** Canonical hash for a section. Constructor stays `#today` (CART-01). */
  function sectionHash(id: string): string {
    if (id === 'chains') return '#today';
    if (id === 'home') return '#home';
    if (id === 'media') return '#media';
    return `#${id}`;
  }

  function writeHash(hash: string) {
    if (typeof window === 'undefined') return;
    const next = window.location.pathname + window.location.search + hash;
    if (window.location.pathname + window.location.search + window.location.hash === next) return;
    window.history.replaceState({}, '', next);
  }

  /** CRM-R10: apply /crm/?project=&node=&section=chains&asset_id= from Signal return_to. */
  function applyLocationSearch() {
    if (typeof window === 'undefined') return;
    const q = new URLSearchParams(window.location.search || '');
    const project = (q.get('project') || '').trim();
    const sectionParam = (q.get('section') || '').trim();
    const node = (q.get('node') || '').trim();
    const chain = (q.get('chain') || '').trim();
    const assetId = (q.get('asset_id') || '').trim();
    const contentKind = (q.get('content_kind') || '').trim();
    if (project) setPid(project);
    if (sectionParam === 'chains' || sectionParam === 'today' || node || assetId) {
      setSection('chains');
      writeHash('#today');
    } else if (sectionParam && sections.some(([sid]) => sid === sectionParam)) {
      setSection(sectionParam);
    }
    if (node) setCtorFocusNodeId(node);
    if (chain) setCtorFocusChainId(chain);
    if (assetId) setCtorPendingAssetId(assetId);
    if (contentKind) setCtorPendingContentKind(contentKind);
  }

  /** Apply location.hash → section / cart / person. Safe to call on hashchange. */
  function applyLocationHash() {
    if (typeof window === 'undefined') return;
    const hash = window.location.hash || '';
    const base = hash.split('?')[0] || '';
    // CART-01: #today is constructor-only (not WorkToday sales contour).
    // CART-04: #today?project=&cart= — project + CMS cart id for Constructor.
    if (base === '#today' || hash.startsWith('#today')) {
      setSection('chains');
      const qIdx = hash.indexOf('?');
      if (qIdx >= 0) {
        const q = new URLSearchParams(hash.slice(qIdx + 1));
        const project = (q.get('project') || '').trim();
        const cart = (q.get('cart') || '').trim();
        const node = (q.get('node') || '').trim();
        const chain = (q.get('chain') || '').trim();
        const assetId = (q.get('asset_id') || '').trim();
        const contentKind = (q.get('content_kind') || '').trim();
        if (project) setPid(project);
        setCartId(cart);
        setCartForProject(cart && project ? project : '');
        if (node) setCtorFocusNodeId(node);
        if (chain) setCtorFocusChainId(chain);
        if (assetId) setCtorPendingAssetId(assetId);
        if (contentKind) setCtorPendingContentKind(contentKind);
      }
    } else if (base === '#payments') setSection('payments');
    else if (base === '#connections') setSection('connections');
    else if (base === '#media' || base === '#mediathek') setSection('media');
    else if (base === '#chains' || base === '#constructor') setSection('chains');
    else if (base === '#overview') setSection('overview');
    else if (base === '#home' || base === '#projects') setSection('home');
    else if (base === '#help' || hash.startsWith('#help')) setHelpOpen(true);
    else if (base.startsWith('#')) {
      const id = base.slice(1);
      if (sections.some(([sid]) => sid === id)) setSection(id);
    }
    if (hash === '#help' || hash.startsWith('#help')) setHelpOpen(true);
    const personMatch = hash.match(/person=([0-9a-fA-F-]{8,})/);
    if (personMatch) {
      void api('/contacts/' + personMatch[1])
        .then((row: Row) => setDetail(row))
        .catch(() => undefined);
    }
  }

  function goSection(id: string) {
    setSection(id);
    setDetail(null);
    setError('');
    // CRM-R6: «Проекты» = home board face — leave project filter empty on the board.
    if (id === 'home') {
      setPid('');
      setCartId('');
      setCartForProject('');
      setWorkToday(null);
    }
    writeHash(sectionHash(id));
  }

  useEffect(() => {
    if (!ready) return;
    void act(async () => {
      try {
        const m = await api('/me');
        setMe(m);
        // Keep current project if still allowed; otherwise leave empty until staff picks from board.
        // Fresh SSO with no deep-link must not auto-enter a project (home = board only).
        setPid((current) => (m.projects.some((p: Row) => p.id === current) ? current : ''));
        await loadHomeBoard().catch(() => setProjectBoard([]));
      } catch {
        setMe(null);
        setToken('');
      } finally {
        setSessionChecked(true);
      }
    });
  }, [ready, token]); // eslint-disable-line react-hooks/exhaustive-deps

  // Hash router: apply on login + listen for deep-links / back-forward.
  // CRM-R6: empty hash after SSO → #home (project cards), never contacts/WorkToday as face.
  // Nav writes the matching hash so a stuck `#overview` cannot snap the section back.
  useEffect(() => {
    if (!me) return;
    try {
      const saved = sessionStorage.getItem('crm-return-hash');
      if (saved) {
        sessionStorage.removeItem('crm-return-hash');
        if (!window.location.hash || window.location.hash === '#') {
          writeHash(saved);
        }
      }
    } catch {}
    // CRM-R10: Signal return_to lands on /crm/?project=&node=&section=chains&asset_id=
    // before hash — apply search first so empty-hash → #home does not wipe it.
    applyLocationSearch();
    applyLocationHash();
    const hash = typeof window !== 'undefined' ? window.location.hash || '' : '';
    const search = typeof window !== 'undefined' ? window.location.search || '' : '';
    const searchQ = new URLSearchParams(search);
    const fromSignalReturn =
      searchQ.has('asset_id') ||
      searchQ.has('node') ||
      searchQ.get('section') === 'chains' ||
      searchQ.get('section') === 'today';
    const base = hash.split('?')[0] || '';
    if ((!base || base === '#') && !fromSignalReturn) {
      writeHash('#home');
      setSection('home');
      setPid('');
    }
    const onHash = () => applyLocationHash();
    window.addEventListener('hashchange', onHash);
    return () => window.removeEventListener('hashchange', onHash);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [me]);

  // CART-04 / Codex P2: drop deep-linked cart when staff switches Project (selector / board).
  // CRM-R7: also reset deals table page so a smaller project is not stuck on an empty page.
  useEffect(() => {
    const prev = prevPidRef.current;
    if (prev && prev !== pid) {
      setCartId('');
      setCartForProject('');
      setDealOffset(0);
    }
    prevPidRef.current = pid;
  }, [pid]);

  useEffect(() => {
    if (section !== 'chains') setMediaOverlay(false);
  }, [section]);

  useEffect(() => {
    if (!me) return;
    let cancelled = false;
    void api('/sales-runbook')
      .then((row: Row) => {
        if (!cancelled) setSalesRunbook(row);
      })
      .catch(() => {
        if (!cancelled) setSalesRunbook(null);
      });
    return () => {
      cancelled = true;
    };
  }, [me]); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    if (!me) return;
    setDetail(null);
    setSelected([]);
    setPreview(null);
    setContacts([]);
    setDeals([]);
    setOrders([]);
    setTasks([]);
    setInbox([]);
    if (pid) {
      // Silent background load — do not flip global `busy` (that blinked #overview
      // chrome and made nav feel locked while Promise.all settled).
      void load().catch((e) => {
        setError(e instanceof Error ? e.message : 'Ошибка загрузки');
      });
      return;
    }
    // Linked staff without a project: keep chrome + portals; do not wall the world.
    let cancelled = false;
    void api('/portals')
      .then((portalsRes: Row) => {
        if (!cancelled) setPortalLinks(portalsRes?.links || []);
      })
      .catch(() => {
        if (!cancelled) setPortalLinks([]);
      });
    return () => {
      cancelled = true;
    };
  }, [pid, me, offset, sourceFilter, pageSize, branchFilter]); // eslint-disable-line react-hooks/exhaustive-deps

  function openCreateProject() {
    setForm({
      title: 'Новый проект',
      fields: [
        { key: 'name', label: 'Название', required: true },
        { key: 'goal', label: 'Задача проекта' },
        { key: 'description', label: 'Краткое описание' },
      ],
      save: async (v) => {
        const p = await api('/api/projects', 'POST', v);
        const m = await api('/me');
        setMe(m);
        setPid(p.id);
        setSection('overview');
        writeHash('#overview');
        await loadHomeBoard().catch(() => undefined);
      },
    });
  }

  function openProject(id: string) {
    setOffset(0);
    setDealOffset(0);
    setPid(id);
    setCartId('');
    setCartForProject('');
    setSection('overview');
    writeHash('#overview');
  }

  /** B-01 / CART-01: «Запустить цепочку» / #today → full-page Action Constructor (project pick if none). */
  function goToday() {
    setSection('chains');
    setDetail(null);
    setError('');
    writeHash('#today');
    if (typeof window !== 'undefined') {
      window.requestAnimationFrame(() => {
        document.getElementById('action-constructor')?.scrollIntoView({ behavior: 'smooth', block: 'start' });
      });
    }
  }

  async function loadWorkToday(p = pid) {
    if (!p) {
      setWorkToday(null);
      return;
    }
    try {
      setWorkToday(await api(`/projects/${p}/work-today`));
    } catch {
      setWorkToday(null);
    }
  }

  async function showContact(id: string) {
    setDetail(await api('/contacts/' + id));
  }
  const clients = names(contacts);
  const productOptions = names(products.filter((p) => p.active));
  const stages = Object.entries(me?.stages || {}) as [string, string][];
  const kinds = Object.entries(me?.product_kinds || {}) as [string, string][];
  const clientName = (id: string) => contacts.find((c) => c.id === id)?.name || id.slice(0, 8);

  function convertLeadForm(lead: Row) {
    const targets = (me?.projects || []).filter(
      (p: Row) => p.id !== me?.start_project_id && !p.is_system,
    ) as Row[];
    if (!targets.length) {
      setError('Нет коммерческого проекта для конвертации. Создайте проект на доске.');
      return;
    }
    setForm({
      title: `В проект · ${lead.name || 'лид'}`,
      fields: [
        {
          key: 'project_id',
          label: 'Проект',
          type: 'select',
          required: true,
          options: targets.map((p) => [String(p.id), String(p.name)]) as [string, string][],
        },
        {
          key: 'person_role',
          label: 'Роль',
          type: 'select',
          value: 'lead',
          options: [
            ['lead', 'Лид'],
            ['client', 'Клиент'],
            ['alumni', 'Alumni'],
          ],
        },
      ],
      save: async (v) => {
        const res = await api(`/contacts/${lead.id}/convert`, 'POST', {
          project_id: v.project_id,
          person_role: v.person_role || 'lead',
        });
        setNotice(`Перенесено в проект`);
        setDetail(null);
        if (res?.project_id) {
          openProject(String(res.project_id));
          await load(String(res.project_id));
          if (res.profile?.id) await showContact(String(res.profile.id));
        } else {
          await load();
        }
      },
    });
  }

  const pagedDeals =
    dealPageSize === 0
      ? deals
      : deals.slice(dealOffset, dealOffset + dealPageSize);
  async function refreshDetail() {
    await load();
    if (detail) await showContact(detail.id);
  }

  function contactForm(row?: Row) {
    setForm({
      title: row ? 'Изменить клиента' : 'Новый клиент',
      fields: [
        { key: 'name', label: 'Имя', value: row?.name, required: true },
        { key: 'email', label: 'Email', type: 'email', value: row?.email },
        { key: 'phone', label: 'Телефон', value: row?.phone },
        { key: 'company', label: 'Компания', value: row?.company },
        {
          key: 'source',
          label: 'Источник',
          type: 'select',
          value: row?.source || 'manual',
          options: [
            ['manual', 'Вручную'],
            ['telegram', 'Telegram'],
            ['max', 'MAX'],
            ['site', 'Сайт'],
            ['import', 'Импорт'],
            ['bot', 'Из ботов'],
          ],
        },
        { key: 'tags', label: 'Теги через запятую', value: row?.tags?.join(', ') },
        { key: 'email_allowed', label: 'Разрешена email-рассылка (основание проверено)', type: 'checkbox', value: row?.extra?.email_allowed ? 'true' : '' },
        { key: 'stopped', label: 'Отказ от сообщений', type: 'checkbox', value: row?.stopped ? 'true' : '' },
      ],
      save: async (v) => {
        const { email_allowed, ...values } = v;
        const data = {
          ...values,
          tags: v.tags.split(',').map((s) => s.trim()).filter(Boolean),
          stopped: v.stopped === 'true',
          extra: { ...(row?.extra || {}), email_allowed: email_allowed === 'true' },
          owner_id: row?.owner_id || null,
        };
        const result = await api(row ? '/contacts/' + row.id : `/projects/${pid}/contacts`, row ? 'PUT' : 'POST', row ? { ...data, revision: row.revision } : data);
        await load();
        await showContact(result.id);
      },
    });
  }

  function productForm(row?: Row) {
    const priorConfig = row?.config && typeof row.config === 'object' ? { ...row.config } : {};
    const boundCourse =
      typeof priorConfig.course_id === 'string' ? priorConfig.course_id : '';
    setForm({
      title: row ? 'Изменить продукт' : 'Новый продукт или услуга',
      fields: [
        { key: 'name', label: 'Название', required: true, value: row?.name },
        { key: 'kind', label: 'Вид', type: 'select', options: kinds, value: row?.kind || 'audit' },
        { key: 'description', label: 'Описание и результат', type: 'textarea', value: row?.description },
        { key: 'price', label: 'Стоимость, ₽', type: 'number', value: String((row?.price_minor || 0) / 100) },
        {
          key: 'lms_course_id',
          label: 'Курс LMS · externalId / ключ ресурса (для AccessGrant)',
          value: boundCourse,
        },
        { key: 'active', label: 'Доступен для оформления заказа', type: 'checkbox', value: row?.active ? 'true' : '' },
      ],
      save: async (v) => {
        const courseId = (v.lms_course_id || '').trim();
        const config = { ...priorConfig };
        if (courseId) config.course_id = courseId;
        else delete config.course_id;
        await api(row ? '/products/' + row.id : `/projects/${pid}/products`, row ? 'PUT' : 'POST', {
          name: v.name,
          kind: v.kind,
          description: v.description,
          price_minor: toMinor(v.price),
          active: v.active === 'true',
          config,
        });
        await load();
      },
    });
  }

  function dealForm(profile?: Row) {
    setForm({
      title: 'Новая сделка',
      fields: [
        { key: 'profile_id', label: 'Клиент', type: 'select', options: clients, value: profile?.id, required: true },
        { key: 'title', label: 'Задача клиента', required: true },
        { key: 'product_id', label: 'Продукт', type: 'select', options: [['', 'Без продукта'], ...names(products)] },
        { key: 'amount', label: 'Ожидаемая сумма, ₽', type: 'number', value: '0' },
        { key: 'next_action', label: 'Следующее действие' },
        { key: 'due_at', label: 'Когда', type: 'datetime-local' },
      ],
      save: async (v) => {
        await api(`/projects/${pid}/deals`, 'POST', {
          profile_id: v.profile_id,
          title: v.title,
          product_id: v.product_id || null,
          amount_minor: toMinor(v.amount),
          next_action: v.next_action,
          due_at: v.due_at ? new Date(v.due_at).toISOString() : null,
        });
        await refreshDetail();
      },
    });
  }

  function taskForm(profile?: Row) {
    setForm({
      title: 'Задача, сессия или результат работы',
      fields: [
        { key: 'profile_id', label: 'Клиент', type: 'select', options: clients, value: profile?.id, required: true },
        { key: 'kind', label: 'Вид работы', type: 'select', options: [['task', 'Задача'], ['session', 'Сессия'], ['deliverable', 'Результат / этап работы'], ['document', 'Документ'], ['reminder', 'Напоминание']] },
        { key: 'title', label: 'Что сделать', required: true },
        { key: 'due_at', label: 'Срок', type: 'datetime-local' },
      ],
      save: async (v) => {
        await api(`/contacts/${v.profile_id}/tasks`, 'POST', {
          title: v.title,
          kind: v.kind,
          due_at: v.due_at ? new Date(v.due_at).toISOString() : null,
        });
        await refreshDetail();
      },
    });
  }

  function orderForm(profile?: Row) {
    const requestKey = crypto.randomUUID();
    setForm({
      title: 'Оформить заказ',
      fields: [
        { key: 'profile_id', label: 'Клиент', type: 'select', options: clients, value: profile?.id, required: true },
        { key: 'product_id', label: 'Продукт', type: 'select', options: productOptions, required: true },
        { key: 'promo_code', label: 'Промокод' },
        ...(me?.role === 'owner'
          ? [
              { key: 'agreed_price', label: 'Индивидуальная цена, ₽ (пусто — по каталогу)', type: 'number' },
              { key: 'price_reason', label: 'Основание индивидуальной цены' },
            ]
          : []),
        { key: 'installments', label: 'График: дата; сумма ₽ — одна строка на взнос', type: 'textarea' },
      ],
      save: async (v) => {
        const installments = v.installments.trim()
          ? v.installments.trim().split('\n').map((line) => {
              const [day, amount] = line.split(';');
              if (!day || !amount) throw new Error('Формат взноса: 2026-12-01; 5000');
              return { due_at: new Date(day.trim() + 'T12:00:00').toISOString(), amount_minor: toMinor(amount.trim()) };
            })
          : [];
        await api(`/projects/${pid}/orders`, 'POST', {
          profile_id: v.profile_id,
          product_id: v.product_id,
          promo_code: v.promo_code,
          request_key: requestKey,
          installments,
          agreed_price_minor: v.agreed_price ? toMinor(v.agreed_price) : null,
          price_reason: v.price_reason || '',
        });
        await refreshDetail();
      },
    });
  }

  function paymentForm(o: Row, refund = false) {
    const key = crypto.randomUUID();
    setForm({
      title: refund ? 'Подтвердить возврат' : 'Подтвердить поступление',
      fields: [
        { key: 'amount', label: 'Сумма, ₽', type: 'number', value: String((refund ? o.paid_minor : o.balance_minor) / 100), required: true },
        { key: 'evidence', label: 'Основание: выписка, номер документа', required: true },
      ],
      save: async (v) => {
        await api(`/orders/${o.id}/payments`, 'POST', {
          amount_minor: toMinor(v.amount),
          external_id: key,
          kind: refund ? 'refund' : 'payment',
          evidence: v.evidence,
        });
        await refreshDetail();
      },
    });
  }

  function grantForm(o: Row) {
    // Prefill only from immutable order snapshot — never live product.config
    // (orders created before a later LMS bind must stay unbound until staff enter resource).
    const snapConfig =
      o.snapshot?.config && typeof o.snapshot.config === 'object' ? o.snapshot.config : {};
    const boundResource =
      typeof snapConfig.course_id === 'string' ? snapConfig.course_id.trim() : '';
    setForm({
      title: boundResource ? 'Назначить право (ресурс из заказа)' : 'Назначить право',
      fields: [
        {
          key: 'resource',
          label: boundResource
            ? 'Ключ ресурса / раздела (из snapshot заказа · курс LMS продукта)'
            : 'Ключ ресурса / раздела (externalId курса LMS)',
          required: true,
          value: boundResource,
        },
        {
          key: 'capability',
          label: 'Действие',
          type: 'select',
          options: [
            ['course.view', 'Курс (LMS course.view)'],
            ['course.enter', 'Курс (alias course.enter)'],
            ['video.play', 'Видео'],
            ['document.view', 'Документ'],
            ['archive.view', 'Архив'],
            ['live.join', 'Онлайн-занятие'],
            ['certificate.issue', 'Сертификат'],
          ],
          value: boundResource ? 'course.view' : undefined,
        },
        { key: 'min_paid_percent', label: 'Минимальная оплата, %', type: 'number', value: '100' },
        { key: 'expires_at', label: 'До даты', type: 'datetime-local' },
      ],
      save: async (v) => {
        await api(`/orders/${o.id}/grants`, 'POST', {
          ...v,
          min_paid_percent: Number(v.min_paid_percent),
          expires_at: v.expires_at ? new Date(v.expires_at).toISOString() : null,
        });
        setNotice('Назначение сохранено. События access/enrollment уйдут в outbox соседей.');
      },
    });
  }

  async function previewFile(fields: Record<string, string> = mapping) {
    const f = file || (paste ? new File([paste], 'contacts.csv', { type: 'text/csv' }) : null);
    if (!f) throw new Error('Выберите CSV или вставьте строки с заголовками');
    const data = new FormData();
    data.append('file', f);
    data.append('mapping', JSON.stringify(fields));
    const p = await api(`/projects/${pid}/imports/preview`, 'POST', data);
    setPreview(p);
    setMapping(p.mapping);
  }

  async function previewAudience(fields: Record<string, string> = mapping) {
    const f = file || (paste ? new File([paste], 'audience.csv', { type: 'text/csv' }) : null);
    if (!f) throw new Error('Выберите CSV или вставьте строки с заголовками');
    if (audienceConsentMode === 'grant_marketing' && !audienceEvidence.trim()) {
      throw new Error('Для grant_marketing укажите evidence (источник согласия)');
    }
    const data = new FormData();
    data.append('file', f);
    data.append('mapping', JSON.stringify(fields));
    data.append('consent_mode', audienceConsentMode);
    data.append('consent_evidence', audienceEvidence);
    const p = await api(`/projects/${pid}/audience-imports/dry-run`, 'POST', data);
    setAudiencePreview(p);
    setMapping(p.mapping || {});
  }

  function campaignForm() {
    const assetOptions: [string, string][] = [
      ['', 'Только текст'],
      ...mediaAssets.map((a: Row): [string, string] => [
        String(a.id),
        `${a.kind} · ${a.name}${a.blocker ? ' · БЛОКЕР' : ''}`,
      ]),
    ];
    setForm({
      title: 'Приветственная кампания',
      fields: [
        { key: 'title', label: 'Название', required: true },
        {
          key: 'connection_id',
          label: 'Подключение',
          type: 'select',
          options: (overview.connections || []).map((c: Row) => {
            const vn = c.capabilities?.video_note;
            const note = vn && !vn.available ? ' · video_note недоступно' : '';
            return [c.id, `${c.name} · ${c.platform}${note}`];
          }),
          required: true,
        },
        { key: 'text', label: 'Сообщение, {name} — имя клиента', type: 'textarea', required: true },
        {
          key: 'asset_id',
          label: 'Медиа (кружок/видео из медиабанка)',
          type: 'select',
          options: assetOptions,
        },
      ],
      save: async (v) => {
        const payload = { ...v, profile_ids: selected, asset_id: v.asset_id || null };
        const c = await api(`/projects/${pid}/campaigns`, 'POST', payload);
        const blocker = c.report?.asset_blocker;
        setNotice(
          blocker
            ? `Черновик создан, но медиа заблокировано: ${blocker}`
            : `Черновик создан. Доступных диалогов: ${c.report.recipients.filter((r: Row) => r.eligible).length} из ${selected.length}. Импортированные ID не активируются автоматически.`,
        );
        setSection('campaigns');
        await load();
      },
    });
  }

  if (!ready || !sessionChecked) return <main className="loading">Загрузка рабочего кабинета…</main>;
  if (!me)
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

  return (
    <div className="workspace">
      <aside className="sidebar">
        <div className="wordmark">
          <span className="brandmark small">С</span>
          <div>
            Соло-PR<small>CRM</small>
          </div>
        </div>
        {portalLinks.length > 0 && (
          <div className="portal-chrome" aria-label="Порталы">
            {portalLinks.map((link: Row) => (
              <a
                key={link.id}
                href={link.url}
                className={link.id === 'crm' ? 'current' : undefined}
                target={link.id === 'crm' ? undefined : '_blank'}
                rel="noreferrer"
              >
                {link.label}
              </a>
            ))}
          </div>
        )}
        <label className="project-label">
          ПРОЕКТ
          <select
            aria-label="Проект"
            value={pid}
            onChange={(e) => {
              setOffset(0);
              setDealOffset(0);
              const next = e.target.value;
              setPid(next);
              if (next) {
                setSection('overview');
                writeHash('#overview');
              } else {
                goSection('home');
              }
            }}
          >
            <option value="">Все проекты (доска)</option>
            {me.projects.map((p: Row) => (
              <option key={p.id} value={p.id}>
                {p.name}
                {p.is_system ? ' · система' : ''}
              </option>
            ))}
          </select>
        </label>
        <button type="button" className="work-today" onClick={goToday}>
          Запустить цепочку
        </button>
        <nav aria-label="Разделы CRM">
          {sections.map(([id, label]) => {
            const secondary = ['campaigns', 'analytics', 'imports', 'settings'].includes(id);
            return (
              <button
                key={id}
                className={`${section === id ? 'nav active' : 'nav'}${secondary ? ' nav-secondary' : ''}`}
                onClick={() => goSection(id)}
              >
                <span className="nav-icon">
                  <Icon name={id} />
                </span>
                {label}
                {id === 'contacts' && <small>{total}</small>}
                {id === 'inbox' && <small>{inbox.length}</small>}
                {id === 'tasks' && <small>{tasks.filter((t) => t.state === 'open').length || ''}</small>}
              </button>
            );
          })}
        </nav>
        <div className="sidebar-bottom">
          <a href={voronkaOpsHref({ section: 'bots' })} target="_blank" rel="noreferrer">
            Боты / Ops ↗
          </a>
          <div className="user">
            <span className="avatar">{me.role === 'owner' ? 'С' : 'М'}</span>
            <div>
              {me.role === 'owner' ? 'Владелец' : me.role === 'editor' ? 'Партнёр' : 'Менеджер'}
              <small>Личный аккаунт</small>
            </div>
            <button
              title="Выйти"
              onClick={() =>
                void act(async () => {
                  await api('/logout', 'POST');
                  sessionStorage.removeItem('crm-token');
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
        <header className="topbar staff-chrome" data-staff-chrome="crm" aria-label="Шапка CRM">
          <div className="staff-chrome__brand">
            <span className="staff-chrome__mark" aria-hidden="true" />
            <div>
              <p className="staff-chrome__product">CRM</p>
              <p className="staff-chrome__meta">
                {sections.find((s) => s[0] === section)?.[1] || 'Обзор'}
                {' · '}
                {me.role === 'owner' ? 'Владелец' : me.role === 'editor' ? 'Партнёр' : 'Менеджер'}
              </p>
            </div>
          </div>
          <div className="staff-chrome__actions" role="toolbar" aria-label="Действия">
            <div className="staff-chrome__platform" role="group" aria-label="Платформа">
              <SignalLauncher role={typeof me?.role === 'string' ? me.role : undefined} />
              <button
                type="button"
                className="staff-chrome__help"
                aria-label="О системе"
                title="О системе"
                aria-expanded={helpOpen}
                aria-controls="system-help-title"
                onClick={() => setHelpOpen(true)}
              >
                ?
              </button>
              <button
                type="button"
                className="staff-chrome__logout"
                onClick={() =>
                  void act(async () => {
                    await api('/logout', 'POST');
                    sessionStorage.removeItem('crm-token');
                    setToken('');
                    setMe(null);
                  })
                }
              >
                Выйти
              </button>
            </div>
            <button type="button" disabled={busy || !pid} onClick={() => void act(() => load())}>
              {busy ? 'Обновление…' : 'Обновить'}
            </button>
          </div>
          <nav className="staff-chrome__strip" data-visible="true" aria-label="Быстрые ссылки">
            <button type="button" className="linkish" onClick={goToday}>
              Запустить цепочку
            </button>
            {/* History lives in the contact drawer (z-40). Hide strip link while drawer open so chrome
                need not sit above the drawer (that blocked «Закрыть карточку»). */}
            {!detail?.id && (
              <button type="button" className="linkish" onClick={() => goSection('contacts')}>
                History
              </button>
            )}
          </nav>
        </header>
        <main className="content">
          <div className="page-heading">
            <div>
              <p className="eyebrow">СОЛО-PR · CRM</p>
              <h1>{sections.find((s) => s[0] === section)?.[1]}</h1>
              <p className="muted">{sectionHints[section]}</p>
            </div>
            {section === 'contacts' && (
              <button className="primary" disabled={!pid} onClick={() => contactForm()}>
                + Новый клиент
              </button>
            )}
            {section === 'deals' && (
              <button className="primary" disabled={!pid || !contacts.length} onClick={() => dealForm()}>
                + Сделка
              </button>
            )}
            {section === 'products' && me.role === 'owner' && (
              <button className="primary" disabled={!pid} onClick={() => productForm()}>
                + Продукт
              </button>
            )}
            {section === 'tasks' && (
              <button className="primary" disabled={!pid || !contacts.length} onClick={() => taskForm()}>
                + Работа
              </button>
            )}
            {section === 'orders' && (
              <button className="primary" disabled={!pid || !productOptions.length || !contacts.length} onClick={() => orderForm()}>
                + Заказ
              </button>
            )}
          </div>
          {error && (
            <div role="alert" className="error banner">
              {error}
            </div>
          )}
          {notice && (
            <div role="status" className="notice banner">
              {notice}
            </div>
          )}
          {/* CRM-R6: home = projects board only — no lead-wall / «project optional» face above cards. */}
          {!pid && section !== 'home' && (
            <div className="notice banner project-pick" role="status">
              {(me.projects || []).length > 0 || projectBoard.length > 0 ? (
                <>
                  <p>
                    Сначала выберите проект на доске — клиенты, сделки и конструктор открываются внутри проекта, не как бесконечный inbox.
                  </p>
                  <button type="button" className="primary" onClick={() => goSection('home')}>
                    К доске проектов
                  </button>
                </>
              ) : me.role === 'owner' ? (
                <>
                  <p>Пока нет проектов. Создайте первый на доске — или откройте системный «Старт», когда он появится.</p>
                  <button type="button" className="primary" onClick={() => goSection('home')}>
                    К доске проектов
                  </button>
                </>
              ) : (
                <>
                  <p>
                    Аккаунт привязан, но проект ещё не назначен. Запросите назначение проекта или staff у администратора.
                  </p>
                  <div className="access-actions">
                    <a className="primary" href={ACCESS_REQUEST.signal} target="_blank" rel="noreferrer">
                      {ACCESS_REQUEST.label} в SIGNAL
                    </a>
                    <a href={ACCESS_REQUEST.cmsStaff} target="_blank" rel="noreferrer">
                      CMS → выдать staff
                    </a>
                  </div>
                </>
              )}
            </div>
          )}
          <>
              {section === 'home' && (
                <ProjectsBoardSection
                  items={projectBoard.length ? projectBoard : me.projects || []}
                  busy={busy}
                  onOpen={openProject}
                  onCreate={openCreateProject}
                />
              )}
              {section === 'overview' && (
                <>
                  {!pid && (
                    <section className="panel">
                      <p className="muted">Выберите проект на доске «Проекты» или в фильтре слева.</p>
                      <button type="button" className="link" onClick={() => goSection('home')}>
                        К доске проектов →
                      </button>
                    </section>
                  )}
                  {!!pid && (
                    <ProjectSalesDashboard
                      dashboard={dashboard}
                      overview={overview}
                      deals={deals}
                      startProjectId={typeof me.start_project_id === 'string' ? me.start_project_id : undefined}
                      busy={busy}
                      onOpenClients={() => goSection('contacts')}
                      onOpenDeals={() => goSection('deals')}
                      onOpenChains={() => goSection('chains')}
                      onOpenContact={(id) => void act(() => showContact(id))}
                      onConvertLead={(lead) => convertLeadForm(lead)}
                      clientName={clientName}
                    />
                  )}
                  <section className="panel" style={{ marginTop: 16 }} data-testid="overview-base-chain">
                    <div className="panel-title">
                      <h2>Базовая цепочка</h2>
                      <button type="button" className="link" onClick={() => goSection('chains')}>
                        Конструктор →
                      </button>
                    </div>
                    {dashboard?.base_chain ? (
                      <>
                        <p>
                          <b>{String((dashboard.base_chain as Row).name)}</b>{' '}
                          <span className="badge">{String((dashboard.base_chain as Row).state)}</span>
                        </p>
                        <ol className="fine" style={{ margin: '8px 0 0', paddingLeft: 18 }}>
                          {(((dashboard.base_chain as Row).steps as Row[]) || []).map((s, i) => (
                            <li key={String(s.id || i)}>
                              {String(s.title || s.kind || `Шаг ${i + 1}`)}
                            </li>
                          ))}
                        </ol>
                      </>
                    ) : (
                      <p className="muted">
                        Пока нет — откройте Конструктор и нажмите «Сохранить». База проекта = сохранённая цепочка, не
                        фиксированный шаблон воронки.
                      </p>
                    )}
                  </section>
                  {!!dashboard?.branches?.length && (
                    <section className="panel" style={{ marginTop: 16 }}>
                      <div className="panel-title">
                        <h2>Ветки</h2>
                        <button
                          type="button"
                          className="link"
                          onClick={() =>
                            setForm({
                              title: 'Новая ветка',
                              fields: [
                                { key: 'name', label: 'Название', required: true },
                                { key: 'description', label: 'Описание' },
                              ],
                              save: async (v) => {
                                await api(`/projects/${pid}/branches`, 'POST', v);
                                await load();
                              },
                            })
                          }
                        >
                          + Ветка
                        </button>
                      </div>
                      <ul className="branch-list">
                        {(dashboard.branches as Row[]).map((b) => (
                          <li key={String(b.id)}>
                            <b>{String(b.name)}</b>
                            <small>{String(b.description || '')}</small>
                          </li>
                        ))}
                      </ul>
                    </section>
                  )}
                  {!!pid && (
                    <ProjectCoursesPanel
                      courses={Array.isArray(dashboard?.courses) ? (dashboard.courses as Row[]) : []}
                      projectName={
                        typeof dashboard?.name === 'string'
                          ? dashboard.name
                          : String(me.projects?.find((p: Row) => p.id === pid)?.name || '')
                      }
                      busy={busy}
                      canEdit={me.role === 'owner' || me.role === 'editor'}
                      onBind={() =>
                        setForm({
                          title: 'Привязать курс LMS',
                          fields: [
                            {
                              key: 'course_id',
                              label: 'Курс LMS · externalId',
                              required: true,
                            },
                            { key: 'label', label: 'Подпись (необязательно)' },
                          ],
                          save: async (v) => {
                            await api(`/projects/${pid}/courses`, 'POST', {
                              course_id: (v.course_id || '').trim(),
                              label: (v.label || '').trim(),
                            });
                            await load();
                          },
                        })
                      }
                      onUnbind={(courseId) =>
                        void act(async () => {
                          await api(`/projects/${pid}/courses/${encodeURIComponent(courseId)}`, 'DELETE');
                          await load();
                        })
                      }
                    />
                  )}
                  {tasks.some((t) => t.state === 'open') && (
                    <section className="panel" style={{ marginTop: 22 }}>
                      <div className="panel-title">
                        <h2>Открытые задачи</h2>
                        <button type="button" className="link" onClick={() => goSection('tasks')}>
                          Все задачи →
                        </button>
                      </div>
                      {tasks
                        .filter((t) => t.state === 'open')
                        .slice(0, 6)
                        .map((t) => (
                          <button className="action-row" key={t.id} onClick={() => void act(() => showContact(t.profile_id))}>
                            <span className="task-dot" />
                            <div>
                              <b>{t.title}</b>
                              <small>{clientName(t.profile_id)}</small>
                            </div>
                            <time>{date(t.due_at)}</time>
                          </button>
                        ))}
                    </section>
                  )}
                  <div className="panel" style={{ marginTop: 22 }}>
                    <h2>Ваши направления</h2>
                    {kinds.slice(0, 6).map(([kind, label]) => (
                      <div key={kind} className="direction">
                        <span>◇</span>
                        <b>{label}</b>
                        <small>{products.filter((p) => p.kind === kind).length} в каталоге</small>
                      </div>
                    ))}
                    <button className="link" onClick={() => goSection('products')}>
                      Перейти к продуктам →
                    </button>
                  </div>
                  <section className="panel work-today-secondary" style={{ marginTop: 22 }} aria-label="Очередь дня">
                    <div className="panel-title">
                      <h2>Очередь дня</h2>
                      <span className="muted">вторично · продажи выше</span>
                    </div>
                    <div className="two-column work-today-layout" style={{ marginTop: 12 }}>
                      <WorkTodayPanel
                        data={workToday}
                        busy={busy}
                        onOpenPerson={(id) => void act(() => showContact(id))}
                        onRefresh={() => void act(() => loadWorkToday())}
                      />
                      <SalesRunbookPanel data={salesRunbook} />
                    </div>
                  </section>
                </>
              )}
              {section === 'inbox' && (
                <InboxSection
                  items={inbox}
                  busy={busy}
                  onOpen={(id) => act(() => showContact(id))}
                  setForm={setForm}
                  api={api}
                  onNotice={setNotice}
                  refresh={async () => {
                    await load();
                  }}
                  connections={overview.connections || []}
                  filters={inboxFilters}
                  setFilters={(next) => {
                    setInboxFilters(next);
                    void act(() => load(pid, next));
                  }}
                  meId={typeof me?.id === 'number' ? me.id : undefined}
                />
              )}
              {section === 'contacts' && (
                <section className="panel table-panel">
                  <div className="toolbar">
                    <form
                      onSubmit={(e) => {
                        e.preventDefault();
                        setOffset(0);
                        void act(() => load());
                      }}
                    >
                      <input aria-label="Поиск клиентов" placeholder="Поиск по имени, почте, компании…" value={query} onChange={(e) => setQuery(e.target.value)} />
                      <button>Найти</button>
                    </form>
                    <span className="spacer" />
                    <button
                      onClick={() =>
                        void act(async () => {
                          await api(`/projects/${pid}/sync-contacts`, 'POST');
                          await load();
                        })
                      }
                    >
                      Из ботов
                    </button>
                    <button onClick={() => setSection('imports')}>Импорт</button>
                    <button
                      onClick={() =>
                        void act(async () => {
                          const blob = await api(`/projects/${pid}/export?q=${encodeURIComponent(query)}`, 'GET', undefined, true);
                          const url = URL.createObjectURL(blob);
                          const a = document.createElement('a');
                          a.href = url;
                          a.download = 'clients.csv';
                          a.click();
                          URL.revokeObjectURL(url);
                        })
                      }
                    >
                      Экспорт CSV
                    </button>
                  </div>
                  <div className="source-filters" role="group" aria-label="Фильтр источника">
                    {CONTACT_SOURCE_FILTERS.map(([id, label]) => (
                      <button
                        key={id || 'all'}
                        type="button"
                        className={sourceFilter === id ? 'active' : undefined}
                        onClick={() => {
                          setOffset(0);
                          setSourceFilter(id);
                        }}
                      >
                        {label}
                      </button>
                    ))}
                  </div>
                  <div className="source-filters" role="group" aria-label="Ветка и размер страницы">
                    <select
                      aria-label="Ветка"
                      value={branchFilter}
                      onChange={(e) => {
                        setOffset(0);
                        setBranchFilter(e.target.value);
                      }}
                    >
                      <option value="">Все ветки</option>
                      <option value="__none__">Без ветки</option>
                      {branches.map((b) => (
                        <option key={String(b.id)} value={String(b.id)}>
                          {String(b.name)}
                        </option>
                      ))}
                    </select>
                    {CLIENT_PAGE_SIZES.map((sz) => (
                      <button
                        key={sz.label}
                        type="button"
                        className={pageSize === sz.value ? 'active' : undefined}
                        onClick={() => {
                          setOffset(0);
                          setPageSize(sz.value);
                        }}
                      >
                        {sz.label}
                      </button>
                    ))}
                  </div>
                  {selected.length > 0 && (
                    <div className="selection">
                      Выбрано: {selected.length}
                      <button onClick={campaignForm}>Приветственная кампания</button>
                      <button onClick={() => setSelected([])}>Снять выбор</button>
                    </div>
                  )}
                  <div className="table-scroll">
                    <table>
                      <thead>
                        <tr>
                          <th>
                            <input
                              aria-label="Выбрать всех клиентов на странице"
                              type="checkbox"
                              checked={contacts.length > 0 && selected.length === contacts.length}
                              onChange={(e) => setSelected(e.target.checked ? contacts.map((c) => c.id) : [])}
                            />
                          </th>
                          <th>Клиент</th>
                          <th>Проект</th>
                          <th>Ветка</th>
                          <th>Компания</th>
                          <th>Контакты</th>
                          <th>Последний результат</th>
                          <th>Источник</th>
                          <th>Теги</th>
                        </tr>
                      </thead>
                      <tbody>
                        {contacts.map((c) => {
                          const projectName =
                            me.projects.find((p: Row) => p.id === c.project_id)?.name || c.project_id;
                          const branchName =
                            branches.find((b) => b.id === c.branch_id)?.name ||
                            (c.branch_id ? String(c.branch_id).slice(0, 8) : '—');
                          const startHighlight = me.start_project_id && c.project_id === me.start_project_id;
                          return (
                          <tr key={c.id} className={startHighlight ? 'start-client' : undefined}>
                            <td>
                              <input
                                aria-label={'Выбрать ' + c.name}
                                type="checkbox"
                                checked={selected.includes(c.id)}
                                onChange={(e) => setSelected(e.target.checked ? [...selected, c.id] : selected.filter((i) => i !== c.id))}
                              />
                            </td>
                            <td>
                              <button className="name-cell" onClick={() => void act(() => showContact(c.id))}>
                                <span className="avatar">{c.name.slice(0, 1)}</span>
                                <span>
                                  {c.name}
                                  {c.stopped && <small>Отказ от сообщений</small>}
                                  {c.is_purchaser && <small>Покупатель</small>}
                                  {startHighlight && <small>Старт</small>}
                                </span>
                              </button>
                            </td>
                            <td>{String(projectName)}</td>
                            <td>{String(branchName)}</td>
                            <td>{c.company || '—'}</td>
                            <td>
                              <div>{c.email || '—'}</div>
                              <small>{c.phone}</small>
                            </td>
                            <td>
                              {c.last_contact?.outcome || 'Ещё нет контакта'}
                              <small>{date(c.last_contact?.created_at)}</small>
                            </td>
                            <td>{c.source}</td>
                            <td>
                              {(c.tags || []).map((t: string) => (
                                <span key={t} className="tag">
                                  {t}
                                </span>
                              ))}
                            </td>
                          </tr>
                          );
                        })}
                      </tbody>
                    </table>
                  </div>
                  {!contacts.length && (
                    <div className="empty">
                      <h2>Здесь появятся ваши клиенты</h2>
                      <p>Добавьте первого вручную, загрузите CSV или перенесите контакты из подключённых ботов.</p>
                    </div>
                  )}
                  <footer className="table-footer">
                    {total} клиентов · на странице {contacts.length} ·{' '}
                    {pageSize === 0 ? 'все' : `по ${pageSize}`}
                    <button
                      disabled={offset === 0 || pageSize === 0}
                      onClick={() => setOffset(Math.max(0, offset - (pageSize || 50)))}
                    >
                      ←
                    </button>
                    <button
                      disabled={pageSize === 0 || offset + pageSize >= total}
                      onClick={() => setOffset(offset + pageSize)}
                    >
                      →
                    </button>
                  </footer>
                </section>
              )}
              {section === 'deals' && (
                <>
                  <div className="view-toggle">
                    <button className={board ? 'selected' : ''} onClick={() => setBoard(true)}>
                      Доска
                    </button>
                    <button className={!board ? 'selected' : ''} onClick={() => setBoard(false)}>
                      Таблица
                    </button>
                  </div>
                  <div className="source-filters" role="group" aria-label="Размер страницы сделок">
                    {CLIENT_PAGE_SIZES.map((sz) => (
                      <button
                        key={`deal-${sz.label}`}
                        type="button"
                        className={dealPageSize === sz.value ? 'active' : undefined}
                        onClick={() => {
                          setDealOffset(0);
                          setDealPageSize(sz.value);
                        }}
                      >
                        {sz.label}
                      </button>
                    ))}
                  </div>
                  {board ? (
                    <div className="board">
                      {stages.map(([key, label]) => (
                        <section className="board-column" key={key}>
                          <h3>
                            <span className={'stage-dot ' + key} />
                            {label}
                            <small>{deals.filter((d) => d.stage === key).length}</small>
                          </h3>
                          {deals
                            .filter((d) => d.stage === key)
                            .map((d) => (
                              <article className="deal-card" key={d.id}>
                                <button className="card-title" onClick={() => void act(() => showContact(d.profile_id))}>
                                  {d.title}
                                </button>
                                <p>{clientName(d.profile_id)}</p>
                                <strong>{money(d.amount_minor)}</strong>
                                <button
                                  className="link"
                                  onClick={() =>
                                    setForm({
                                      title: 'Следующее действие',
                                      fields: [
                                        { key: 'next_action', label: 'Что сделать', value: d.next_action },
                                        { key: 'due_at', label: 'Когда', type: 'datetime-local' },
                                      ],
                                      save: async (v) => {
                                        await api('/deals/' + d.id, 'PATCH', {
                                          stage: d.stage,
                                          revision: d.revision,
                                          next_action: v.next_action,
                                          due_at: v.due_at ? new Date(v.due_at).toISOString() : null,
                                        });
                                        await load();
                                      },
                                    })
                                  }
                                >
                                  Назначить шаг
                                </button>
                                <p className="next-action">{d.next_action || 'Следующий шаг не назначен'}</p>
                                <time>{date(d.due_at)}</time>
                                <SignalPersonActions
                                  compact
                                  personId={
                                    (typeof d.person_id === 'string' && d.person_id) ||
                                    contacts.find((c) => c.id === d.profile_id)?.person_id ||
                                    null
                                  }
                                />
                                <select
                                  aria-label={'Этап сделки ' + d.title}
                                  value={d.stage}
                                  onChange={(e) =>
                                    void act(async () => {
                                      await api('/deals/' + d.id, 'PATCH', {
                                        stage: e.target.value,
                                        revision: d.revision,
                                        next_action: d.next_action,
                                        due_at: d.due_at,
                                      });
                                      await load();
                                    })
                                  }
                                >
                                  {stages.map(([s, l]) => (
                                    <option key={s} value={s}>
                                      {l}
                                    </option>
                                  ))}
                                </select>
                              </article>
                            ))}
                        </section>
                      ))}
                    </div>
                  ) : (
                    <div className="panel table-scroll">
                      <table>
                        <thead>
                          <tr>
                            <th>Сделка</th>
                            <th>Клиент</th>
                            <th>Этап</th>
                            <th>Сумма</th>
                            <th>Следующий шаг</th>
                          </tr>
                        </thead>
                        <tbody>
                          {pagedDeals.map((d) => (
                            <tr key={d.id}>
                              <td>
                                <button className="link" onClick={() => void act(() => showContact(d.profile_id))}>
                                  {d.title}
                                </button>
                              </td>
                              <td>{clientName(d.profile_id)}</td>
                              <td>{me.stages[d.stage]}</td>
                              <td>{money(d.amount_minor)}</td>
                              <td>{d.next_action}</td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </div>
                  )}
                  <footer className="table-footer">
                    {deals.length} сделок · на странице {board ? deals.length : pagedDeals.length} ·{' '}
                    {dealPageSize === 0 ? 'все' : `по ${dealPageSize}`}
                    {!board && (
                      <>
                        <button
                          disabled={dealOffset === 0 || dealPageSize === 0}
                          onClick={() => setDealOffset(Math.max(0, dealOffset - (dealPageSize || 50)))}
                        >
                          ←
                        </button>
                        <button
                          disabled={dealPageSize === 0 || dealOffset + dealPageSize >= deals.length}
                          onClick={() => setDealOffset(dealOffset + dealPageSize)}
                        >
                          →
                        </button>
                      </>
                    )}
                  </footer>
                </>
              )}
              {section === 'products' && (
                <>
                  <div className="toolbar">
                    <span className="muted">Стоимость можно согласовать отдельно для каждого предложения.</span>
                    <span className="spacer" />
                    {me.role === 'owner' && (
                      <button
                        onClick={() =>
                          void act(async () => {
                            await api(`/projects/${pid}/catalog-template`, 'POST');
                            await load();
                          })
                        }
                      >
                        Добавить направления Соло-PR
                      </button>
                    )}
                  </div>
                  <div className="product-grid">
                    {products.map((p) => {
                      const courseId =
                        (typeof p.course_id === 'string' && p.course_id.trim()) ||
                        (p.config && typeof p.config === 'object' && typeof p.config.course_id === 'string'
                          ? p.config.course_id.trim()
                          : '');
                      const courseProjects = Array.isArray(p.course_projects)
                        ? (p.course_projects as Row[])
                        : [];
                      const otherNames = courseProjects
                        .filter((x) => String(x.project_id) !== pid)
                        .map((x) => String(x.project_name || ''))
                        .filter(Boolean);
                      const ownProjectName =
                        courseProjects.find((x) => String(x.project_id) === pid)?.project_name ||
                        dashboard?.name ||
                        me.projects?.find((pr: Row) => pr.id === pid)?.name ||
                        '';
                      return (
                      <article className="panel product-card" key={p.id} data-crm-r11="product-course">
                        <div className="row">
                          <span className="tag">{me.product_kinds[p.kind]}</span>
                          <span className={p.active ? 'badge good' : 'badge'}>{p.active ? 'Активен' : 'Черновик'}</span>
                        </div>
                        <h2>{p.name}</h2>
                        <p className="muted">{p.description || 'Описание пока не добавлено'}</p>
                        {courseId ? (
                          <>
                            <p className="fine">
                              Курс LMS: <code>{courseId}</code>
                            </p>
                            {ownProjectName ? (
                              <p className="fine">
                                Проект: <b>{String(ownProjectName)}</b>
                              </p>
                            ) : null}
                            {otherNames.length > 0 ? (
                              <p className="fine muted">Также в проектах: {otherNames.join(', ')}</p>
                            ) : null}
                          </>
                        ) : (
                          <p className="fine muted">Курс LMS не привязан</p>
                        )}
                        <div className="row">
                          <strong>{money(p.price_minor)}</strong>
                          <button disabled={me.role !== 'owner'} onClick={() => productForm(p)}>
                            Изменить
                          </button>
                        </div>
                      </article>
                      );
                    })}
                  </div>
                  {!products.length && <div className="empty">Создайте продукт или добавьте шаблоны направлений. Цены не заполняются вымышленными значениями.</div>}
                </>
              )}
              {section === 'tasks' && (
                <section className="panel table-scroll">
                  <table>
                    <thead>
                      <tr>
                        <th>Работа</th>
                        <th>Клиент</th>
                        <th>Вид</th>
                        <th>Срок</th>
                        <th>Результат</th>
                        <th>Состояние</th>
                      </tr>
                    </thead>
                    <tbody>
                      {tasks.map((t) => (
                        <tr key={t.id}>
                          <td>{t.title}</td>
                          <td>
                            <button className="link" onClick={() => void act(() => showContact(t.profile_id))}>
                              {clientName(t.profile_id)}
                            </button>
                          </td>
                          <td>{t.kind}</td>
                          <td>{date(t.due_at)}</td>
                          <td>{t.result || '—'}</td>
                          <td>
                            <button
                              onClick={() =>
                                setForm({
                                  title: 'Результат работы',
                                  fields: [
                                    {
                                      key: 'state',
                                      label: 'Состояние',
                                      type: 'select',
                                      options: [
                                        ['open', 'В работе'],
                                        ['done', 'Готово'],
                                        ['cancelled', 'Отменено'],
                                      ],
                                      value: t.state,
                                    },
                                    { key: 'result', label: 'Что сделано / ссылка на документ', type: 'textarea', value: t.result },
                                  ],
                                  save: async (v) => {
                                    await api('/tasks/' + t.id, 'PATCH', v);
                                    await load();
                                  },
                                })
                              }
                            >
                              {stateNames[t.state] || t.state}
                            </button>
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                  {!tasks.length && <div className="empty">Задач пока нет</div>}
                </section>
              )}
              {section === 'orders' && (
                <>
                  <section className="panel table-scroll">
                    <table>
                      <thead>
                        <tr>
                          <th>Заказ</th>
                          <th>Клиент</th>
                          <th>Итого</th>
                          <th>Оплачено</th>
                          <th>Остаток / просрочка</th>
                          <th>Действия</th>
                        </tr>
                      </thead>
                      <tbody>
                        {orders.map((o) => (
                          <tr key={o.id}>
                            <td>
                              <b>{o.snapshot.name}</b>
                              <small>
                                {o.id.slice(0, 8)} · {stateNames[o.state]}
                                {o.snapshot.promo ? ' · ' + o.snapshot.promo : ''}
                              </small>
                              {o.snapshot.discount_minor > 0 && <small>Скидка {money(o.snapshot.discount_minor)}</small>}
                            </td>
                            <td>{clientName(o.profile_id)}</td>
                            <td>{money(o.total_minor)}</td>
                            <td>{money(o.paid_minor)}</td>
                            <td>
                              {money(o.balance_minor)}
                              {o.overdue_minor > 0 && <small className="danger-text">Просрочено {money(o.overdue_minor)}</small>}
                              {o.installments.map((p: Row, i: number) => (
                                <small key={i}>
                                  {date(p.due_at)} · {money(p.amount_minor)}
                                </small>
                              ))}
                            </td>
                            <td>
                              <div className="button-stack">
                                <button
                                  onClick={() =>
                                    void act(async () => {
                                      const r = await api('/orders/' + o.id + '/robokassa', 'POST');
                                      window.open(r.url, '_blank', 'noopener,noreferrer');
                                      setNotice(r.test ? 'Открыта тестовая оплата. Она не выдаёт рабочие права.' : 'Ссылка оплаты открыта');
                                    })
                                  }
                                >
                                  Ссылка оплаты
                                </button>
                                {me.role === 'owner' && (
                                  <>
                                    <button onClick={() => paymentForm(o)}>Учесть оплату</button>
                                    <button onClick={() => paymentForm(o, true)}>Возврат</button>
                                    <button onClick={() => grantForm(o)}>Право доступа</button>
                                    <button
                                      onClick={() =>
                                        setForm({
                                          title: 'Подтверждение обучения',
                                          fields: [{ key: 'evidence', label: 'Основание завершения', required: true }],
                                          save: async (v) => {
                                            await api('/orders/' + o.id + '/academic', 'POST', { complete: true, evidence: v.evidence });
                                            await load();
                                          },
                                        })
                                      }
                                    >
                                      Обучение завершено
                                    </button>
                                  </>
                                )}
                                <button
                                  onClick={() =>
                                    void act(async () => {
                                      await api('/orders/' + o.id + '/cancel', 'POST');
                                      await load();
                                    })
                                  }
                                >
                                  Отменить
                                </button>
                              </div>
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                    {!orders.length && <div className="empty">Создайте заказ по активному продукту из карточки клиента.</div>}
                  </section>
                  <section className="panel promo-panel">
                    <div className="panel-title">
                      <h2>Промокоды</h2>
                      {me.role === 'owner' && (
                        <button
                          onClick={() =>
                            setForm({
                              title: 'Создать промокод',
                              fields: [
                                { key: 'code', label: 'Код (пусто — сгенерировать)' },
                                { key: 'percent', label: 'Скидка, %', type: 'number', required: true },
                                { key: 'max_uses', label: 'Число заказов', type: 'number', value: '1' },
                              ],
                              save: async (v) => {
                                await api(`/projects/${pid}/promos`, 'POST', { code: v.code, percent: Number(v.percent), max_uses: Number(v.max_uses) });
                                await load();
                              },
                            })
                          }
                        >
                          + Промокод
                        </button>
                      )}
                    </div>
                    {promos.map((p) => (
                      <div className="direction" key={p.id}>
                        <b>{p.code}</b>
                        <span>−{p.percent}%</span>
                        <small>
                          Занято {p.reserved} из {p.max_uses}
                        </small>
                      </div>
                    ))}
                  </section>
                </>
              )}
              {section === 'payments' && (
                <PaymentsSection
                  data={paymentProviders}
                  api={api}
                  busy={busy}
                  onBusy={act}
                  onNotice={setNotice}
                  isOwner={me.role === 'owner'}
                  pid={pid}
                  onProvidersChanged={async () => {
                    if (!pid) return;
                    const next = await api(`/projects/${pid}/payment-providers`).catch(() => null);
                    if (next) setPaymentProviders(next);
                  }}
                />
              )}
              {section === 'connections' && (
                <ConnectionsVaultSection
                  api={api}
                  pid={pid}
                  busy={busy}
                  onBusy={act}
                  onNotice={setNotice}
                  isOwner={me.role === 'owner'}
                />
              )}
              {section === 'media' && (
                <MediaLibrarySection
                  api={api}
                  pid={pid}
                  busy={busy}
                  onBusy={act}
                  onNotice={setNotice}
                />
              )}
              {section === 'chains' && pid && (
                <ActionConstructorSection
                  api={api}
                  pid={pid}
                  branches={branches}
                  cartId={
                    cartId && (!cartForProject || cartForProject === pid) ? cartId : null
                  }
                  busy={busy}
                  onBusy={act}
                  onNotice={setNotice}
                  onOpenMediaLibrary={(nodeId) => {
                    setMediaPickNodeId(nodeId);
                    setCtorFocusNodeId(nodeId);
                    setMediaOverlay(true);
                    setError('');
                  }}
                  onOpenConnections={() => goSection('connections')}
                  focusNodeId={ctorFocusNodeId}
                  focusChainId={ctorFocusChainId}
                  pendingAssetId={ctorPendingAssetId}
                  pendingContentKind={ctorPendingContentKind}
                  onConsumedReturnAsset={() => {
                    setCtorPendingAssetId(null);
                    setCtorPendingContentKind(null);
                  }}
                />
              )}
              {section === 'chains' && mediaOverlay && (
                <div className="drawer-layer media-overlay-layer" role="dialog" aria-modal="true" aria-label="Медиатека CRM">
                  <button
                    type="button"
                    className="scrim"
                    aria-label="Закрыть медиатеку"
                    onClick={() => {
                      setMediaOverlay(false);
                      setMediaPickNodeId(null);
                    }}
                  />
                  <div className="drawer media-overlay-drawer">
                    <MediaLibrarySection
                      api={api}
                      pid={pid}
                      busy={busy}
                      onBusy={act}
                      onNotice={setNotice}
                      pickMode
                      returnNodeId={mediaPickNodeId || ctorFocusNodeId}
                      onPick={(assetId, item) => {
                        const nodeId = mediaPickNodeId || ctorFocusNodeId;
                        if (nodeId) setCtorFocusNodeId(nodeId);
                        setCtorPendingAssetId(assetId);
                        setCtorPendingContentKind(
                          item?.kind ? String(item.kind) : null,
                        );
                        setMediaOverlay(false);
                        setMediaPickNodeId(null);
                        setNotice(`Выбрано · ${assetId.slice(0, 8)}…`);
                      }}
                    />
                    <div className="row" style={{ marginTop: 12 }}>
                      <button
                        type="button"
                        className="link"
                        onClick={() => {
                          setMediaOverlay(false);
                          setMediaPickNodeId(null);
                        }}
                      >
                        ← К конструктору
                      </button>
                    </div>
                  </div>
                </div>
              )}
              {section === 'chains' && !pid && (
                <section className="panel" id="action-constructor" aria-label="Выбор проекта для конструктора">
                  <h2>Конструктор действий</h2>
                  <p className="muted">
                    Выберите проект в фильтре «ПРОЕКТ» слева или откройте карточку на доске проектов — затем
                    соберите цепочку на холсте конструктора.
                  </p>
                  <div className="row" style={{ gap: 10, marginTop: 12 }}>
                    <button type="button" className="primary" onClick={() => goSection('home')}>
                      Выбрать проект на доске
                    </button>
                  </div>
                </section>
              )}
              {section === 'analytics' && (
                <AnalyticsSection
                  report={analytics}
                  from={periodFrom}
                  to={periodTo}
                  onFrom={setPeriodFrom}
                  onTo={setPeriodTo}
                  busy={busy}
                  onLoad={() => void act(() => loadAnalytics())}
                />
              )}
              {section === 'campaigns' && (
                <section className="panel">
                  <div className="panel-title">
                    <h2>Рассылки / Email</h2>
                    <a href={voronkaOpsHref({ section: 'bots' })} target="_blank" rel="noreferrer">
                      Мессенджер-blasts → Боты / Ops ↗
                    </a>
                  </div>
                  {mailStatus && (
                    <div className="direction" style={{ marginBottom: 12 }}>
                      <div>
                        <b>Почта Mailu</b>
                        <small>
                          SMTP/домен: {mailStatus.smtp_configured ? 'настроен' : 'не настроен'} · секреты скрыты · {mailStatus.domain_note}
                        </small>
                      </div>
                      <a href={mailStatus.webmail_url || 'https://mail.vodopetov.ru/webmail/'} target="_blank" rel="noreferrer">
                        Webmail ↗
                      </a>
                    </div>
                  )}
                  <p className="muted">
                    Канал email — через SMTP Outbox. Текст или кружок из медиабанка для мессенджеров. Импорт контакта не подтверждает отправку.
                  </p>
                  {mediaAssets.length > 0 && (
                    <p className="fine">
                      Медиа для рассылки: {mediaAssets.filter((a) => !a.blocker).length} готовы,{' '}
                      {mediaAssets.filter((a) => a.blocker).length} с блокером.
                    </p>
                  )}
                  {campaigns.map((c) => (
                    <article className="campaign" key={c.id}>
                      <div className="row">
                        <h3>{c.title}</h3>
                        <span className="badge">{stateNames[c.state]}</span>
                        {c.asset_id && <span className="tag">media</span>}
                      </div>
                      <p>{c.text}</p>
                      {c.report?.asset_blocker && <p className="fine">Блокер медиа: {c.report.asset_blocker}</p>}
                      <p className="fine">
                        {JSON.stringify(c.delivery)} {c.report?.excluded?.length ? `Исключено: ${c.report.excluded.length}` : ''}
                      </p>
                      {c.problems?.length > 0 && (
                        <p className="fine">Проблемы доставки: {c.problems.length} (retry только с подтверждением риска дубликата)</p>
                      )}
                      {c.report?.recipients && (
                        <p className="fine">
                          Доступных диалогов: {c.report.recipients.filter((r: Row) => r.eligible).length} / {c.profile_ids.length}
                        </p>
                      )}
                      <div className="row">
                        {c.state === 'draft' && (
                          <>
                            <button
                              onClick={() =>
                                void act(async () => {
                                  const r = await api('/campaigns/' + c.id + '/preflight', 'POST');
                                  setNotice(r.ready ? 'Preflight OK' : `Preflight: ${r.asset_blocker || 'есть исключения'}`);
                                })
                              }
                            >
                              Preflight
                            </button>
                            <button
                              className="primary"
                              onClick={() =>
                                void act(async () => {
                                  await api('/campaigns/' + c.id + '/launch', 'POST');
                                  await load();
                                })
                              }
                            >
                              Запустить отправку
                            </button>
                          </>
                        )}
                        {['queued', 'paused'].includes(c.state) && (
                          <button
                            onClick={() =>
                              void act(async () => {
                                await api('/campaigns/' + c.id + '/control', 'POST', { action: c.state === 'paused' ? 'resume' : 'pause' });
                                await load();
                              })
                            }
                          >
                            {c.state === 'paused' ? 'Продолжить' : 'Пауза'}
                          </button>
                        )}
                        {c.problems?.length > 0 && (
                          <button
                            onClick={() =>
                              void act(async () => {
                                await api('/campaigns/' + c.id + '/retry', 'POST', { acknowledge_duplicate_risk: true });
                                await load();
                              })
                            }
                          >
                            Retry failed
                          </button>
                        )}
                        {c.state !== 'cancelled' && (
                          <button
                            onClick={() =>
                              void act(async () => {
                                await api('/campaigns/' + c.id + '/cancel', 'POST');
                                await load();
                              })
                            }
                          >
                            Остановить
                          </button>
                        )}
                      </div>
                    </article>
                  ))}
                  {!campaigns.length && <div className="empty">Кампаний пока нет</div>}
                </section>
              )}
              {section === 'imports' && (
                <div className="import-layout">
                  <section className="panel">
                    <h2>Тип импорта</h2>
                    <div className="actions" style={{ gap: 8, flexWrap: 'wrap' }}>
                      <button className={importKind === 'clients' ? 'primary' : ''} onClick={() => { setImportKind('clients'); setAudiencePreview(null); }}>
                        Клиенты (Profile)
                      </button>
                      <button className={importKind === 'audience' ? 'primary' : ''} onClick={() => { setImportKind('audience'); setPreview(null); }}>
                        Аудитория Person+Consent
                      </button>
                    </div>
                    <p className="fine">
                      {importKind === 'audience'
                        ? 'IMPORT-101: email/Telegram → Person + ContactPoint + Consent. Без marketing Consent адрес не sendable (send_allowed deny). Массовая отправка не выполняется.'
                        : 'Классический импорт клиентов в CRM Profile.'}
                    </p>
                  </section>
                  <section className="panel">
                    <h2>1. Подготовьте данные</h2>
                    <p className="muted">CSV до 5 МБ и 5000 строк. UTF-8 или Windows-1251. Разделитель определяется автоматически.</p>
                    {importKind === 'audience' && (
                      <>
                        <label>
                          Режим согласия
                          <select
                            value={audienceConsentMode}
                            onChange={(e) => {
                              setAudienceConsentMode(e.target.value as 'quarantine' | 'grant_marketing');
                              setAudiencePreview(null);
                            }}
                          >
                            <option value="quarantine">Quarantine — без marketing grant (по умолчанию)</option>
                            <option value="grant_marketing">Grant marketing — только с evidence</option>
                          </select>
                        </label>
                        <label>
                          Evidence (источник согласия / подтверждения базы)
                          <input
                            value={audienceEvidence}
                            onChange={(e) => {
                              setAudienceEvidence(e.target.value);
                              setAudiencePreview(null);
                            }}
                            placeholder="например: partner export 2026-11-19, confirmed opt-in"
                          />
                        </label>
                        {audiencePreview && (
                          <p className="fine">
                            Dry-run зафиксировал mode={String(audiencePreview.consent_mode)}. Смена mode/evidence сбрасывает preview — нужен новый dry-run.
                          </p>
                        )}
                      </>
                    )}
                    <label>
                      Файл CSV
                      <input
                        type="file"
                        accept=".csv,.txt"
                        onChange={(e) => {
                          setFile(e.target.files?.[0] || null);
                          setPreview(null);
                          setAudiencePreview(null);
                          setMapping({});
                        }}
                      />
                    </label>
                    <label>
                      Или вставьте таблицу с заголовками
                      <textarea
                        rows={7}
                        placeholder={
                          importKind === 'audience'
                            ? 'email;telegram_id;telegram_username;name\nanna@example.ru;1234;@anna;Анна'
                            : 'Имя;Email;Телефон\nАнна;anna@example.ru;+79991234567'
                        }
                        value={paste}
                        onChange={(e) => {
                          setPaste(e.target.value);
                          setFile(null);
                          setPreview(null);
                          setAudiencePreview(null);
                          setMapping({});
                        }}
                      />
                    </label>
                    <button
                      className="primary"
                      disabled={busy || (!file && !paste)}
                      onClick={() =>
                        void act(() => (importKind === 'audience' ? previewAudience({}) : previewFile({})))
                      }
                    >
                      {importKind === 'audience' ? 'Dry-run (подсчёт)' : 'Проверить и сопоставить'}
                    </button>
                    <p className="fine">
                      {importKind === 'audience'
                        ? 'Идемпотентность по email / telegram_id / username. Повтор того же файла не дублирует Person.'
                        : 'Имена и username не используются для автоматического слияния. Возможные дубли пропускаются без перезаписи.'}
                    </p>
                  </section>
                  {importKind === 'clients' && preview && (
                    <section className="panel">
                      <h2>2. Проверьте соответствия</h2>
                      <div className="mapping">
                        {[
                          ['name', 'Имя'],
                          ['email', 'Email'],
                          ['phone', 'Телефон'],
                          ['company', 'Компания'],
                          ['source', 'Источник'],
                          ['external_id', 'Внешний ID'],
                          ['username', 'Username'],
                          ['notes', 'Заметка'],
                        ].map(([field, label]) => (
                          <label key={field}>
                            {label}
                            <select value={mapping[field] || ''} onChange={(e) => setMapping({ ...mapping, [field]: e.target.value })}>
                              <option value="">Не импортировать</option>
                              {preview.headers.map((h: string) => (
                                <option key={h}>{h}</option>
                              ))}
                            </select>
                          </label>
                        ))}
                      </div>
                      <button onClick={() => void act(() => previewFile(Object.fromEntries(Object.entries(mapping).filter(([, v]) => v))))}>
                        Обновить предпросмотр
                      </button>
                      <p>
                        {preview.total} строк · ошибок: {preview.errors} · возможных дублей: {preview.possible_duplicates}
                      </p>
                      <div className="preview-scroll">
                        {preview.rows.map((r: Row) => (
                          <div className="preview-row" key={r.line}>
                            <b>
                              #{r.line} {r.data.name || r.data.email || r.data.username}
                            </b>
                            <small>
                              {r.data.email} {r.data.phone}
                            </small>
                            {r.status === 'possible_duplicate' && <small>Возможный дубль — будет пропущен</small>}
                            {r.error && <span className="danger-text">{r.error}</span>}
                          </div>
                        ))}
                      </div>
                      <button
                        className="primary"
                        disabled={busy || preview.state === 'done'}
                        onClick={() =>
                          void act(async () => {
                            const r = await api('/imports/' + preview.id + '/commit', 'POST');
                            setPreview({ ...preview, state: 'done', report: r });
                            setNotice(`Создано: ${r.created}. Пропущено: ${r.skipped}. Ошибок: ${r.errors.length}. Сообщения не отправлялись.`);
                            await load();
                          })
                        }
                      >
                        {preview.state === 'done' ? 'Партия импортирована' : '3. Импортировать в базу'}
                      </button>
                      {preview.report?.errors?.map((e: Row) => (
                        <p key={e.line} className="fine">
                          Строка {e.line}: {e.error}
                        </p>
                      ))}
                    </section>
                  )}
                  {importKind === 'audience' && audiencePreview && (
                    <section className="panel">
                      <h2>2. Dry-run результат</h2>
                      <div className="mapping">
                        {[
                          ['name', 'Имя'],
                          ['email', 'Email'],
                          ['telegram_id', 'Telegram ID'],
                          ['telegram_username', 'Telegram username'],
                        ].map(([field, label]) => (
                          <label key={field}>
                            {label}
                            <select value={mapping[field] || ''} onChange={(e) => setMapping({ ...mapping, [field]: e.target.value })}>
                              <option value="">Не импортировать</option>
                              {(audiencePreview.headers || []).map((h: string) => (
                                <option key={h}>{h}</option>
                              ))}
                            </select>
                          </label>
                        ))}
                      </div>
                      <button
                        onClick={() =>
                          void act(() => previewAudience(Object.fromEntries(Object.entries(mapping).filter(([, v]) => v))))
                        }
                      >
                        Обновить dry-run
                      </button>
                      <p>
                        Всего: {audiencePreview.counts?.total ?? 0} · persons+{' '}
                        {audiencePreview.counts?.created_persons ?? 0} / upd {audiencePreview.counts?.updated_persons ?? 0} · points+{' '}
                        {audiencePreview.counts?.created_contact_points ?? 0} · granted{' '}
                        {audiencePreview.counts?.consent_granted ?? 0} · quarantine {audiencePreview.counts?.quarantined ?? 0} ·
                        ошибок {audiencePreview.counts?.errors ?? 0}
                      </p>
                      <div className="preview-scroll">
                        {(audiencePreview.samples || []).map((r: Row) => (
                          <div className="preview-row" key={r.line}>
                            <b>
                              #{r.line} {r.email || r.telegram_id || r.telegram_username}
                            </b>
                            <small>
                              marketing send_allowed: {String(r.marketing_send_allowed)} ({r.marketing_reason})
                            </small>
                          </div>
                        ))}
                      </div>
                      <button
                        className="primary"
                        disabled={
                          busy ||
                          audiencePreview.state === 'done' ||
                          audiencePreview.consent_mode !== audienceConsentMode
                        }
                        onClick={() =>
                          void act(async () => {
                            if (audiencePreview.consent_mode !== audienceConsentMode) {
                              throw new Error('Mode изменился — повторите dry-run');
                            }
                            const data = new FormData();
                            data.append('consent_mode', audienceConsentMode);
                            data.append('consent_evidence', audienceEvidence);
                            const r = await api('/audience-imports/' + audiencePreview.id + '/commit', 'POST', data);
                            setAudiencePreview({ ...audiencePreview, state: 'done', ...r });
                            const c = r.counts || {};
                            setNotice(
                              `Аудитория: persons+${c.created_persons || 0}, granted=${c.consent_granted || 0}, quarantine=${c.quarantined || 0}. Рассылка не запускалась.`,
                            );
                            await load();
                          })
                        }
                      >
                        {audiencePreview.state === 'done' ? 'Партия аудитории записана' : '3. Записать Person + Consent'}
                      </button>
                      {(audiencePreview.errors || []).map((e: Row) => (
                        <p key={e.line} className="fine">
                          Строка {e.line}: {e.error}
                        </p>
                      ))}
                    </section>
                  )}
                </div>
              )}
              {section === 'settings' && (
                <div className="two-column">
                  <section className="panel">
                    <div className="panel-title">
                      <h2>Команда</h2>
                      {me.role === 'owner' && (
                        <button
                          onClick={() =>
                            setForm({
                              title: 'Новый сотрудник',
                              fields: [
                                { key: 'login', label: 'Логин', required: true },
                                { key: 'password', label: 'Начальный пароль, от 12 символов', type: 'password', required: true },
                                { key: 'email', label: 'Email', type: 'email' },
                                {
                                  key: 'role',
                                  label: 'Роль',
                                  type: 'select',
                                  options: [
                                    ['manager', 'Аккаунт-менеджер'],
                                    ['editor', 'Партнёр'],
                                  ],
                                },
                              ],
                              save: async (v) => {
                                await api('/staff', 'POST', { ...v, project_ids: [pid] });
                                await load();
                              },
                            })
                          }
                        >
                          + Сотрудник
                        </button>
                      )}
                    </div>
                    {staff.map((s) => (
                      <div className="direction" key={s.id}>
                        <span className="avatar">{String(s.login).slice(0, 1)}</span>
                        <div>
                          <b>{s.login}</b>
                          <small>
                            {s.role} · {s.active ? 'активен' : 'отключён'}
                          </small>
                        </div>
                        {me.role === 'owner' && s.active && s.id !== me.id && (
                          <button
                            onClick={() =>
                              void act(async () => {
                                await api('/staff/' + s.id + '/disable', 'POST');
                                await load();
                              })
                            }
                          >
                            Отключить
                          </button>
                        )}
                      </div>
                    ))}
                  </section>
                  <section className="panel">
                    <div className="panel-title">
                      <h2>Подключения</h2>
                      {me.role === 'owner' && (
                        <button
                          onClick={() =>
                            void act(async () => {
                              const r = await api(`/projects/${pid}/email-connection`, 'POST');
                              setNotice(r.configured ? 'Email-подключение добавлено' : 'Email-подключение добавлено. Настройте SMTP на сервере.');
                              await load();
                            })
                          }
                        >
                          + Email
                        </button>
                      )}
                    </div>
                    {(connectionHub.length ? connectionHub : overview.connections || []).map((c: Row) => (
                      <div className="direction" key={c.id}>
                        <b>{c.name}</b>
                        <span>{c.platform}{c.role ? ` · ${c.role}` : ''}</span>
                        {c.bot_username ? <span>@{String(c.bot_username)}</span> : null}
                        <span className={c.active ? 'badge good' : 'badge'}>{c.active ? 'Включено' : 'Выключено'}</span>
                        {c.webhook_preflight && (
                          <small>
                            webhook: {c.webhook_preflight.ok ? 'ok' : 'не готов'}
                            {c.last_error ? ` · err: ${c.last_error}` : ''}
                            {typeof c.outbox_pending === 'number' ? ` · outbox pending ${c.outbox_pending}` : ''}
                            {typeof c.outbox_failed_or_unknown === 'number' && c.outbox_failed_or_unknown
                              ? ` · fail ${c.outbox_failed_or_unknown}`
                              : ''}
                            {typeof c.course_invites_pending === 'number' && c.course_invites_pending > 0
                              ? ` · ждут /start ${c.course_invites_pending}`
                              : ''}
                            {c.media_writable === false ? ' · media RO' : ''}
                          </small>
                        )}
                        {c.platform === 'telegram' && c.course_invite_requires_start ? (
                          <small className="muted">
                            Invite в канал: только после /start у этого бота (любой лид с ContactPoint
                            telegram + marketing consent).
                          </small>
                        ) : null}
                        {c.capabilities?.video_note && (
                          <small>
                            video_note: {c.capabilities.video_note.available ? 'native' : 'недоступно'}
                          </small>
                        )}
                        {me.role === 'owner' && c.token_env && (
                          <small>env: {c.token_env}{c.token_present ? ' ✓' : ' ✗'} (значение скрыто)</small>
                        )}
                      </div>
                    ))}
                    <h3>Почта Mailu</h3>
                    {mailStatus ? (
                      <div className="direction">
                        <div>
                          <b>SMTP</b>
                          <small>{mailStatus.smtp_configured ? 'хост/from заданы' : 'не настроено'} · секреты скрыты</small>
                        </div>
                        <a href={mailStatus.webmail_url} target="_blank" rel="noreferrer">
                          Webmail ↗
                        </a>
                      </div>
                    ) : (
                      <p className="fine">Статус почты недоступен</p>
                    )}
                    <h3>Настройка компании</h3>
                    <p className="fine">
                      Согласия (transactional/service/marketing), чеклист Signal welcome/inquiry/access, путь AccessGrant.
                      Массовая production-отправка не включается.
                    </p>
                    {me.role === 'owner' && (
                      <button
                        className="primary"
                        onClick={() =>
                          void act(async () => {
                            const r = await api(`/projects/${pid}/company-setup`, 'POST');
                            setNotice(
                              r.ai_rule_created
                                ? 'Правила компании применены (AiRule создан). Проверьте Signal seed и AccessGrant path.'
                                : 'Правила компании обновлены. Signal seed и AccessGrant — по чеклисту.',
                            );
                          })
                        }
                      >
                        Применить defaults проекта
                      </button>
                    )}
                    <h3>Интеграции</h3>
                    {Object.entries(overview.integrations || {}).map(([key, value]) => (
                      <div className="direction" key={key}>
                        <b>{key.toUpperCase()}</b>
                        <small>
                          {value === true ? 'Параметры заданы; требуется проверка' : value === 'not_implemented' ? 'Адаптер LMS ещё не реализован' : 'Не настроено'}
                        </small>
                      </div>
                    ))}
                    <h3>Роли ↔ CMS-матрица</h3>
                    {cmsMatrix ? (
                      <>
                        <p className="fine">
                          SoR: {cmsMatrix.source_of_truth}. Синхронизация: {cmsMatrix.sync}.{' '}
                          <a href={cmsMatrix.cms_url} target="_blank" rel="noreferrer">
                            Открыть CMS ↗
                          </a>
                        </p>
                        <ul className="checklist">
                          {(cmsMatrix.checklist || []).map((item: Row) => (
                            <li key={item.id}>
                              <span className={item.done || item.auto ? 'badge good' : 'badge'}>
                                {item.done ? '✓' : item.auto ? 'CRM' : 'ручн.'}
                              </span>{' '}
                              {item.label}
                            </li>
                          ))}
                        </ul>
                        <p className="fine">Нет второй матрицы ученика в CRM.</p>
                      </>
                    ) : (
                      <p className="fine">Чеклист матрицы недоступен</p>
                    )}
                    <h3>ИИ (черновики)</h3>
                    <p className="fine">
                      Правила и провайдеры (RAGA / OpenRouter / Ollama) — на{' '}
                      <a href={voronkaOpsHref()} target="_blank" rel="noreferrer">
                        voronka.solo-pr.ru/ops/
                      </a>
                      . Автоответ клиенту без approve запрещён.
                    </p>
                    {aiStatus ? (
                      <div className="direction">
                        <small>
                          RAGA: {aiStatus.raga?.configured ? 'настроен' : 'не настроено'} · OpenRouter:{' '}
                          {aiStatus.openrouter?.configured ? 'настроен' : 'не настроено'} · Ollama:{' '}
                          {aiStatus.ollama?.configured ? 'настроен' : 'не настроено'} · автоотправка: нет
                        </small>
                      </div>
                    ) : (
                      <p className="fine">Статус ИИ недоступен</p>
                    )}
                    {overview.queue && (
                      <>
                        <h3>Очередь</h3>
                        <p className="fine">
                          pending {overview.queue.outbox_pending} · failed {overview.queue.outbox_failed} · unknown{' '}
                          {overview.queue.outbox_unknown} · worker_stale {String(overview.queue.worker_stale)}
                        </p>
                      </>
                    )}
                    <p className="fine">Секреты задаются на сервере. Наличие настроек не доказывает успешную доставку или доступность сервиса. Chatwoot/Espo не используются.</p>
                  </section>
                </div>
              )}
          </>
        </main>
      </div>
      {detail && (
        <div className="drawer-layer">
          <button className="scrim" aria-label="Закрыть фон карточки" onClick={() => setDetail(null)} />
          <aside className="drawer" role="dialog" aria-modal="true" aria-label="Карточка клиента">
            <div className="drawer-top">
              <span className="eyebrow">КАРТОЧКА КЛИЕНТА</span>
              <button aria-label="Закрыть карточку" onClick={() => setDetail(null)}>
                ✕
              </button>
            </div>
            <div className="contact-heading">
              <span className="avatar large">{detail.name.slice(0, 1)}</span>
              <h2>{detail.name}</h2>
              <p>{detail.company || 'Компания не указана'}</p>
            </div>
            <div className="row drawer-actions">
              <button onClick={() => contactForm(detail)}>Изменить</button>
              <button onClick={() => dealForm(detail)}>+ Сделка</button>
              <button onClick={() => taskForm(detail)}>+ Работа</button>
              <button disabled={!productOptions.length} onClick={() => orderForm(detail)}>
                + Заказ
              </button>
              {me.start_project_id &&
                detail.project_id === me.start_project_id &&
                !(detail.tags || []).includes('converted') &&
                !detail.extra?.converted_to_project_id && (
                  <button className="primary" onClick={() => convertLeadForm(detail)}>
                    В проект
                  </button>
                )}
            </div>
            <SignalPersonActions personId={typeof detail.person_id === 'string' ? detail.person_id : null} />
            <dl>
              <dt>Email</dt>
              <dd>{detail.email || '—'}</dd>
              <dt>Телефон</dt>
              <dd>{detail.phone || '—'}</dd>
              <dt>Источник</dt>
              <dd>
                {detail.source}
                {detail.is_purchaser ? ' · покупатель' : ''}
              </dd>
              <dt>Теги</dt>
              <dd>
                {(detail.tags || []).length
                  ? detail.tags.map((t: string) => (
                      <span key={t} className="tag">
                        {t}
                      </span>
                    ))
                  : '—'}
              </dd>
              <dt>Коммуникации</dt>
              <dd>{detail.stopped ? 'Отказ от сообщений' : 'Проверять доступность выбранного канала'}</dd>
            </dl>
            <h3>Идентичности</h3>
            {(detail.identities || []).length === 0 && <p className="fine">Нет привязанных диалогов</p>}
            {(detail.identities || []).map((i: Row) => (
              <div className="mini-card" key={i.id}>
                <b>{i.name || i.external_user_id}</b>
                <small>
                  {i.platform || 'channel'} · {i.connection_name || i.connection_id}
                </small>
              </div>
            ))}
            <h3>Посещения вебинаров (RO)</h3>
            {detail.learning?.available ? (
              <p>Есть события LMS</p>
            ) : (
              <p className="fine">{detail.learning?.note || 'Пока нет LMS/BBB events.'}</p>
            )}
            <h3>Последний контакт</h3>
            <p>{detail.activities[0]?.outcome || 'Результат ещё не зафиксирован'}</p>
            {detail.person_id && (
              <>
                <h3>Медиа Signal (soft-ref)</h3>
                <p className="fine">
                  Только Registry <code>asset_id</code> — без второй медиатеки CRM. Байты по запросу через
                  fetch-by-asset_id.
                </p>
                <div className="row">
                  <button
                    onClick={() =>
                      setForm({
                        title: 'Привязать media asset_id',
                        fields: [
                          {
                            key: 'signal_asset_id',
                            label: 'Signal / Registry asset_id',
                            required: true,
                          },
                          { key: 'name', label: 'Подпись (опц.)' },
                        ],
                        save: async (v) => {
                          await api(
                            `/projects/${pid}/persons/${detail.person_id}/media-refs`,
                            'POST',
                            {
                              signal_asset_id: v.signal_asset_id,
                              name: v.name || null,
                              ensure_delivery: false,
                            },
                          );
                          setNotice('Soft-ref сохранён. Файлы в CRM не загружаются.');
                          await refreshDetail();
                        },
                      })
                    }
                  >
                    + Soft-ref asset_id
                  </button>
                </div>
              </>
            )}
            <div className="row">
              <button
                onClick={() =>
                  setForm({
                    title: 'Зафиксировать контакт',
                    fields: [
                      { key: 'body', label: 'Содержание разговора', type: 'textarea', required: true },
                      { key: 'outcome', label: 'Результат / договорённость' },
                      {
                        key: 'channel',
                        label: 'Канал',
                        type: 'select',
                        options: [
                          ['manual', 'Заметка'],
                          ['phone', 'Телефон'],
                          ['meeting', 'Встреча'],
                          ['email', 'Email'],
                          ['telegram', 'Telegram'],
                          ['max', 'MAX'],
                          ['vk', 'ВК'],
                        ],
                      },
                    ],
                    save: async (v) => {
                      await api('/contacts/' + detail.id + '/activities', 'POST', v);
                      await refreshDetail();
                    },
                  })
                }
              >
                + Записать контакт
              </button>
              <button
                onClick={() =>
                  setForm({
                    title: 'Черновик с RAGA',
                    fields: [{ key: 'question', label: 'Что подготовить для клиента?', type: 'textarea', required: true }],
                    save: async (v) => {
                      await api('/contacts/' + detail.id + '/assistant', 'POST', v);
                      await refreshDetail();
                      setNotice('Черновик сохранён в истории. Клиенту ничего не отправлено.');
                    },
                  })
                }
              >
                Помощь ИИ
              </button>
            </div>
            <h3>Сделки и следующий шаг</h3>
            {detail.deals.map((d: Row) => (
              <div className="mini-card" key={d.id}>
                <b>{d.title}</b>
                <span className="tag">{me.stages[d.stage]}</span>
                <p>{d.next_action || 'Следующий шаг не назначен'}</p>
                <small>{date(d.due_at)}</small>
              </div>
            ))}
            <h3>Работа и обязательства</h3>
            {detail.tasks.map((t: Row) => (
              <div className="mini-card" key={t.id}>
                <b>{t.title}</b>
                <small>
                  {stateNames[t.state]} · {date(t.due_at)}
                </small>
                <p>{t.result}</p>
              </div>
            ))}
            <h3>Финансы</h3>
            {detail.orders.map((o: Row) => (
              <div className="mini-card" key={o.id}>
                <b>{o.snapshot.name}</b>
                <p>
                  Оплачено {money(o.paid_minor)} · осталось {money(o.balance_minor)}
                </p>
              </div>
            ))}
            {detail.identities.length > 0 && (
              <button
                onClick={() =>
                  setForm({
                    title: 'Ответ клиенту',
                    fields: [
                      {
                        key: 'identity_id',
                        label: 'Диалог',
                        type: 'select',
                        options: detail.identities.map((i: Row) => [i.id, i.name + ' · ' + i.connection_id]),
                      },
                      { key: 'text', label: 'Сообщение', type: 'textarea', required: true },
                    ],
                    save: async (v) => {
                      await api('/api/messages', 'POST', v);
                      setNotice('Ответ поставлен в очередь. Автоматический сценарий приостановлен.');
                      await refreshDetail();
                    },
                  })
                }
              >
                Ответить через подключение
              </button>
            )}
            {!!detail.llm_drafts?.length && (
              <>
                <h3>Черновики ИИ (сценарий)</h3>
                {detail.llm_drafts.map((draft: Row) => (
                  <div className="mini-card" key={draft.id}>
                    <b>{draft.response_status}</b>
                    <small>
                      {' '}
                      · {draft.mode} · rules {draft.rules_version} · {draft.model || draft.provider}
                    </small>
                    <p>{draft.draft_text}</p>
                    {['pending', 'ok', 'fallback'].includes(draft.response_status) && (
                      <div className="row" style={{ gap: 8, flexWrap: 'wrap' }}>
                        <button
                          className="primary"
                          onClick={() =>
                            setForm({
                              title: 'Утвердить черновик',
                              fields: [
                                {
                                  key: 'text',
                                  label: 'Текст',
                                  type: 'textarea',
                                  required: true,
                                  value: draft.draft_text || '',
                                },
                              ],
                              save: async (v) => {
                                await api('/llm-drafts/' + draft.id + '/approve', 'POST', v);
                                setNotice('Черновик утверждён и поставлен в очередь.');
                                await refreshDetail();
                              },
                            })
                          }
                        >
                          Утвердить
                        </button>
                        <button
                          onClick={() =>
                            void (async () => {
                              await api('/llm-drafts/' + draft.id + '/reject', 'POST', {});
                              setNotice('Черновик отклонён.');
                              await refreshDetail();
                            })()
                          }
                        >
                          Отклонить
                        </button>
                      </div>
                    )}
                  </div>
                ))}
              </>
            )}
            <div id="contact-history">
              <h3>История</h3>
              <p className="fine">
                Переписка и техника по Person: оплаты, доступы, согласия, приглашения, заметки.
                Фильтры Всё / Переписка / Техника (VR-452).
              </p>
              <HistoryTimeline detail={detail} api={api} projectId={pid || detail.project_id} />
            </div>
          </aside>
        </div>
      )}
      {form && (
        <FormModal
          form={form}
          busy={busy}
          error={error}
          onClose={() => setForm(null)}
          onSubmit={async (data) => {
            await act(async () => {
              await form.save(data);
              setForm(null);
            });
          }}
        />
      )}
      {helpOpen && <SystemHelpModal onClose={() => setHelpOpen(false)} />}
    </div>
  );
}
