/** Staff portal URLs — mirrors solo_pr src/lib/portals.ts (CRM chrome). */

export const PORTAL_URLS = {
  public: 'https://solo-pr.ru',
  cms: 'https://cms.solo-pr.ru',
  crm: 'https://crm.solo-pr.ru',
  voronka: 'https://voronka.solo-pr.ru',
  lms: 'https://lms.solo-pr.ru',
  signal: 'https://signal.vodopetov.ru',
} as const;

/** Self-serve path when SSO works but CRM staff row / project is missing. */
export const ACCESS_REQUEST = {
  signal: PORTAL_URLS.signal + '/?view=access',
  cmsStaff: PORTAL_URLS.cms + '/admin',
  label: 'Запросить доступ',
} as const;

export const MAILU_WEBMAIL = 'https://mail.vodopetov.ru/webmail/';

/** FUNC-L4: post-grant open-course handoff (lms_end /open → MyCourses). */
export function lmsOpenCourseHref(): string {
  return PORTAL_URLS.lms + '/open';
}

/** FUNC-L1/L3 contour stage labels (lead → mail). */
export const CONTOUR_STAGES: { id: string; label: string }[] = [
  { id: 'lead', label: 'Лид' },
  { id: 'touch', label: 'Касание' },
  { id: 'pay', label: 'Оплата' },
  { id: 'access', label: 'Доступ' },
  { id: 'course', label: 'Курс' },
  { id: 'mail', label: 'Письмо' },
];

/** Deep-link into Signal studio — no UUID paste into a form (FUNC-L3). */
export function signalHref(opts: {
  view?: string;
  doc?: string;
  person?: string | null;
  brand?: string | null;
  set?: string | null;
} = {}): string {
  const q = new URLSearchParams();
  if (opts.view) q.set('view', opts.view);
  if (opts.doc) q.set('doc', opts.doc);
  if (opts.person) q.set('person', opts.person);
  if (opts.brand) q.set('brand', opts.brand);
  if (opts.set) q.set('set', opts.set);
  const qs = q.toString();
  return PORTAL_URLS.signal + '/' + (qs ? `?${qs}` : '');
}

/** CRM-A15: Constructor «Медиатека» — SSO preserves return_to (Signal PR #57). */
export const SIGNAL_MEDIA_LIBRARY_URL = PORTAL_URLS.signal + '/?view=media';

export function signalMediaLibraryHref(opts: {
  brand?: string | null;
  person?: string | null;
  project?: string | null;
} = {}): string {
  const base = signalHref({ view: 'media', brand: opts.brand, person: opts.person });
  if (!opts.project) return base;
  const sep = base.includes('?') ? '&' : '?';
  return `${base}${sep}project=${encodeURIComponent(opts.project)}`;
}

/**
 * @deprecated CRM-R2: CMS cart is no longer a staff path. Kept for ops/deep-link compat only.
 * Canonical route was `/#/admin/cart/<project_id>` or `/#/admin/cart`.
 */
export function cmsChainCartHref(projectId?: string | null): string {
  const path = projectId
    ? `/#/admin/cart/${encodeURIComponent(projectId)}`
    : '/#/admin/cart';
  return `${PORTAL_URLS.cms}${path}`;
}

/** Open Signal CE (регламенты / campaigns) or message-set for a Person. */
export function signalPersonLinks(personId: string | null | undefined) {
  if (!personId) {
    return {
      ce: signalHref({ view: 'campaigns' }),
      sets: signalHref({ view: 'sets' }),
      hasPerson: false as const,
    };
  }
  return {
    ce: signalHref({ view: 'campaigns', person: personId }),
    sets: signalHref({ view: 'sets', person: personId }),
    hasPerson: true as const,
  };
}

export const CONTACT_SOURCE_FILTERS: [string, string][] = [
  ['', 'Все источники'],
  ['telegram', 'Telegram'],
  ['max', 'MAX'],
  ['site', 'Сайт'],
  ['import', 'Импорт'],
  ['purchase', 'Покупатели'],
];

export function isCrmHost(host = typeof window !== 'undefined' ? window.location.host : ''): boolean {
  const h = String(host || '')
    .split(':')[0]
    .toLowerCase();
  return h === 'crm.solo-pr.ru' || h.startsWith('crm.');
}

export function voronkaOpsHref(opts: { project?: string | null; section?: string } = {}): string {
  let base: string;
  if (typeof window === 'undefined') base = PORTAL_URLS.voronka + '/ops/';
  else if (isCrmHost()) base = PORTAL_URLS.voronka + '/ops/';
  else base = '/ops/';
  const q = new URLSearchParams();
  if (opts.project) q.set('project', opts.project);
  const qs = q.toString();
  const hash = opts.section ? `#${opts.section}` : '';
  return base.replace(/\/?$/, '/') + (qs ? `?${qs}` : '') + hash;
}

/** Vault telegram_bot row → ops Боты (Activate webhook) with project context. */
export function voronkaOpsBotsHref(project?: string | null): string {
  return voronkaOpsHref({ project, section: 'bots' });
}
