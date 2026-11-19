'use client';

import {useEffect, useState} from 'react';

type Item={id:string;label:string;mark:string;site:string;admin?:string};
/** Static twin of Signal GET /api/v1/platform/services — SoR chrome pattern stays here (PR-V1 / PROD-013).
 * Mailu is intentionally absent from «Сервисы» (mailbox passwords ≠ SIGNAL SSO; signal#33 / PR-CAT0).
 */
const ITEMS:Item[]=[
 {id:'solo',label:'Соло-Пиар',mark:'СП',site:'https://solo-pr.ru/',admin:'https://cms.solo-pr.ru/admin'},
 // One promo entry only — no separate raga7/raga9/course tiles (owner 2026-11-19).
 {id:'raga',label:'Промо',mark:'ПР',site:'https://raga7.vodopetov.ru/'},
 {id:'wited',label:'wi-ted',mark:'WT',site:'https://wited.familypaper.online/',admin:'https://wited.familypaper.online/'},
 {id:'signal',label:'СИГНАЛ',mark:'СИ',site:'https://signal.vodopetov.ru/',admin:'https://signal.vodopetov.ru/?view=access'},
 {id:'crm',label:'CRM',mark:'CRM',site:'https://crm.solo-pr.ru/crm/',admin:'https://crm.solo-pr.ru/crm/'},
 {id:'ops',label:'Воронка',mark:'ВО',site:'https://voronka.solo-pr.ru/ops/',admin:'https://voronka.solo-pr.ru/ops/'},
 {id:'lms',label:'Обучение',mark:'LMS',site:'https://lms.solo-pr.ru/',admin:'https://lms.solo-pr.ru/'},
 {id:'personal',label:'Личный сайт',mark:'СВ',site:'https://vodopetov.ru/',admin:'https://vodopetov.ru/edit/'}
];

export type CrmRole = 'owner' | 'editor' | 'manager' | string;

/** Owner/editor ≈ Signal workspace_admin for Админка visibility (ACL chrome only). */
export function launcherShowsAdmin(role?: CrmRole | null): boolean {
  return role === 'owner' || role === 'editor';
}

/** Navigation only: cross-service access is granted by each app's own server-side ACL.
 * Keycloak is the SSO authority; do not share tokens or cookies using links.
 */
export default function SignalLauncher({role}:{role?: CrmRole | null}={}){
 const showAdmin=launcherShowsAdmin(role);
 const [mode,setMode]=useState<'site'|'admin'>('site');
 useEffect(()=>{
  if(!showAdmin && mode==='admin') setMode('site');
 },[showAdmin,mode]);
 const adminItems=ITEMS.filter(x=>x.admin);
 const shown=mode==='site'?ITEMS:adminItems;
 return <details className="signal-service-switcher">
   <summary aria-label="Приложения SIGNAL" title="Приложения SIGNAL">▦</summary>
   <div className="signal-service-popover">
     <strong>Приложения SIGNAL</strong>
     <div className="signal-service-tabs" role="group" aria-label="Разделы">
       <button type="button" aria-pressed={mode==='site'} onClick={()=>setMode('site')}>Сервисы</button>
       {showAdmin && (
         <button type="button" aria-pressed={mode==='admin'} onClick={()=>setMode('admin')}>Админка</button>
       )}
     </div>
     <div className="signal-service-grid">
       {shown.map(x=><a href={mode==='site'?x.site:x.admin} key={x.id}>
         <b aria-hidden="true">{x.mark}</b><span>{x.label}</span>
       </a>)}
     </div>
     <small>
       {showAdmin
         ? 'Административные права проверяются целевой системой. Пароль и токены не передаются.'
         : 'Админка скрыта без прав владельца или партнёра. Пароль и токены не передаются.'}
     </small>
   </div>
 </details>;
}
