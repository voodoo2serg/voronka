export const sections: [string, string][] = [
  ['home', 'Проекты'],
  ['overview', 'Обзор'],
  ['contacts', 'Клиенты'],
  ['inbox', 'Диалоги'],
  ['deals', 'Сделки'],
  ['products', 'Продукты'],
  ['tasks', 'Работа и сессии'],
  ['orders', 'Финансы'],
  ['payments', 'Платежи'],
  ['connections', 'Подключения'],
  ['media', 'Медиатека'],
  ['chains', 'Конструктор'],
  ['campaigns', 'Рассылки'],
  ['analytics', 'Аналитика'],
  ['imports', 'Импорт данных'],
  ['settings', 'Команда'],
];

/** Page sizes for clients list (CRM-A6): 10 / 50 / Все. */
export const CLIENT_PAGE_SIZES: { value: number; label: string }[] = [
  { value: 10, label: '10' },
  { value: 50, label: '50' },
  { value: 0, label: 'Все' },
];

export const stateNames: Record<string, string> = {
  new: 'Новый', open: 'Открыт', done: 'Готово', cancelled: 'Отменён', sent: 'Отправлено',
  pending: 'В очереди', queued: 'В очереди', draft: 'Черновик', paused: 'Пауза', failed: 'Ошибка',
  unknown: 'Результат неизвестен', payment: 'Оплата', refund: 'Возврат',
  prepared: 'Подготовлено', invited: 'Приглашён', started: 'Start бота', skipped: 'Пропущен',
  stopped: 'Отказ', completed: 'Завершено',
};

export const sectionHints: Record<string, string> = {
  home: 'Доска проектов: контакты, деньги, следующие задачи.',
  overview: 'Дашборд выбранного проекта: продажи, сделки, цепочки.',
  contacts: 'Клиенты проекта с пагинацией 10/50/Все и колонкой Branch.',
  inbox: 'Ответы по существующим диалогам подключений.',
  deals: 'От первого вопроса до выполненного обязательства.',
  products: 'Курсы, экспертные услуги и форматы участия.',
  tasks: 'Задачи, встречи и материалы, которые вы обещали.',
  orders: 'Заказы, авансы, рассрочки и подтверждённые поступления.',
  payments: 'Robokassa и ЮKassa: статус, webhook, оповещения. Учётные данные — в Подключениях.',
  connections: 'Connection Vault: боты, каналы, SMTP, Robokassa/ЮKassa. Секреты только ••••last4.',
  media: 'Медиатека проекта: список и создание в Signal SoR без ухода из CRM (B-02).',
  chains: 'Конструктор цепочки (#today): шаги из корзины CMS, медиатека в CRM, проверка email/TG.',
  campaigns: 'Email-кампании здесь; мессенджер-blasts — на voronka.solo-pr.ru/ops/.',
  analytics: 'Периодный отчёт: деньги, контакты, обучение (RO), коммуникации.',
  imports: 'Клиенты (Profile) или аудитория IMPORT-101: Person + ContactPoint + Consent.',
  settings: 'Люди проекта и согласование ролей с CMS. Каналы и SMTP — в Подключениях.',
};
