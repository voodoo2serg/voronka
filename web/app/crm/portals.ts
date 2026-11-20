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

export function voronkaOpsHref(): string {
  if (typeof window === 'undefined') return PORTAL_URLS.voronka + '/ops/';
  if (isCrmHost()) return PORTAL_URLS.voronka + '/ops/';
  return '/ops/';
}
