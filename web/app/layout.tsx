import type { Metadata } from 'next';
import './globals.css';

const portal = process.env.NEXT_PUBLIC_PORTAL || 'crm';

export const metadata: Metadata =
  portal === 'ops'
    ? { title: 'Соло-PR · Voronka', description: 'Боты, сценарии, рассылки, поддержка и AI' }
    : { title: 'Соло-PR · CRM', description: 'Клиенты, сделки и платежи' };

export default function Layout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="ru">
      <body>{children}</body>
    </html>
  );
}
