"""Production Next.js export exercised against the real FastAPI CRM via TestClient."""
import mimetypes
import os
from pathlib import Path
from urllib.parse import urlparse
import pytest
from test_crm import crm
from test_operational import system

pytestmark = pytest.mark.skipif(os.getenv('RUN_BROWSER') != '1', reason='Browser job only')


@pytest.mark.parametrize('width',[1440,390])
def test_browser_login_create_contact_deal_and_import(crm,width):
    from playwright.sync_api import sync_playwright, expect
    root=Path('web/out').resolve()
    if not root.is_dir(): pytest.fail('Run npm ci && npm run build in web first')
    errors=[]
    crm.client.headers.pop('Authorization',None)
    def serve(route):
        req=route.request
        path=urlparse(req.url).path
        if path.startswith('/api/'):
            headers={k:v for k,v in req.headers.items() if k.lower() in {'authorization','content-type'}}
            response=crm.client.request(req.method,req.url.split('crm.test',1)[1],headers=headers,content=req.post_data_buffer)
            route.fulfill(status=response.status_code,body=response.content,headers={'Content-Type':response.headers.get('content-type','application/json')})
            return
        relative=path.removeprefix('/crm/').lstrip('/')
        file=root/relative
        if file.is_dir(): file=file/'index.html'
        if not file.is_file(): route.fulfill(status=404,body='Not found');return
        route.fulfill(body=file.read_bytes(),content_type=mimetypes.guess_type(str(file))[0] or 'application/octet-stream')
    with sync_playwright() as p:
        browser=p.chromium.launch(executable_path=os.getenv('PLAYWRIGHT_EXECUTABLE_PATH') or None,args=['--no-sandbox','--disable-dev-shm-usage','--disable-gpu','--no-zygote'])
        page=browser.new_page(viewport={'width':width,'height':1000})
        page.on('pageerror',lambda e:errors.append(str(e)))
        page.route('http://crm.test/**',serve)
        page.goto('http://crm.test/crm/')
        page.get_by_label('Логин',exact=True).fill('owner')
        page.get_by_label('Пароль',exact=True).fill('owner-password-123')
        page.get_by_role('button',name='Войти в кабинет',exact=True).click()
        expect(page.get_by_role('heading',name='Обзор',exact=True)).to_be_visible()
        # On mobile the sidebar labels are visually compact but remain accessible.
        page.get_by_role('button',name='Клиенты',exact=False).first.click()
        page.get_by_role('button',name='+ Новый клиент',exact=True).click()
        dialog=page.get_by_role('dialog')
        dialog.get_by_label('Имя',exact=True).fill('Анна Тестовая')
        dialog.get_by_label('Email',exact=True).fill('anna@example.ru')
        dialog.get_by_label('Компания',exact=True).fill('Медиа-команда')
        dialog.get_by_role('button',name='Сохранить',exact=True).click()
        expect(page.get_by_role('heading',name='Анна Тестовая',exact=True)).to_be_visible()
        page.get_by_role('button',name='+ Сделка',exact=True).click()
        dialog=page.get_by_role('dialog')
        dialog.get_by_label('Задача клиента',exact=True).fill('Коммуникационный аудит')
        dialog.get_by_label('Следующее действие',exact=True).fill('Согласовать бриф')
        dialog.get_by_role('button',name='Сохранить',exact=True).click()
        expect(page.get_by_text('Согласовать бриф',exact=True)).to_be_visible()
        page.get_by_role('button',name='Закрыть карточку',exact=True).click()
        page.get_by_role('button',name='Импорт данных',exact=False).first.click()
        page.get_by_label('Или вставьте таблицу с заголовками',exact=True).fill('Имя;Email\nПётр Импорт;petya@example.ru')
        page.get_by_role('button',name='Проверить и сопоставить',exact=True).click()
        expect(page.get_by_role('heading',name='2. Проверьте соответствия')).to_be_visible()
        page.get_by_role('button',name='3. Импортировать в базу',exact=True).click()
        expect(page.get_by_role('status')).to_contain_text('Создано: 1')
        page.get_by_role('button',name='Клиенты',exact=False).first.click()
        expect(page.get_by_role('button',name='Пётр Импорт',exact=False)).to_be_visible()
        page.screenshot(path=str(Path(os.getenv('CRM_SCREENSHOT_DIR','/tmp'))/f'crm-{width}.png'),full_page=True)
        assert page.evaluate('document.documentElement.scrollWidth <= innerWidth'), 'Page overflows horizontally'
        assert not errors,errors
        browser.close()
