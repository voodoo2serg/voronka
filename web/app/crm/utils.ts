export const money = (n: number = 0) =>
  new Intl.NumberFormat('ru-RU', { style: 'currency', currency: 'RUB', maximumFractionDigits: 2 }).format(n / 100);

export const date = (v?: string) =>
  v ? new Date(v).toLocaleString('ru-RU', { dateStyle: 'short', timeStyle: 'short' }) : '—';

export const toMinor = (s: string) => Math.round(Number(s.replace(',', '.')) * 100);

export const names = (items: Record<string, any>[], key = 'name'): [string, string][] =>
  items.map((x) => [String(x.id), x[key] || String(x.id)]);
