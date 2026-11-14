/** Staff portal URLs — mirrors app/portals.py (ops chrome). */

export const PORTAL_URLS = {
  public: 'https://solo-pr.ru',
  cms: 'https://cms.solo-pr.ru',
  crm: 'https://crm.solo-pr.ru',
  voronka: 'https://voronka.solo-pr.ru',
  lms: 'https://lms.solo-pr.ru',
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

export function miniAppHref(): string {
  return '/';
}
