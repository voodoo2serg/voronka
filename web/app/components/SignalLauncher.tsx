'use client';

import {useState} from 'react';

type Item={id:string;label:string;mark:string;site:string;admin?:string};
const ITEMS:Item[]=[
 {id:'solo',label:'Соло-Пиар',mark:'СП',site:'https://solo-pr.ru/',admin:'https://cms.solo-pr.ru/admin'},
 {id:'raga',label:'РАГА',mark:'РА',site:'https://raga.vodopetov.ru/'},
 {id:'raga7',label:'Кабинет RAGA7',mark:'R7',site:'https://raga7.vodopetov.ru/'},
 {id:'wited',label:'wi-ted',mark:'WT',site:'https://wi-ted.solo-pr.ru/'},
 {id:'signal',label:'СИГНАЛ',mark:'СИ',site:'https://signal.vodopetov.ru/'},
 {id:'crm',label:'CRM',mark:'CRM',site:'https://crm.solo-pr.ru/crm/',admin:'https://crm.solo-pr.ru/crm/'},
 {id:'ops',label:'Воронка',mark:'ВО',site:'https://voronka.solo-pr.ru/ops/',admin:'https://voronka.solo-pr.ru/ops/'},
 {id:'lms',label:'Обучение',mark:'LMS',site:'https://lms.solo-pr.ru/',admin:'https://lms.solo-pr.ru/'},
 {id:'personal',label:'Личный сайт',mark:'СВ',site:'https://vodopetov.ru/',admin:'https://vodopetov.ru/edit/'}
];

/** Navigation only: cross-service access is granted by each app's own server-side ACL.
 * Keycloak is the SSO authority; do not share tokens or cookies using links.
 */
export default function SignalLauncher(){
 const [mode,setMode]=useState<'site'|'admin'>('site');
 const shown=mode==='site'?ITEMS:ITEMS.filter(x=>x.admin);
 return <details className="signal-service-switcher">
   <summary aria-label="Приложения SIGNAL" title="Приложения SIGNAL">▦</summary>
   <div className="signal-service-popover">
     <strong>Приложения SIGNAL</strong>
     <div className="signal-service-tabs" role="group" aria-label="Разделы">
       <button type="button" aria-pressed={mode==='site'} onClick={()=>setMode('site')}>Сервисы</button>
       <button type="button" aria-pressed={mode==='admin'} onClick={()=>setMode('admin')}>Админка</button>
     </div>
     <div className="signal-service-grid">
       {shown.map(x=><a href={mode==='site'?x.site:x.admin} key={x.id}>
         <b aria-hidden="true">{x.mark}</b><span>{x.label}</span>
       </a>)}
     </div>
     <small>Административные права проверяются целевой системой. Пароль и токены не передаются.</small>
   </div>
 </details>;
}
