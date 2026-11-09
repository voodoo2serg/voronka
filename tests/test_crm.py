"""CRM acceptance tests: authorization, imports, finance and durable messages."""
import hashlib
import io
import os
from datetime import timedelta
from unittest.mock import patch
import pytest
from sqlalchemy import select
from test_operational import system
from app import crm_api, auth, runtime, worker
from app.db import Project, Connection, Admin, Contact, Identity, Outbox, Event, now
from app.crm_models import Account, Profile, Payment, Campaign, AccessGrant, Order, Invoice
from app.crm_service import password_hash


@pytest.fixture
def crm(system, monkeypatch):
    from app import crm_deps
    from app.crm_routers import auth_staff, contacts, commerce, campaigns, outreach
    for mod in (crm_api, crm_deps, auth_staff, contacts, commerce, campaigns, outreach):
        monkeypatch.setattr(mod, 'Session', system.db)
    with system.db.begin() as db:
        db.add(Project(id='p', name='Соло-PR', goal='Продажи'))
        db.add(Project(id='secret', name='Секрет', goal='Закрытый проект'))
        db.flush()
        db.get(Connection, 'tg').project_id = 'p'
        db.get(Connection, 'other').project_id = 'secret'
        db.add(Account(id='owner-account', admin_id=123, login='owner', password_hash=password_hash('owner-password-123'),email='owner@example.ru'))
    return system


def contact(crm, name='Анна', **values):
    r=crm.client.post('/api/crm/projects/p/contacts',json={'name':name, **values})
    assert r.status_code==200,r.text
    return r.json()


def product(crm, amount=100000, **values):
    r=crm.client.post('/api/crm/projects/p/products',json={'name':'Аудит','kind':'audit','price_minor':amount, **values})
    assert r.status_code==200,r.text
    return r.json()


def order(crm, person=None, item=None, **values):
    person=person or contact(crm);item=item or product(crm)
    r=crm.client.post('/api/crm/projects/p/orders',json={'profile_id':person['id'],'product_id':item['id'],'request_key':'request-order-001', **values})
    assert r.status_code==200,r.text
    return r.json()


def test_browser_login_revocation_and_password_not_exposed(crm):
    client=crm.client
    assert client.post('/api/crm/login',json={'login':'owner','password':'bad'}).status_code==401
    r=client.post('/api/crm/login',json={'login':'owner','password':'owner-password-123'})
    assert r.status_code==200
    client.headers['Authorization']='Bearer '+r.json()['token']
    assert client.get('/api/crm/me').status_code==200
    assert 'password' not in str(client.get('/api/crm/staff').json())
    assert client.post('/api/crm/logout').status_code==200
    assert client.get('/api/crm/me').status_code==401


def test_staff_project_scope_all_object_endpoints(crm):
    person=contact(crm);item=product(crm);o=order(crm,person,item)
    assert crm.client.post('/api/crm/staff',json={'login':'limited','password':'limited-password-12','project_ids':['secret']}).status_code==200
    r=crm.client.post('/api/crm/login',json={'login':'limited','password':'limited-password-12'})
    crm.client.headers['Authorization']='Bearer '+r.json()['token']
    for path in ['/api/crm/projects/p/contacts','/api/crm/projects/p/export','/api/crm/contacts/'+person['id'], '/api/crm/projects/p/orders', '/api/crm/orders/'+o['id']+'/access?resource=x&capability=video.play']:
        assert crm.client.get(path).status_code==404,path
    assert [p['id'] for p in crm.client.get('/api/projects').json()]==['secret']
    assert 'password_hash' not in str(crm.client.get('/api/crm/staff').json())


def test_contact_revision_and_distinct_deals(crm):
    c=contact(crm)
    a=crm.client.post('/api/crm/projects/p/deals',json={'profile_id':c['id'],'title':'Аудит','next_action':'Согласовать бриф'}).json()
    b=crm.client.post('/api/crm/projects/p/deals',json={'profile_id':c['id'],'title':'Менторство'}).json()
    data={'stage':'proposal','revision':1,'next_action':'Отправить предложение'}
    assert crm.client.patch('/api/crm/deals/'+a['id'],json=data).status_code==200
    assert crm.client.patch('/api/crm/deals/'+a['id'],json=data).status_code==409
    detail=crm.client.get('/api/crm/contacts/'+c['id']).json()
    assert {d['id']:d['stage'] for d in detail['deals']}=={a['id']:'proposal',b['id']:'new'}
    assert crm.client.post('/api/crm/contacts/'+c['id']+'/activities',json={'body':'Обсудили бюджет','outcome':'Готов к встрече'}).status_code==200
    assert crm.client.get('/api/crm/projects/p/contacts').json()['items'][0]['last_contact']['outcome']=='Готов к встрече'


def test_import_preview_mapping_encoding_duplicates_and_replay(crm):
    raw='ФИО;Почта;telegram_id\nАнна;Anna@EXAMPLE.RU;1234\nДубль;anna@example.ru;9999\nОшибка;bad-email;3'.encode('cp1251')
    r=crm.client.post('/api/crm/projects/p/imports/preview',files={'file':('old.csv',raw)})
    assert r.status_code==200,r.text
    p=r.json();assert p['total']==3 and p['errors']==1
    assert crm.client.get('/api/crm/projects/p/contacts').json()['total']==0
    endpoint='/api/crm/imports/'+p['id']+'/commit'
    report=crm.client.post(endpoint).json()
    assert report['created']==1 and report['skipped']==1
    assert crm.client.post(endpoint).json()==report
    assert crm.client.post('/api/crm/projects/p/imports/preview',files={'file':('again.csv',raw)}).json()['id']==p['id']
    with crm.db() as db:
        assert db.scalar(select(Profile)).email=='anna@example.ru'
        assert not list(db.scalars(select(Identity)))
        assert not list(db.scalars(select(Outbox)))


def test_export_prevents_spreadsheet_formula_and_scope(crm):
    contact(crm,name='=HYPERLINK("evil")')
    r=crm.client.get('/api/crm/projects/p/export')
    assert r.status_code==200 and "'=HYPERLINK" in r.text
    assert 'password_hash' not in r.text


def test_money_partial_refund_replay_and_certificate_gate(crm):
    o=order(crm)
    base='/api/crm/orders/'+o['id']
    for capability in ['video.play','certificate.issue']:
        assert crm.client.post(base+'/grants',json={'resource':'audit','capability':capability,'min_paid_percent':25}).status_code==200
    assert not crm.client.get(base+'/access?resource=audit&capability=video.play').json()['allow']
    payment={'amount_minor':25000,'external_id':'bank-operation-1','evidence':'Выписка №1'}
    assert crm.client.post(base+'/payments',json=payment).json()['balance_minor']==75000
    assert crm.client.post(base+'/payments',json=payment).json()['balance_minor']==75000
    assert crm.client.get(base+'/access?resource=audit&capability=video.play').json()['allow']
    assert not crm.client.get(base+'/access?resource=audit&capability=certificate.issue').json()['allow']
    assert crm.client.post(base+'/academic',json={'complete':True,'evidence':'Все задания проверены'}).status_code==200
    assert not crm.client.get(base+'/access?resource=audit&capability=certificate.issue').json()['allow']
    payment.update(amount_minor=75000,external_id='bank-operation-2')
    assert crm.client.post(base+'/payments',json=payment).json()['fully_paid']
    assert crm.client.get(base+'/access?resource=audit&capability=certificate.issue').json()['allow']
    payment.update(kind='refund',amount_minor=50000,external_id='refund-operation-1')
    assert crm.client.post(base+'/payments',json=payment).json()['balance_minor']==50000
    assert not crm.client.get(base+'/access?resource=audit&capability=certificate.issue').json()['allow']
    payment.update(amount_minor=100000,external_id='refund-operation-2')
    assert crm.client.post(base+'/payments',json=payment).status_code==422


def test_future_installment_not_overdue_and_snapshot_is_immutable(crm):
    p=product(crm)
    o=order(crm,item=p,installments=[{'due_at':(now()+timedelta(days=10)).isoformat(),'amount_minor':100000}])
    assert o['overdue_minor']==0 and o['balance_minor']==100000
    assert crm.client.put('/api/crm/products/'+p['id'],json={'name':'Другой тариф','kind':'audit','price_minor':200000}).status_code==200
    assert crm.client.get('/api/crm/projects/p/orders').json()[0]['total_minor']==100000


def test_promocode_capacity_and_cancel_release(crm):
    person=contact(crm);item=product(crm)
    assert crm.client.post('/api/crm/projects/p/promos',json={'code':'HELLO','percent':20,'max_uses':1}).status_code==200
    o=order(crm,person,item,promo_code='hello')
    assert o['total_minor']==80000
    data={'profile_id':person['id'],'product_id':item['id'],'request_key':'request-order-002','promo_code':'HELLO'}
    assert crm.client.post('/api/crm/projects/p/orders',json=data).status_code==409
    assert crm.client.post('/api/crm/orders/'+o['id']+'/cancel').status_code==200
    assert crm.client.post('/api/crm/projects/p/orders',json=data).status_code==200


def test_robokassa_signature_test_mode_and_repeated_callback(crm,monkeypatch):
    monkeypatch.setenv('ROBOKASSA_LOGIN','merchant')
    monkeypatch.setenv('ROBOKASSA_PASSWORD1','first')
    monkeypatch.setenv('ROBOKASSA_PASSWORD2','second')
    monkeypatch.setenv('ROBOKASSA_TEST_PASSWORD1','test-first')
    monkeypatch.setenv('ROBOKASSA_TEST_PASSWORD2','test-second')
    monkeypatch.setenv('ROBOKASSA_HASH','sha256')
    monkeypatch.setenv('ROBOKASSA_TEST','1')
    o=order(crm)
    r=crm.client.post('/api/crm/orders/'+o['id']+'/robokassa')
    assert r.status_code==200
    with crm.db() as db: invoice=db.get(Invoice,r.json()['invoice_id']);number=invoice.number
    data={'OutSum':'1000.00','InvId':number,'SignatureValue':'wrong'}
    assert crm.client.post('/api/crm/robokassa/result',data=data).status_code==403
    data['SignatureValue']=hashlib.sha256(f'1000.00:{number}:test-second'.encode()).hexdigest()
    for _ in range(2): assert crm.client.post('/api/crm/robokassa/result',data=data).text=='OK'+number
    assert crm.client.get('/api/crm/projects/p/orders').json()[0]['paid_minor']==0
    # A separately created live invoice uses the same verifier, but creates money only once.
    with crm.db.begin() as db: db.add(Invoice(id='live',order_id=o['id'],number='54321',amount_minor=100000,test=False))
    data['InvId']='54321'; data['SignatureValue']=hashlib.sha256(b'1000.00:54321:second').hexdigest()
    for _ in range(2): assert crm.client.post('/api/crm/robokassa/result',data=data).text=='OK54321'
    assert crm.client.get('/api/crm/projects/p/orders').json()[0]['paid_minor']==100000
    with crm.db() as db: assert len(list(db.scalars(select(Payment))))==1


def test_campaign_excludes_imported_ids_replay_pause_and_optout(crm):
    a=contact(crm,name='Реальный диалог');b=contact(crm,name='Только импорт',extra={'telegram_id':'777'})
    with crm.db.begin() as db:
        db.add(Identity(id='known',contact_id=a['contact_id'],connection_id='tg',external_user_id='900',chat_id='900',name='Анна'))
    r=crm.client.post('/api/crm/projects/p/campaigns',json={'connection_id':'tg','title':'Приветствие','text':'Здравствуйте, {name}','profile_ids':[a['id'],b['id']]})
    assert r.status_code==200,r.text
    cid=r.json()['id']; assert sum(x['eligible'] for x in r.json()['report']['recipients'])==1
    assert crm.client.post('/api/crm/campaigns/'+cid+'/launch').json()['queued']==1
    assert crm.client.post('/api/crm/campaigns/'+cid+'/launch').json()['queued']==1
    with crm.db() as db: assert len(list(db.scalars(select(Outbox))))==1
    assert crm.client.post('/api/crm/campaigns/'+cid+'/control',json={'action':'pause'}).status_code==200
    with crm.db() as db: assert db.scalar(select(Outbox)).status=='held'
    assert crm.client.post('/api/crm/campaigns/'+cid+'/control',json={'action':'resume'}).status_code==200
    with crm.db.begin() as db: db.get(Profile,a['id']).stopped=True
    with patch('app.worker.send') as send:
        assert worker.outgoing()
        send.assert_not_called()
    with crm.db() as db: assert db.scalar(select(Outbox)).status=='cancelled'


def test_registration_not_exposed_and_recovery_requires_verified_email(crm,monkeypatch):
    assert crm.client.post('/api/crm/register',json={}).status_code==404
    monkeypatch.setenv('SMTP_HOST','mail.test')
    monkeypatch.setenv('SMTP_FROM','team@example.ru')
    codes=[]
    from app.crm_routers import auth_staff
    monkeypatch.setattr(auth_staff,'mail_code',lambda email,code:codes.append(code))
    monkeypatch.setattr(crm_api,'mail_code',lambda email,code:codes.append(code))
    assert crm.client.post('/api/crm/recovery/start',json={'login':'owner'}).status_code==200
    assert not codes
    assert crm.client.post('/api/crm/recovery/start',json={'login':'owner','purpose':'verify'}).status_code==200
    assert len(codes)==1
    assert crm.client.post('/api/crm/recovery/finish',json={'login':'owner','purpose':'verify','code':codes[-1]}).status_code==200
    assert crm.client.post('/api/crm/recovery/start',json={'login':'owner'}).status_code==200
    data={'login':'owner','code':codes[-1],'password':'new-password-1234'}
    assert crm.client.post('/api/crm/recovery/finish',json=data).status_code==200
    assert crm.client.post('/api/crm/recovery/finish',json=data).status_code==400
    assert crm.client.get('/api/crm/me').status_code==401


def test_raga_unconfigured_has_no_fake_success(crm):
    c=contact(crm)
    r=crm.client.post('/api/crm/contacts/'+c['id']+'/assistant',json={'question':'Подготовь ответ'})
    assert r.status_code==503


def test_migration_0004_matches_models_and_downgrades(crm):
    import importlib.util
    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    from sqlalchemy import inspect
    from app.db import Base
    bind=crm.db.kw['bind']
    if bind.dialect.name!='sqlite': pytest.skip('Uses isolated SQLite metadata, PG upgrade tested in CI')
    # Tables added after 0004 are covered by later migration tests.
    later={'crm_outreach_batches','crm_outreach_recipients'}
    with bind.begin() as conn:
        for table in reversed(Base.metadata.sorted_tables):
            if table.name.startswith('crm_'): table.drop(conn)
        spec=importlib.util.spec_from_file_location('crm_migration','migrations/versions/0004_crm_workspace.py')
        module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
        with Operations.context(MigrationContext.configure(conn)):
            module.upgrade()
            inspector=inspect(conn)
            for table in Base.metadata.sorted_tables:
                if table.name.startswith('crm_') and table.name not in later:
                    assert set(c.name for c in table.columns)=={c['name'] for c in inspector.get_columns(table.name)}
            module.downgrade()
        assert not any(n.startswith('crm_') for n in inspect(conn).get_table_names())


def test_migration_0006_outreach_matches_models_and_downgrades(crm):
    import importlib.util
    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    from sqlalchemy import inspect
    from app.db import Base
    bind=crm.db.kw['bind']
    if bind.dialect.name!='sqlite': pytest.skip('Uses isolated SQLite metadata, PG upgrade tested in CI')
    outreach={'crm_outreach_batches','crm_outreach_recipients'}
    with bind.begin() as conn:
        for table in reversed(Base.metadata.sorted_tables):
            if table.name in outreach: table.drop(conn, checkfirst=True)
        # 0006 assumes baseline CRM tables from create_all / prior migrations.
        spec=importlib.util.spec_from_file_location('outreach_migration','migrations/versions/0006_outreach_batches.py')
        module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
        with Operations.context(MigrationContext.configure(conn)):
            module.upgrade()
            inspector=inspect(conn)
            for table in Base.metadata.sorted_tables:
                if table.name in outreach:
                    assert set(c.name for c in table.columns)=={c['name'] for c in inspector.get_columns(table.name)}
            module.downgrade()
        assert not outreach.intersection(inspect(conn).get_table_names())


def test_email_requires_permission_and_uses_real_smtp_transport(crm,monkeypatch):
    from app.channels import send
    from types import SimpleNamespace
    a=contact(crm,email='anna@example.ru')
    connection=crm.client.post('/api/crm/projects/p/email-connection').json()['id']
    assert connection not in [c['id'] for c in crm.client.get('/api/connections').json()]
    data={'connection_id':connection,'title':'Знакомство','text':'Привет, {name}','profile_ids':[a['id']]}
    preview=crm.client.post('/api/crm/projects/p/campaigns',json=data).json()
    assert not preview['report']['recipients'][0]['eligible']
    values={k:a[k] for k in ['name','email','phone','company','source','tags','extra','owner_id','stopped','revision']}
    values['extra']={'email_allowed':True}
    assert crm.client.put('/api/crm/contacts/'+a['id'],json=values).status_code==200
    campaign=crm.client.post('/api/crm/projects/p/campaigns',json=data).json()
    assert campaign['report']['recipients'][0]['eligible']
    assert crm.client.post('/api/crm/campaigns/'+campaign['id']+'/launch').json()['queued']==1
    monkeypatch.setenv('SMTP_HOST','smtp.test');monkeypatch.setenv('SMTP_FROM','team@example.ru')
    with patch('smtplib.SMTP') as smtp:
        with crm.db() as db: conn=db.get(Connection,connection);out=db.scalar(select(Outbox))
        receipt=send(conn,out.chat_id,out.payload,out.id)
        assert receipt
        message=smtp.return_value.__enter__.return_value.send_message.call_args.args[0]
        assert message['To']=='anna@example.ru' and 'Анна' in message.get_content()


def test_manager_cannot_change_catalog_prices_or_record_money(crm):
    c=contact(crm);p=product(crm);o=order(crm,c,p)
    assert crm.client.post('/api/crm/staff',json={'login':'manager','password':'manager-password-123','project_ids':['p']}).status_code==200
    mid=next(s['id'] for s in crm.client.get('/api/crm/staff').json() if s['login']=='manager')
    with crm.db.begin() as db: db.get(Profile,c['id']).owner_id=mid
    login=crm.client.post('/api/crm/login',json={'login':'manager','password':'manager-password-123'}).json()
    crm.client.headers['Authorization']='Bearer '+login['token']
    assert crm.client.put('/api/crm/products/'+p['id'],json={'name':'Цена','kind':'audit','price_minor':1}).status_code==403
    assert crm.client.post('/api/crm/orders/'+o['id']+'/payments',json={'amount_minor':100000,'external_id':'bank-1234','evidence':'Не подтверждено'}).status_code==403
    assert crm.client.get('/api/crm/projects/p/contacts').status_code==200
    assert crm.client.post('/api/crm/projects/p/orders',json={'profile_id':c['id'],'product_id':p['id'],'request_key':'another-order','agreed_price_minor':1,'price_reason':'Индивидуальные условия'}).status_code==403


def test_individual_service_price_requires_reason(crm):
    c=contact(crm);p=product(crm)
    data={'profile_id':c['id'],'product_id':p['id'],'request_key':'service-quote-1','agreed_price_minor':250000}
    assert crm.client.post('/api/crm/projects/p/orders',json=data).status_code==422
    data['price_reason']='Согласован расширенный аудит'
    r=crm.client.post('/api/crm/projects/p/orders',json=data)
    assert r.status_code==200 and r.json()['total_minor']==250000


def test_raga_draft_is_scoped_and_never_sends(crm,monkeypatch):
    import httpx,json
    c=contact(crm)
    monkeypatch.setenv('RAG_API_URL','https://raga.test/api/gateway/v1/chat/completions')
    monkeypatch.setenv('RAG_API_TOKEN','server-secret')
    monkeypatch.setenv('RAG_PROJECT_MODELS',json.dumps({'p':'solo-sales'}))
    with patch('httpx.post') as post:
        post.return_value=httpx.Response(200,json={'choices':[{'message':{'content':'Черновик ответа'}}]},request=httpx.Request('POST','https://raga.test'))
        r=crm.client.post('/api/crm/contacts/'+c['id']+'/assistant',json={'question':'Как начать аудит?'})
        assert r.status_code==200 and not r.json()['sent']
        assert post.call_args.kwargs['json']['model']=='solo-sales'
    with crm.db() as db: assert not list(db.scalars(select(Outbox)))


def test_staff_cannot_create_owner_role(crm):
    r=crm.client.post('/api/crm/staff',json={'login':'second-owner','password':'second-owner-12','role':'owner','project_ids':['p']})
    assert r.status_code==422


def test_disable_staff_revokes_redis_sessions(crm):
    assert crm.client.post('/api/crm/staff',json={'login':'to-disable','password':'disable-password-12','project_ids':['p']}).status_code==200
    login=crm.client.post('/api/crm/login',json={'login':'to-disable','password':'disable-password-12'})
    assert login.status_code==200
    token=login.json()['token']
    assert crm.cache.get(auth.session_key(token))
    staff=crm.client.get('/api/crm/staff').json()
    aid=next(s['id'] for s in staff if s['login']=='to-disable')
    assert crm.client.post(f'/api/crm/staff/{aid}/disable').status_code==200
    assert crm.cache.get(auth.session_key(token)) is None
    limited=crm.client.__class__(crm.client.app)
    limited.headers['Authorization']='Bearer '+token
    assert limited.get('/api/crm/me').status_code in (401,403)
    limited.close()


def test_manager_sees_only_assigned_contacts_deals_and_export(crm):
    mine=contact(crm,name='Мой клиент')
    other=contact(crm,name='Чужой клиент')
    assert crm.client.post('/api/crm/staff',json={'login':'acl-manager','password':'manager-password-123','role':'manager','project_ids':['p']}).status_code==200
    staff=crm.client.get('/api/crm/staff').json()
    mid=next(s['id'] for s in staff if s['login']=='acl-manager')
    with crm.db.begin() as db:
        db.get(Profile,mine['id']).owner_id=mid
        db.get(Profile,other['id']).owner_id=123
    deal_mine=crm.client.post('/api/crm/projects/p/deals',json={'profile_id':mine['id'],'title':'Моя сделка','owner_id':mid}).json()
    crm.client.post('/api/crm/projects/p/deals',json={'profile_id':other['id'],'title':'Чужая сделка','owner_id':123})
    login=crm.client.post('/api/crm/login',json={'login':'acl-manager','password':'manager-password-123'}).json()
    crm.client.headers['Authorization']='Bearer '+login['token']
    listed=crm.client.get('/api/crm/projects/p/contacts').json()
    assert listed['total']==1 and listed['items'][0]['id']==mine['id']
    assert crm.client.get('/api/crm/contacts/'+other['id']).status_code==404
    deals=crm.client.get('/api/crm/projects/p/deals').json()
    assert [d['id'] for d in deals]==[deal_mine['id']]
    export=crm.client.get('/api/crm/projects/p/export')
    assert export.status_code==200 and 'Мой клиент' in export.text and 'Чужой клиент' not in export.text
    assert crm.client.post('/api/crm/projects/p/campaigns',json={'connection_id':'tg','title':'x','text':'hi','profile_ids':[other['id']]}).status_code==404


def test_import_clears_rows_after_commit_keeps_fingerprint(crm):
    from app.crm_models import ImportBatch
    raw='name;email\nБорис;boris@example.ru'.encode()
    p=crm.client.post('/api/crm/projects/p/imports/preview',files={'file':('b.csv',raw)}).json()
    report=crm.client.post('/api/crm/imports/'+p['id']+'/commit').json()
    assert report['created']==1
    with crm.db() as db:
        batch=db.get(ImportBatch,p['id'])
        assert batch.state=='done' and batch.rows==[]
    assert crm.client.post('/api/crm/projects/p/imports/preview',files={'file':('b.csv',raw)}).json()['id']==p['id']
    assert crm.client.post('/api/crm/imports/'+p['id']+'/commit').json()==report


def test_robokassa_overview_uses_test_passwords_in_test_mode(crm,monkeypatch):
    monkeypatch.setenv('ROBOKASSA_LOGIN','merchant')
    monkeypatch.setenv('ROBOKASSA_TEST','1')
    monkeypatch.delenv('ROBOKASSA_PASSWORD1',raising=False)
    monkeypatch.delenv('ROBOKASSA_PASSWORD2',raising=False)
    monkeypatch.setenv('ROBOKASSA_TEST_PASSWORD1','test-first')
    monkeypatch.setenv('ROBOKASSA_TEST_PASSWORD2','test-second')
    assert crm.client.get('/api/crm/projects/p/overview').json()['integrations']['robokassa'] is True
    monkeypatch.delenv('ROBOKASSA_TEST_PASSWORD1',raising=False)
    assert crm.client.get('/api/crm/projects/p/overview').json()['integrations']['robokassa'] is False


def test_login_sets_httponly_session_cookie(crm):
    r=crm.client.post('/api/crm/login',json={'login':'owner','password':'owner-password-123'})
    assert r.status_code==200
    assert auth.CRM_SESSION_COOKIE in r.cookies
    bare=crm.client.__class__(crm.client.app)
    bare.cookies.set(auth.CRM_SESSION_COOKIE,r.cookies[auth.CRM_SESSION_COOKIE])
    assert bare.get('/api/crm/me').status_code==200
    bare.close()


def test_inbox_lists_dialog_identities(crm):
    a=contact(crm,name='С диалогом')
    contact(crm,name='Без диалога')
    with crm.db.begin() as db:
        db.add(Identity(id='inbox-id',contact_id=a['contact_id'],connection_id='tg',external_user_id='901',chat_id='901',name='Анна'))
        db.add(Event(id='evt1',connection_id='tg',contact_id=a['contact_id'],kind='message.received',payload={'text':'Привет'}))
    r=crm.client.get('/api/crm/projects/p/inbox')
    assert r.status_code==200
    assert r.json()['total']==1 and r.json()['items'][0]['id']==a['id']
    assert r.json()['items'][0]['identities']


def _published_entry(crm):
    from test_operational import graph
    r=crm.client.post('/api/funnels',json={'name':'Outreach','connection_ids':['tg'],'graph':graph(),'project_id':'p'})
    assert r.status_code==200,r.text
    fid=r.json()['id']
    assert crm.client.post('/api/funnels/'+fid+'/publish').status_code==200
    entry=crm.client.post('/api/entries',json={'funnel_id':fid,'connection_id':'tg','attribution':{'source':'outreach'}}).json()
    return entry['payload']


def test_outreach_dry_run_respects_phone_optout_cap_and_skips_outbox(crm):
    from app.crm_models import OutreachRecipient, Activity
    token=_published_entry(crm)
    ok=contact(crm,name='С телефоном',phone='+79991234567')
    no_phone=contact(crm,name='Без телефона')
    stopped=contact(crm,name='Отказ',phone='+79997654321',stopped=True)
    only_import=contact(crm,name='Импорт ID',phone='+79990001122',extra={'telegram_id':'777'})
    body={
        'title':'Прогрев MAX/TG','invite_text':'Здравствуйте, {name}! Бот: {link}',
        'profile_ids':[ok['id'],no_phone['id'],stopped['id'],only_import['id']],
        'entry_token':token,'entry_connection_id':'tg','daily_cap':2,'mode':'dry_run','transport':'manual',
    }
    created=crm.client.post('/api/crm/projects/p/outreach-batches',json=body)
    assert created.status_code==200,created.text
    batch=created.json(); bid=batch['id']
    preview={row['profile_id']:row for row in batch['report']['preview']}
    assert preview[ok['id']]['eligible'] and preview[only_import['id']]['eligible']
    assert not preview[no_phone['id']]['eligible'] and not preview[stopped['id']]['eligible']
    prepared=crm.client.post('/api/crm/outreach-batches/'+bid+'/prepare')
    assert prepared.status_code==200,prepared.text
    report=prepared.json()
    assert report['outbox_used'] is False and report['mode']=='dry_run'
    assert report['prepared']==2  # daily_cap; stopped/no_phone skipped, fourth hits cap or is prepared
    with crm.db() as db:
        assert not list(db.scalars(select(Outbox)))
        states={r.profile_id:r.state for r in db.scalars(select(OutreachRecipient).where(OutreachRecipient.batch_id==bid))}
        assert states[ok['id']]=='prepared'
        assert states[stopped['id']]=='stopped'
        assert states[no_phone['id']]=='skipped'
        assert 't.me/real_bot?start='+token in db.scalar(
            select(OutreachRecipient).where(OutreachRecipient.profile_id==ok['id'])).bot_link
        assert db.scalar(select(Activity).where(Activity.profile_id==ok['id'],Activity.kind=='outreach'))
        # Imported telegram_id still has no Identity — phone invite ≠ write permission.
        assert not db.scalar(select(Identity).where(Identity.contact_id==only_import['contact_id']))
    detail=crm.client.get('/api/crm/outreach-batches/'+bid).json()
    recipient=next(r for r in detail['recipients'] if r['profile_id']==ok['id'])
    assert crm.client.post(
        f"/api/crm/outreach-batches/{bid}/recipients/{recipient['id']}/outcome",
        json={'state':'invited','note':'Отправлено с отдельной SIM'},
    ).status_code==200
    # After official Start, same contact_id marks recipient started and lands in inbox.
    with crm.db.begin() as db:
        db.add(Identity(id='after-start',contact_id=ok['contact_id'],connection_id='tg',
                        external_user_id='910',chat_id='910',name='С телефоном'))
    from app.outreach import mark_started_for_identity
    with crm.db.begin() as db:
        identity=db.get(Identity,'after-start'); connection=db.get(Connection,'tg')
        assert mark_started_for_identity(db,identity,connection)==1
    with crm.db() as db:
        assert db.scalar(select(OutreachRecipient).where(OutreachRecipient.profile_id==ok['id'])).state=='started'
    inbox=crm.client.get('/api/crm/projects/p/inbox').json()
    assert any(item['id']==ok['id'] for item in inbox['items'])


def test_outreach_live_pymax_disabled_without_credentials(crm):
    token=_published_entry(crm)
    person=contact(crm,name='Live stub',phone='+79991112233')
    batch=crm.client.post('/api/crm/projects/p/outreach-batches',json={
        'title':'Live stub','invite_text':'Hi {name} {link}','profile_ids':[person['id']],
        'entry_token':token,'entry_connection_id':'tg','mode':'live','transport':'pymax','daily_cap':20,
    }).json()
    report=crm.client.post('/api/crm/outreach-batches/'+batch['id']+'/prepare').json()
    assert report['prepared']==0 and report['errors']
    assert report['errors'][0]['error']=='pymax_disabled'
    with crm.db() as db:
        assert not list(db.scalars(select(Outbox)))


def test_outreach_does_not_use_available_identity_campaign_path(crm):
    """Warm-up must remain separate from Campaign eligibility/Outbox launch."""
    from app.crm_service import available_identity
    token=_published_entry(crm)
    person=contact(crm,name='Только телефон',phone='+79995556677',extra={'telegram_id':'888'})
    with crm.db() as db:
        profile=db.get(Profile,person['id'])
        identity,reason=available_identity(db,profile,'tg')
        assert identity is None
        assert 'Нет подтверждённого диалога' in reason
    batch=crm.client.post('/api/crm/projects/p/outreach-batches',json={
        'title':'Не кампания','invite_text':'{link}','profile_ids':[person['id']],
        'entry_token':token,'entry_connection_id':'tg','daily_cap':20,
    }).json()
    assert crm.client.post('/api/crm/outreach-batches/'+batch['id']+'/prepare').json()['prepared']==1
    campaign=crm.client.post('/api/crm/projects/p/campaigns',json={
        'connection_id':'tg','title':'Обычная','text':'Hi','profile_ids':[person['id']],
    }).json()
    assert campaign['report']['recipients'][0]['eligible'] is False
    assert crm.client.post('/api/crm/campaigns/'+campaign['id']+'/launch').json()['queued']==0
    with crm.db() as db:
        assert not list(db.scalars(select(Outbox)))
