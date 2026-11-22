import type { Metadata } from 'next';
import './globals.css';
import './styles/staff-chrome.css';

const portal = process.env.NEXT_PUBLIC_PORTAL || 'crm';

export const metadata: Metadata =
  portal === 'ops'
    ? { title: 'Соло-PR · Voronka', description: 'Боты, сценарии, рассылки, поддержка и AI' }
    : { title: 'Соло-PR · CRM', description: 'Клиенты, сделки и платежи' };

export default function Layout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="ru">
      <head>
        <link rel="preconnect" href="https://fonts.googleapis.com" />
        <link rel="preconnect" href="https://fonts.gstatic.com" crossOrigin="anonymous" />
        <link
          href="https://fonts.googleapis.com/css2?family=Manrope:wght@400;600;700&family=Literata:opsz,wght@7..72,500;7..72,700&display=swap"
          rel="stylesheet"
        />
      </head>
      <body>{children}</body>
    </html>
  );
}
