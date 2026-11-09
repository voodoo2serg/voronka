export const sections: [string, string][] = [
  ['overview', 'Обзор'],
  ['contacts', 'Клиенты'],
  ['inbox', 'Диалоги'],
  ['deals', 'Сделки'],
  ['products', 'Продукты'],
  ['tasks', 'Работа и сессии'],
  ['orders', 'Финансы'],
  ['campaigns', 'Рассылки'],
  ['outreach', 'Прогрев'],
  ['imports', 'Импорт данных'],
  ['settings', 'Команда и подключения'],
];

export const stateNames: Record<string, string> = {
  new: 'Новый', open: 'Открыт', done: 'Готово', cancelled: 'Отменён', sent: 'Отправлено',
  pending: 'В очереди', queued: 'В очереди', draft: 'Черновик', paused: 'Пауза', failed: 'Ошибка',
  unknown: 'Результат неизвестен', payment: 'Оплата', refund: 'Возврат',
  prepared: 'Подготовлено', invited: 'Приглашён', started: 'Start бота', skipped: 'Пропущен',
  stopped: 'Отказ', completed: 'Завершено',
};

export const sectionHints: Record<string, string> = {
  overview: 'Фокус на следующем действии и результате работы.',
  contacts: 'Вся история отношений с клиентом.',
  inbox: 'Ответы по существующим диалогам подключений.',
  deals: 'От первого вопроса до выполненного обязательства.',
  products: 'Курсы, экспертные услуги и форматы участия.',
  tasks: 'Задачи, встречи и материалы, которые вы обещали.',
  orders: 'Заказы, авансы, рассрочки и подтверждённые поступления.',
  campaigns: 'Управляемые сообщения по существующим подключениям.',
  outreach: 'Прогрев по телефону: dry_run с ссылкой на официальный бот, без Campaign/Outbox.',
  imports: 'Превратите старые базы в рабочие данные.',
  settings: 'Люди, каналы и состояние интеграций.',
};
