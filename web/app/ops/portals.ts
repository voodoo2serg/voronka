/** Staff portal URLs — mirrors app/portals.py (ops chrome). */

export const PORTAL_URLS = {
  public: 'https://solo-pr.ru',
  cms: 'https://cms.solo-pr.ru',
  crm: 'https://crm.solo-pr.ru',
  voronka: 'https://voronka.solo-pr.ru',
  lms: 'https://lms.solo-pr.ru',
  signal: 'https://signal.vodopetov.ru',
} as const;

/** Self-serve path when SSO works but staff/role is missing (AUTH-L2). */
export const ACCESS_REQUEST = {
  signal: PORTAL_URLS.signal + '/?view=access',
  cmsStaff: PORTAL_URLS.cms + '/admin',
  label: 'Запросить доступ',
} as const;

export function isVoronkaHost(host = typeof window !== 'undefined' ? window.location.host : ''): boolean {
  const h = String(host || '')
    .split(':')[0]
    .toLowerCase();
  return h === 'voronka.solo-pr.ru' || h.startsWith('voronka.');
}

export function crmHref(): string {
  if (typeof window === 'undefined') return PORTAL_URLS.crm + '/';
  if (isVoronkaHost()) return PORTAL_URLS.crm + '/';
  return '/crm/';
}

/** CRM «работа сегодня» landing (sales contour on overview) for ops chrome CTA. */
export function crmTodayHref(): string {
  return crmHref().replace(/\/?$/, '/') + '#today';
}

export function miniAppHref(): string {
  return '/';
}
