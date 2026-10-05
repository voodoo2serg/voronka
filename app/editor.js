"use strict";
const $=id=>document.getElementById(id);
let token="", role="", connections=[], funnels=[], assets=[], templates=[], projects=[], current=null, selected=null, dirty=false, previewURL=null;
const types={text:"Текст",video:"Видео",video_note:"Кружок",voice:"Голос",photo:"Фото",document:"PDF",
 question:"Голосование",survey:"Опрос",condition:"Условие",wait:"Ожидание",grant:"Выдать доступ",stage:"Этап CRM",task:"Задача",goal:"Цель",finish:"Завершить"};
const roles={bot:"Бот, который отвечает",seller:"Продающий аккаунт",chat:"Чат",channel:"Канал"};
function node(tag,text,cls){const n=document.createElement(tag);if(text!==undefined)n.textContent=text;if(cls)n.className=cls;return n;}
function status(message){$("status").textContent=message;}
function selectedValues(id){return [...$(id).selectedOptions].map(o=>o.value);}
function options(select,values,chosen){select.replaceChildren();for(const [value,label] of values){const o=node("option",label);o.value=value;o.selected=Array.isArray(chosen)?chosen.includes(value):chosen===value;select.append(o);}}
function button(text,action,cls){const b=node("button",text,cls);b.onclick=async()=>{b.disabled=true;try{await action();}catch(e){status(e.message);}finally{b.disabled=false;}};return b;}
function bind(id,fn){$(id).onclick=async()=>{const b=$(id);b.disabled=true;try{await fn();}catch(e){status(e.message);}finally{b.disabled=false;}};}
async function api(path,method="GET",body){
 const headers={Authorization:"Bearer "+token};
 const init={method,headers};
 if(body instanceof FormData)init.body=body;else if(body!==undefined){headers["Content-Type"]="application/json";init.body=JSON.stringify(body);}
 const response=await fetch(path,init);const data=await response.json();
 if(!response.ok)throw Error(typeof data.detail==="string"?data.detail:JSON.stringify(data.detail));
 return data;
}
function switchPage(id){for(const p of document.querySelectorAll(".page"))p.hidden=p.id!==id;}
function markDirty(){dirty=true;$("json").value=JSON.stringify(current.graph,null,2);}
function resetEditor(funnel){
 current=structuredClone(funnel);selected=current.graph.start;dirty=false;
 $("builder").hidden=false;$("funnelName").value=current.name;
 options($("funnelConnections"),connections.map(c=>[c.id,c.name]),current.connection_ids);
 options($("entryConnection"),connections.filter(c=>current.connection_ids.includes(c.id)).map(c=>[c.id,c.name]));
 options($("funnelProject"),[["","Без проекта"],...projects.map(p=>[p.id,p.name])],current.project_id||"");
 $("json").value=JSON.stringify(current.graph,null,2);renderSteps();renderForm();
}
function renderFunnels(){
 $("funnelCards").replaceChildren();
 if(!funnels.length)$("funnelCards").append(node("p","Создайте первую воронку из шаблона."));
 for(const f of funnels){const card=node("div",undefined,"card");card.append(node("strong",f.name),
  node("p",f.version_id?"Опубликована · редакция "+f.revision:"Черновик · редакция "+f.revision,"muted"),
  button("Открыть",()=>{if(dirty&&!confirm("Есть несохранённые правки. Открыть другую воронку?"))return;resetEditor(f);}));
  $("funnelCards").append(card);}
}
function renderSteps(){
 $("steps").replaceChildren();for(const [id,n] of Object.entries(current.graph.nodes)){
 const card=node("div",undefined,"card"+(id===selected?" selected":""));card.append(button(n.title||types[n.type]||id,()=>{selected=id;renderSteps();renderForm();}));
 card.append(node("div",id+(id===current.graph.start?" · вход":"")+(n.next?" → "+n.next:""),"muted"));$("steps").append(card);}
 renderStory();
}
function stepName(id){const n=current.graph.nodes[id];return n?(n.title||types[n.type]||id):id;}
function renderStory(){
 const box=$("story");if(!box||!current)return;box.replaceChildren();
 box.append(node("h3","Ход сценария"));
 const seen=new Set();let id=current.graph.start,guard=0;
 while(id&&current.graph.nodes[id]&&!seen.has(id)&&guard++<40){
  seen.add(id);const n=current.graph.nodes[id];
  const card=node("div",undefined,"card"+(id===selected?" selected":""));
  card.append(node("strong",(guard)+". "+(n.title||types[n.type])));
  if(n.text)card.append(node("p",String(n.text).slice(0,180),"muted"));
  if(n.type==="wait")card.append(node("p","Пауза "+(n.seconds||0)+" с, затем "+stepName(n.next),"muted"));
  if(n.type==="question")for(const opt of n.options||[])card.append(node("p","Кнопка «"+opt+"» → "+stepName((n.routes||{})[opt]||n.next),"muted"));
  for(const b of n.buttons||[])card.append(node("p","Ссылка «"+b.text+"»: "+b.url,"muted"));
  if(n.type==="task")card.append(node("p","Задача редакторам: "+(n.text||n.title||""),"muted"));
  if(!["question","condition","finish","wait"].includes(n.type)&&n.next)card.append(node("p","Дальше: "+stepName(n.next),"muted"));
  const pick=id;card.onclick=()=>{selected=pick;renderSteps();renderForm();};
  box.append(card);
  if(n.type==="question"||n.type==="condition"||n.type==="finish")break;
  id=n.type==="wait"?n.next:n.next;
 }
}
function field(label,value,onchange,type="input"){
 const wrap=node("label",label);const input=node(type);input.setAttribute("aria-label",label);input.value=value??"";input.onchange=()=>{onchange(input.value);markDirty();renderSteps();};wrap.append(input);$("stepForm").append(wrap);return input;
}
function selection(label,choices,value,onchange){
 const wrap=node("label",label),input=node("select");input.setAttribute("aria-label",label);options(input,choices,value);
 input.onchange=()=>{onchange(input.value);markDirty();renderSteps();};wrap.append(input);$("stepForm").append(wrap);return input;
}
function lines(value){return (value||"").split("\n").map(x=>x.trim()).filter(Boolean);}
function renderForm(){
 const box=$("stepForm");box.replaceChildren();const n=current.graph.nodes[selected];if(!n)return;
 box.append(node("h3",(n.title||types[n.type])+" · "+selected));
 field("Название шага",n.title||"",v=>n.title=v);
 if(["text","video","video_note","voice","photo","document","stage","task","goal"].includes(n.type))
  field(n.type==="task"?"Текст задачи":n.type==="goal"?"Имя цели":"Текст сообщения",n.text,v=>n.text=v,"textarea");
 if(["video","video_note","voice","photo","document"].includes(n.type)){
  selection("Материал из медиабанка",[["","Выберите материал"],...assets.filter(a=>a.kind===n.type).map(a=>[a.id,a.name])],n.asset_id||"",v=>{if(v)n.asset_id=v;else delete n.asset_id;});
  field(n.type==="video_note"?"Резервная ссылка для MAX/VK":"HTTPS ссылка (необязательно при выбранном материале)",n.media||"",v=>{n.media=v;});
  box.append(node("p","Проверьте отправку материала себе во вкладке «Медиабанк». Для MAX/VK пока нужна резервная ссылка.","muted"));
 }
 if(["text","video","photo","document"].includes(n.type)){
  box.append(node("h4","Ссылки в этом сообщении"));
  n.buttons=n.buttons||[];
  n.buttons.forEach((b,index)=>{
   field("Подпись кнопки",b.text||"",v=>{b.text=v;});
   field("Адрес https",b.url||"",v=>{b.url=v;});
   box.append(button("Убрать ссылку",()=>{n.buttons.splice(index,1);markDirty();renderForm();}));
  });
  if(n.buttons.length<5)box.append(button("Добавить ссылку",()=>{n.buttons.push({text:"Подписаться",url:"https://t.me/"});markDirty();renderForm();}));
 }
 if(n.type==="question"){
  field("Вопрос",n.text,v=>n.text=v,"textarea");
  n.options=n.options?.length?n.options:["Если согласны — жми и подпишись"];
  n.routes=n.routes||{};
  const targets=Object.entries(current.graph.nodes).filter(([id])=>id!==selected).map(([id,x])=>[id,(x.title||types[x.type])+" · "+id]);
  n.options.forEach((opt,index)=>{
   box.append(node("h4","Кнопка "+(index+1)));
   field("Текст кнопки",opt,v=>{
    const routes={};for(const [k,dest] of Object.entries(n.routes||{}))routes[k===opt?v:k]=dest;
    n.options[index]=v;n.routes=routes;
   });
   selection("Что показать после этой кнопки",targets,n.routes[opt]||n.next,v=>{n.routes[n.options[index]]=v;});
  });
  if(n.options.length<5)box.append(button("Добавить кнопку",()=>{
   const label="Оценка "+(n.options.length+1);n.options.push(label);n.routes[label]=n.next;markDirty();renderForm();
  }));
  if(n.options.length>1)box.append(button("Убрать последнюю кнопку",()=>{
   const last=n.options.pop();delete n.routes[last];markDirty();renderForm();
  }));
  box.append(node("p","От одной кнопки до пяти. Каждая может открыть своё видео, текст или следующий кусок сценария.","muted"));
 }
 if(n.type==="survey"){
  field("Ключ завершённого опроса",n.key,v=>n.key=v);
  for(const [index,q] of (n.questions||[]).entries()){
   box.append(node("h4","Вопрос "+(index+1)));
   field("Ключ",q.key,v=>q.key=v);field("Текст вопроса",q.text,v=>q.text=v,"textarea");
   field("Варианты (пусто — свободный ответ)",q.options?.join("\n")||"",v=>q.options=lines(v),"textarea");
   box.append(button("Удалить вопрос",()=>{n.questions.splice(index,1);markDirty();renderForm();}));
  }
  box.append(button("Добавить вопрос",()=>{n.questions??=[];n.questions.push({key:"question_"+(n.questions.length+1),text:"Ваш вопрос",options:[]});markDirty();renderForm();}));
 }
 const targets=Object.entries(current.graph.nodes).filter(([id])=>id!==selected).map(([id,x])=>[id,(x.title||types[x.type])+" · "+id]);
 if(n.type==="condition"){
  field("Проверить ключ",n.key,v=>n.key=v);
  field("Равно (true/false или текст)",String(n.equals??true),v=>n.equals=v==="true"?true:v==="false"?false:v);
  selection("Если совпало",targets,n.yes,v=>n.yes=v);selection("Иначе",targets,n.no,v=>n.no=v);
 }else if(n.type!=="finish"&&n.type!=="question")selection("Следующий шаг",targets,n.next,v=>n.next=v);
 if(n.type==="wait"){const input=field("Ожидание, секунд",n.seconds??120,v=>n.seconds=Number(v));input.type="number";input.min="1";}
 if(n.type==="grant"){
  field("Ключ продукта",n.product,v=>n.product=v);
  field("Флаги, которые должны быть завершены (каждый с новой строки)",n.requires?.join("\n")||"",v=>n.requires=lines(v),"textarea");
  box.append(node("p","Например: ключ завершённого опроса survey_completed. Без выполненных условий выдача остановится.","muted"));
 }
 box.append(button("Сделать начальным",()=>{current.graph.start=selected;markDirty();renderSteps();}));
 if(n.type!=="finish")box.append(button("Удалить шаг",()=>{
  const next=n.next||n.no||Object.keys(current.graph.nodes).find(id=>current.graph.nodes[id].type==="finish");
  for(const other of Object.values(current.graph.nodes)){
   for(const k of ["next","yes","no"])if(other[k]===selected)other[k]=next;
   if(other.routes)for(const k of Object.keys(other.routes))if(other.routes[k]===selected)other.routes[k]=next;
  }
  if(current.graph.start===selected)current.graph.start=next;
  delete current.graph.nodes[selected];selected=next;markDirty();renderSteps();renderForm();
 },"danger"));
}
function defaultNode(type,id,next){
 const n={type,title:types[type],...(type==="finish"?{}:{next})};
 if(["text","video","video_note","voice","photo","document","question","stage","task","goal"].includes(type))n.text="Введите текст";
 if(type==="question")Object.assign(n,{key:"answer_"+id,text:"Насколько вам зашло?",options:["Если согласны — жми и подпишись"],routes:{"Если согласны — жми и подпишись":next}});
 if(type==="survey")Object.assign(n,{key:"survey_"+id,questions:[{key:"need",text:"Какую задачу хотите решить?"}]});
 if(type==="wait")n.seconds=120;
 if(type==="grant")Object.assign(n,{product:"bonus",requires:["survey_completed"]});
 if(type==="condition"){delete n.next;Object.assign(n,{key:"interested",equals:"Да",yes:next,no:next});}
 return n;
}
async function refreshAssets(){
 assets=await api("/api/assets");$("assetCards").replaceChildren();
 for(const a of assets){const card=node("div",undefined,"card");card.append(node("strong",a.name),node("p",types[a.kind]+" · "+(a.metadata.size?Math.round(a.metadata.size/1000000)+" МБ":"Telegram")+" · "+a.id,"muted"));
  if(a.preview_available)card.append(button("Предпросмотр",async()=>{
   const r=await fetch("/api/assets/"+a.id+"/file",{headers:{Authorization:"Bearer "+token}});if(!r.ok)throw Error("Не удалось открыть материал");
   if(previewURL)URL.revokeObjectURL(previewURL);previewURL=URL.createObjectURL(await r.blob());$("previewBody").replaceChildren();
   const tag=["video","video_note"].includes(a.kind)?"video":a.kind==="voice"?"audio":a.kind==="photo"?"img":"a";
   const view=node(tag,tag==="a"?"Открыть PDF":undefined);
   if(tag==="a"){view.href=previewURL;view.target="_blank";view.rel="noopener";}else{view.src=previewURL;if(tag!=="img")view.controls=true;}
   $("previewBody").append(view);$("preview").showModal();
  }));
  card.append(button("Удалить",async()=>{if(!confirm("Удалить материал? Используемые в версиях воронки материалы удалять нельзя."))return;await api("/api/assets/"+a.id,"DELETE");await refreshAssets();}));
  $("assetCards").append(card);}
 options($("testAsset"),assets.map(a=>[a.id,a.name]));
 if(current)renderForm();
}
async function refreshOperations(){
 const [contacts,runs,tasks,timeline,outbox,inbox,destinations]=await Promise.all([
  api("/api/contacts"),api("/api/runs"),api("/api/tasks"),api("/api/timeline"),api("/api/outbox"),api("/api/inbox"),api("/api/destinations")]);
 $("contactCards").replaceChildren();for(const x of contacts){const card=node("div",undefined,"card");card.append(node("strong",x.name||x.external_user_id),node("p",x.stage+" · "+x.connection_id,"muted"),
  button("Ответить",async()=>{const text=prompt("Сообщение участнику. Автоматическая воронка будет приостановлена.");if(!text)return;await api("/api/messages","POST",{identity_id:x.identity_id,text});status("Сообщение в очереди, сценарий приостановлен");await refreshOperations();}));$("contactCards").append(card);}
 $("runCards").replaceChildren();for(const r of runs){const card=node("div",undefined,"card");card.append(node("strong",r.name||r.id),node("p",r.status+" · "+r.current,"muted"));
  for(const [action,label] of [["pause","Пауза"],["resume","Продолжить"],["stop","Остановить"]])if(action==="stop"||action==="resume"&&r.status==="paused"||action==="pause"&&["waiting","delayed","active"].includes(r.status))
   card.append(button(label,async()=>{await api("/api/runs/"+r.id+"/action","POST",{action});await refreshOperations();}));
  $("runCards").append(card);}
 $("taskCards").replaceChildren();for(const t of tasks){const card=node("div",undefined,"card");card.append(node("strong",t.title),node("p",t.status,"muted"));if(t.status!=="done")card.append(button("Выполнено",async()=>{await api("/api/tasks/"+t.id,"PUT");await refreshOperations();}));$("taskCards").append(card);}
 $("timeline").textContent=JSON.stringify(timeline,null,2);
 $("outboxCards").replaceChildren();for(const o of outbox){const card=node("div",undefined,"card");card.append(node("strong",o.status),node("p",o.id+" · "+(o.error||o.external_message_id||""),"muted"));
 if(["failed","unknown"].includes(o.status))card.append(button("Повторить",async()=>{
  if(o.status==="unknown"&&!confirm("Результат отправки неизвестен. Проверьте мессенджер. Повтор может создать дубликат. Продолжить?"))return;
  await api("/api/outbox/"+o.id+"/retry","POST",{accept_duplicate_risk:o.status==="unknown"});await refreshOperations();}));
 $("outboxCards").append(card);}
 $("inboxCards").replaceChildren();for(const i of inbox){const card=node("div",undefined,"card");card.append(node("p",i.error+" · "+i.id),button("Повторить обработку",async()=>{await api("/api/inbox/"+i.id+"/retry","POST");await refreshOperations();}));$("inboxCards").append(card);}
 options($("postDestinations"),destinations.map(d=>[d.id,d.name]));
}
function renderProjects(){
 const box=$("projectCards");box.replaceChildren();
 if(!projects.length)box.append(node("p","Проектов пока нет. Создайте первый — одну задачу, потом аккаунты.","muted"));
 for(const p of projects){
  const card=node("div",undefined,"card");
  card.append(node("strong",p.name));
  if(p.goal)card.append(node("p",p.goal,"muted"));
  if(!p.accounts.length)card.append(node("p","Аккаунтов нет. Добавьте их в разделе «Подключения».","muted"));
  for(const a of p.accounts){
   const job={bot:"отвечает людям и ведёт сценарий",seller:"продающий аккаунт этой задачи",chat:"чат этой задачи",channel:"канал, куда уходят публикации"}[a.role]||"участвует в задаче";
   card.append(node("p",(roles[a.role]||a.role)+" — "+a.name+". "+job));
  }
  for(const s of p.scripts||[])card.append(node("p","Сценарий: "+s.name,"muted"));
  box.append(card);
 }
 options($("accountProject"),projects.map(p=>[p.id,p.name]));
 options($("funnelProject"),[["","Без проекта"],...projects.map(p=>[p.id,p.name])],current?.project_id||"");
 options($("accountExisting"),connections.map(c=>[c.id,c.name+(c.project_id?" · уже в проекте":"")]));
}
function renderAdmins(list){
 const box=$("adminList");box.replaceChildren();
 const names={owner:"Владелец",editor:"Редактор",manager:"Менеджер",viewer:"Наблюдатель"};
 if(!list.length)box.append(node("p","Пока доступ есть только у владельца.","muted"));
 for(const a of list){
  const card=node("div",undefined,"card");
  card.append(node("strong",String(a.telegram_id)),node("p",(names[a.role]||a.role)+(a.active?"":" · выключен"),"muted"));
  if(String(a.telegram_id).startsWith("-100"))card.append(node("p","Это номер чата. Человек по нему в панель не войдёт.","muted"));
  box.append(card);
 }
}
async function refresh(){
 connections=await api("/api/connections");funnels=await api("/api/funnels");templates=await api("/api/templates");projects=await api("/api/projects");renderFunnels();renderProjects();
 for(const id of ["assetConnections","adminConnections","funnelConnections"])options($(id),connections.map(c=>[c.id,c.name]),id==="funnelConnections"?current?.connection_ids:undefined);
 for(const id of ["destinationConnection","testConnection","entryConnection"])options($(id),connections.map(c=>[c.id,c.name]));
 options($("templates"),templates.map(t=>[t.id,t.name]));
 $("connectionCards").replaceChildren();
 for(const c of connections){const card=node("div",undefined,"card");const project=projects.find(p=>p.id===c.project_id);card.append(node("strong",c.name),node("p",(roles[c.role]||"Бот")+" · "+(project?project.name:"без проекта"),"muted"));if(role==="owner")card.append(button("Проверить webhook",async()=>{const r=await api("/api/connections/"+c.id+"/activate","POST");status(r.manual_setup?"Настройте VK Callback API: "+r.webhook_url:"Бот подключён");}));$("connectionCards").append(card);}
 if(role==="owner")renderAdmins(await api("/api/admins"));
 await refreshAssets();await refreshOperations();
}
bind("refresh",refresh);bind("refreshMedia",refreshAssets);
bind("new",()=>{if(dirty&&!confirm("Открыть новую воронку без сохранения правок?"))return;resetEditor({id:null,name:"Новая воронка",connection_ids:connections.slice(0,1).map(c=>c.id),revision:0,graph:{start:"welcome",nodes:{welcome:{type:"text",text:"Добро пожаловать!",next:"finish"},finish:{type:"finish"}}}});});
bind("useTemplate",()=>{if(!current)return;if(!confirm("Заменить текущие шаги выбранным шаблоном?"))return;const t=templates.find(t=>t.id===$("templates").value);current.graph=structuredClone(t.graph);selected=current.graph.start;markDirty();renderSteps();renderForm();});
bind("addStep",()=>{if(!current)return;const id="n"+Date.now().toString(36),type=$("newType").value;
 const end=Object.keys(current.graph.nodes).find(k=>current.graph.nodes[k].type==="finish");if(!end)throw Error("Добавьте завершение");
 for(const x of Object.values(current.graph.nodes)){for(const key of ["next","yes","no"])if(x[key]===end)x[key]=id;if(x.routes)for(const k of Object.keys(x.routes))if(x.routes[k]===end)x.routes[k]=id;}
 current.graph.nodes[id]=defaultNode(type,id,end);selected=id;markDirty();renderSteps();renderForm();});
async function save(){
 if(!current)throw Error("Откройте воронку");current.name=$("funnelName").value;current.connection_ids=selectedValues("funnelConnections");
 current.project_id=$("funnelProject").value||null;
 const payload={name:current.name,connection_ids:current.connection_ids,graph:current.graph,project_id:current.project_id};
 if(!current.id){const r=await api("/api/funnels","POST",payload);current.id=r.id;current.revision=r.revision;}
 else {const r=await api("/api/funnels/"+current.id+"/draft","PUT",{revision:current.revision,...payload});current.revision=r.revision;}
 dirty=false;funnels=await api("/api/funnels");renderFunnels();status("Сохранено");
}
bind("save",save);bind("publish",async()=>{await save();await api("/api/funnels/"+current.id+"/publish","POST");funnels=await api("/api/funnels");renderFunnels();status("Воронка опубликована. Создайте ссылку входа.");});
bind("applyJson",()=>{current.graph=JSON.parse($("json").value);selected=current.graph.start;markDirty();renderSteps();renderForm();});
bind("export",()=>{const blob=new Blob([JSON.stringify(current.graph,null,2)],{type:"application/json"});const url=URL.createObjectURL(blob),a=node("a");a.href=url;a.download="funnel.json";a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);});
bind("makeEntry",async()=>{if(!current?.id)throw Error("Сначала сохраните и опубликуйте воронку");
 const r=await api("/api/entries","POST",{funnel_id:current.id,connection_id:$("entryConnection").value,attribution:{source:$("entrySource").value,campaign:$("entryCampaign").value}});
 $("entryResult").replaceChildren();if(r.telegram_link){const a=node("a",r.telegram_link);a.href=r.telegram_link;a.target="_blank";a.rel="noopener";$("entryResult").append(a);}else $("entryResult").textContent="Payload: "+r.payload;
});
bind("upload",async()=>{const file=$("assetFile").files[0];if(!file)throw Error("Выберите файл");status("Загрузка и проверка материала…");
 const form=new FormData();form.set("name",$("assetName").value||file.name);form.set("kind",$("assetKind").value);form.set("connection_ids",JSON.stringify(selectedValues("assetConnections")));form.set("file",file);
 await api("/api/assets","POST",form);await refreshAssets();status("Материал сохранён");});
bind("testSend",async()=>{await api("/api/assets/test-send","POST",{asset_id:$("testAsset").value,connection_id:$("testConnection").value,chat_id:$("testChat").value});status("Тестовая отправка поставлена в очередь");});
bind("post",async()=>{await api("/api/publications","POST",{destination_ids:selectedValues("postDestinations"),text:$("postText").value});status("Публикация поставлена в очередь");});
bind("addDestination",async()=>{await api("/api/destinations","POST",{connection_id:$("destinationConnection").value,name:$("destinationName").value,external_chat_id:$("destinationChat").value,kind:"channel"});await refreshOperations();status("Площадка добавлена");});
bind("addConnection",async()=>{await api("/api/connections","POST",{name:$("connectionName").value,platform:$("platform").value,token_env:$("tokenEnv").value,secret_env:$("secretEnv").value,config:{welcome_text:"Выберите программу"}});await refresh();status("Бот создан. Токен и секрет должны уже лежать в окружении сервера.");});
bind("addProject",async()=>{await api("/api/projects","POST",{name:$("projectName").value,goal:$("projectGoal").value});await refresh();switchPage("projects");status("Проект создан. Добавьте аккаунты в «Подключения».");});
bind("refreshProjects",refresh);
bind("addAccount",async()=>{
 const projectId=$("accountProject").value;if(!projectId)throw Error("Сначала создайте проект");
 const role=$("accountRole").value;const name=$("accountName").value.trim();
 if(role==="chat"||role==="channel"){
  const bot=connections.find(c=>c.project_id===projectId);
  if(!bot)throw Error("Сначала добавьте в проект бота, который будет писать в этот чат");
  if(!$("accountChat").value.trim())throw Error("Укажите ID чата или канала");
  await api("/api/destinations","POST",{connection_id:bot.id,name:name||roles[role],external_chat_id:$("accountChat").value.trim(),kind:role==="chat"?"group":"channel"});
 }else{
  if(!$("accountExisting").value)throw Error("Выберите бота");
  await api("/api/connections/"+$("accountExisting").value,"PUT",{project_id:projectId,role,name:name||undefined});
 }
 await refresh();status("Аккаунт добавлен в проект");
});
$("accountRole").onchange=()=>{const chat=$("accountRole").value==="chat"||$("accountRole").value==="channel";$("chatWrap").hidden=!chat;$("existingWrap").hidden=chat;};
bind("saveAdmin",async()=>{
 const id=Number($("adminId").value);
 await api("/api/admins","PUT",{telegram_id:id,role:$("adminRole").value,connection_ids:selectedValues("adminConnections"),active:$("adminActive").checked});
 await refresh();
 status(String(id).startsWith("-100")?"Номер сохранён, но это ID чата. Для входа нужен личный ID человека.":"Доступ сохранён");
});
bind("preflight",async()=>{$("preflightResult").textContent=JSON.stringify(await api("/api/preflight"),null,2);});
bind("closePreview",()=>{$("preview").close();if(previewURL){URL.revokeObjectURL(previewURL);previewURL=null;}});
$("funnelName").oninput=()=>{if(current){current.name=$("funnelName").value;dirty=true;}};
$("funnelConnections").onchange=()=>{if(current){current.connection_ids=selectedValues("funnelConnections");dirty=true;options($("entryConnection"),connections.filter(c=>current.connection_ids.includes(c.id)).map(c=>[c.id,c.name]));}};
window.addEventListener("beforeunload",e=>{if(dirty){e.preventDefault();e.returnValue="";}});
(async()=>{
 try{
 options($("newType"),Object.entries(types).filter(([k])=>k!=="finish"));
 options($("assetKind"),["video","video_note","voice","photo","document"].map(k=>[k,types[k]]));
 for(const [id,label] of [["projects","Проекты"],["accounts","Подключения"],["funnels","Сценарии"],["media","Медиабанк"],["contacts","Контакты"],["publications","Публикации"],["delivery","Доставка"],["access","Совместный доступ"]])$("nav").append(button(label,async()=>{switchPage(id);if(["contacts","delivery"].includes(id))await refreshOperations();}));
 const tg=window.Telegram?.WebApp;if(!tg?.initData)throw Error("Кнопка «Воронки» в чате бота не передала вход. Закройте это окно и нажмите её ещё раз — не открывайте ссылку в браузере.");
 tg.ready();const response=await fetch("/auth",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({init_data:tg.initData})});
 const data=await response.json();if(!response.ok)throw Error(JSON.stringify(data.detail));
 token=data.token;role=data.role;$("testChat").value=String(tg.initDataUnsafe?.user?.id||"");
 await refresh();document.querySelector("main").hidden=false;status("Вход выполнен · "+role);
 }catch(e){status(e.message);}
})();
