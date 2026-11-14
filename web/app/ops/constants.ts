export const sections: [string, string][] = [
  ['bots', 'Боты'],
  ['scenarios', 'Сценарии'],
  ['broadcasts', 'Рассылки'],
  ['support', 'Поддержка'],
  ['automations', 'Автоматизации'],
  ['media', 'Медиа'],
  ['ai', 'AI'],
];

export const sectionHints: Record<string, string> = {
  bots: 'Connection: platform, role, webhook, last error, media_writable, capabilities.',
  scenarios: 'Funnel/Version по боту. Полный граф — Mini App editor без поломок.',
  broadcasts: 'Мгновенная рассылка: бот, Identity-аудитория, текст и/или кружок.',
  support: 'Меню → ветки → эскалация менеджеру. Без Chatwoot.',
  automations: 'Мастер: канал → wait N дней → личное DM.',
  media: 'Медиабанк и приём кружков (сервис-бот / user-bot forward).',
  ai: 'RAGA / OpenRouter / Ollama. Draft-only, без автоответа клиенту.',
};

export const stateNames: Record<string, string> = {
  draft: 'Черновик',
  queued: 'В очереди',
  sent: 'Отправлено',
  paused: 'Пауза',
  failed: 'Ошибка',
  cancelled: 'Отменён',
  pending: 'В очереди',
};
