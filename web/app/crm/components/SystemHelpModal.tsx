'use client';

import { sections, sectionHints } from '../constants';
import { PORTAL_URLS } from '../portals';

/** In-app tester help — authed staff chrome only (VR-381). */
export function SystemHelpModal({ onClose }: { onClose: () => void }) {
  return (
    <div className="modal-layer" onClick={onClose}>
      <section
        role="dialog"
        aria-modal="true"
        aria-labelledby="system-help-title"
        className="modal system-help-modal"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="panel-title">
          <h2 id="system-help-title">О системе</h2>
          <button type="button" aria-label="Закрыть справку" onClick={onClose}>
            ✕
          </button>
        </div>

        <div className="system-help-body">
          <section>
            <h3>Общее описание</h3>
            <p>
              <strong>Voronka CRM</strong> — sales HQ стека Solo-PR:{' '}
              <a href={PORTAL_URLS.crm + '/crm/'} target="_blank" rel="noreferrer">
                crm.solo-pr.ru
              </a>
              . Здесь живут <strong>Person</strong>, деньги (заказ/оплата),{' '}
              <strong>AccessGrant</strong> и операторская <strong>История</strong>. Вход только через
              Keycloak (
              <a href="https://auth.solo-pr.ru/" target="_blank" rel="noreferrer">
                auth.solo-pr.ru
              </a>
              ).
            </p>
            <p>
              Соседние продукты: Signal (
              <a href={PORTAL_URLS.signal} target="_blank" rel="noreferrer">
                signal.vodopetov.ru
              </a>
              ) — CE и медиатека; LMS (
              <a href={PORTAL_URLS.lms} target="_blank" rel="noreferrer">
                lms.solo-pr.ru
              </a>
              ) / OpenOlat (
              <a href="https://learn.solo-pr.ru/" target="_blank" rel="noreferrer">
                learn.solo-pr.ru
              </a>
              ) — курс; Solo-PR CMS (
              <a href={PORTAL_URLS.public} target="_blank" rel="noreferrer">
                solo-pr.ru
              </a>
              ) — сайт. ▦ в шапке переключает сервисы без передачи токенов. Ops-боты:{' '}
              <a href={PORTAL_URLS.voronka + '/ops/'} target="_blank" rel="noreferrer">
                voronka.solo-pr.ru/ops
              </a>{' '}
              (не браузерный SSO-вход CRM).
            </p>
          </section>

          <section>
            <h3>Главное меню</h3>
            <p className="fine">Левая навигация CRM (после входа). Каждый пункт — отдельный рабочий раздел.</p>
            <dl className="system-help-menu">
              {sections.map(([id, label]) => (
                <div key={id}>
                  <dt>{label}</dt>
                  <dd>{sectionHints[id] || 'Раздел CRM.'}</dd>
                </div>
              ))}
            </dl>
            <ul className="system-help-chrome">
              <li>
                <strong>▦ Сервисы</strong> — каталог платформы (сайт / админка по роли). Не передаёт
                пароли и токены.
              </li>
              <li>
                <strong>Выйти</strong> — завершает сессию CRM (BFF logout).
              </li>
              <li>
                <strong>Работа сегодня / History</strong> — быстрые ссылки в полосе шапки: фокус на
                следующем действии и переход к клиентам / карточке с Историей.
              </li>
              <li>
                <strong>ПРОЕКТ · фильтр</strong> — ограничивает списки выбранным проектом (или «все»,
                если проектов нет).
              </li>
            </ul>
          </section>

          <section>
            <h3>Ключевые функции</h3>
            <ol className="system-help-keys">
              <li>
                <strong>Person</strong> — единый человек в CRM (SoR контактов/учеников). Карточка
                клиента в drawer: профиль, точки контакта, OIDC-связь после SSO ученика. Email не
                мержит разных людей.
              </li>
              <li>
                <strong>Продукт → курс LMS</strong> — в «Продукты» → «+ Продукт» / «Изменить» поле{' '}
                <em>Курс LMS · externalId</em> пишется в <code>config.course_id</code>. При «Право
                доступа» на заказе ключ ресурса подставляется из этой привязки.
              </li>
              <li>
                <strong>AccessGrant</strong> — право на курс после оплаты/заказа. CRM выдаёт грант →
                outbox в LMS/Signal; ученик открывает курс через{' '}
                <a href={PORTAL_URLS.lms + '/open'} target="_blank" rel="noreferrer">
                  lms.solo-pr.ru/open
                </a>
                .
              </li>
              <li>
                <strong>History</strong> — лента на карточке клиента: стадии{' '}
                <em>Оплата → Письмо → Открыть курс → Курс</em> (и касания). Sandbox-оплата тоже
                видна без SSH.
              </li>
              <li>
                <strong>Оплата → курс</strong> — заказ/платёж → AccessGrant → письмо/CE → в Истории
                стадии Оплата / Письмо / «Открыть курс»; прогресс OpenOlat возвращается как{' '}
                <code>learning.*</code> в ту же History.
              </li>
            </ol>
            <p className="fine">
              Smoke для тестера: SSO в CRM → клиент с грантом → History показывает Оплату и «Открыть
              курс» → deep-link в LMS/OpenOlat.
            </p>
          </section>
        </div>

        <div className="modal-footer">
          <button type="button" className="primary" onClick={onClose}>
            Закрыть
          </button>
        </div>
      </section>
    </div>
  );
}
