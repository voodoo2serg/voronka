import type { Metadata } from 'next';
import './globals.css';
export const metadata: Metadata = { title: 'Соло-PR · Рабочий кабинет', description: 'Клиенты, решения и обучение' };
export default function Layout({ children }: { children: React.ReactNode }) { return <html lang="ru"><body>{children}</body></html>; }
