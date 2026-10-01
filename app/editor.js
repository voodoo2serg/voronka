"use strict";
const $=id=>document.getElementById(id);
let token="", role="", connections=[], funnels=[], assets=[], templates=[], current=null, selected=null, dirty=false, previewURL=null;
const types={text:"Текст",video:"Видео",video_note:"Кружок",voice:"Голос",photo:"Фото",document:"PDF",
 question:"Вопрос",survey:"Опрос",condition:"Условие",wait:"Ожидание",grant:"Выдать доступ",stage:"Этап CRM",task:"Задача менеджеру",goal:"Цель",finish:"Завершить"};
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
 if(["text","video","video_note","voice","photo","document","question","stage","task","goal"].includes(n.type))
  field(n.type==="goal"?"Имя цели":"Текст",n.text,v=>n.text=v,"textarea");
 if(["video","video_note","voice","photo","document"].includes(n.type)){
  selection("Материал из медиабанка",[["","Выберите материал"],...assets.filter(a=>a.kind===n.type).map(a=>[a.id,a.name])],n.asset_id||"",v=>{if(v)n.asset_id=v;else delete n.asset_id;});
  field(n.type==="video_note"?"Резервная ссылка для MAX/VK":"HTTPS ссылка (необязательно при выбранном материале)",n.media||"",v=>{n.media=v;});
  box.append(node("p","Проверьте отправку материала себе во вкладке «Медиабанк». Для MAX/VK пока нужна резервная ссылка.","muted"));
 }
 if(n.type==="question"){
  field("Ключ ответа",n.key,v=>n.key=v);
  field("Варианты ответа: каждый с новой строки",n.options?.join("\n")||"",v=>n.options=lines(v),"textarea");
  selection("Тип ответа",[["text","Текст"],["email","Email"],["phone","Телефон"]],n.answer_type||"text",v=>n.answer_type=v);
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
 }else if(n.type!=="finish")selection("Следующий шаг",targets,n.next,v=>n.next=v);
 if(n.type==="wait"){const input=field("Ожидание, секунд",n.seconds??120,v=>n.seconds=Number(v));input.type="number";input.min="1";}
 if(n.type==="grant"){
  field("Ключ продукта",n.product,v=>n.product=v);
  field("Флаги, которые должны быть завершены (каждый с новой строки)",n.requires?.join("\n")||"",v=>n.requires=lines(v),"textarea");
  box.append(node("p","Например: ключ завершённого опроса survey_completed. Без выполненных условий выдача остановится.","muted"));
 }
 box.append(button("Сделать начальным",()=>{current.graph.start=selected;markDirty();renderSteps();}));
 if(n.type!=="finish")box.append(button("Удалить шаг",()=>{
  const next=n.next||n.no||Object.keys(current.graph.nodes).find(id=>current.graph.nodes[id].type==="finish");
  for(const other of Object.values(current.graph.nodes))for(const k of ["next","yes","no"])if(other[k]===selected)other[k]=next;
  if(current.graph.start===selected)current.graph.start=next;
  delete current.graph.nodes[selected];selected=next;markDirty();renderSteps();renderForm();
 },"danger"));
}
function defaultNode(type,id,next){
 const n={type,title:types[type],...(type==="finish"?{}:{next})};
 if(["text","video","video_note","voice","photo","document","question","stage","task","goal"].includes(type))n.text="Введите текст";
 if(type==="question")Object.assign(n,{key:"answer_"+id,options:["Продолжить"]});
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
async function refresh(){
 connections=await api("/api/connections");funnels=await api("/api/funnels");templates=await api("/api/templates");renderFunnels();
 for(const id of ["assetConnections","adminConnections","funnelConnections"])options($(id),connections.map(c=>[c.id,c.name]),id==="funnelConnections"?current?.connection_ids:undefined);
 for(const id of ["destinationConnection","testConnection","entryConnection"])options($(id),connections.map(c=>[c.id,c.name]));
 options($("templates"),templates.map(t=>[t.id,t.name]));
 $("connectionCards").replaceChildren();
 for(const c of connections){const card=node("div",undefined,"card");card.append(node("strong",c.name),node("p",c.platform+" · "+c.id,"muted"));if(role==="owner")card.append(button("Проверить и подключить webhook",async()=>{const r=await api("/api/connections/"+c.id+"/activate","POST");status(r.manual_setup?"Настройте VK Callback API: "+r.webhook_url:"Бот подключён");}));$("connectionCards").append(card);}
 if(role==="owner")$("adminList").textContent=JSON.stringify(await api("/api/admins"),null,2);
 await refreshAssets();await refreshOperations();
}
bind("refresh",refresh);bind("refreshMedia",refreshAssets);
bind("new",()=>{if(dirty&&!confirm("Открыть новую воронку без сохранения правок?"))return;resetEditor({id:null,name:"Новая воронка",connection_ids:connections.slice(0,1).map(c=>c.id),revision:0,graph:{start:"welcome",nodes:{welcome:{type:"text",text:"Добро пожаловать!",next:"finish"},finish:{type:"finish"}}}});});
bind("useTemplate",()=>{if(!current)return;if(!confirm("Заменить текущие шаги выбранным шаблоном?"))return;const t=templates.find(t=>t.id===$("templates").value);current.graph=structuredClone(t.graph);selected=current.graph.start;markDirty();renderSteps();renderForm();});
bind("addStep",()=>{if(!current)return;const id="n"+Date.now().toString(36),type=$("newType").value;
 const end=Object.keys(current.graph.nodes).find(k=>current.graph.nodes[k].type==="finish");if(!end)throw Error("Добавьте завершение");
 for(const x of Object.values(current.graph.nodes))for(const key of ["next","yes","no"])if(x[key]===end)x[key]=id;
 current.graph.nodes[id]=defaultNode(type,id,end);selected=id;markDirty();renderSteps();renderForm();});
async function save(){
 if(!current)throw Error("Откройте воронку");current.name=$("funnelName").value;current.connection_ids=selectedValues("funnelConnections");
 if(!current.id){const r=await api("/api/funnels","POST",{name:current.name,connection_ids:current.connection_ids,graph:current.graph});current.id=r.id;current.revision=r.revision;}
 else {const r=await api("/api/funnels/"+current.id+"/draft","PUT",{revision:current.revision,graph:current.graph,name:current.name,connection_ids:current.connection_ids});current.revision=r.revision;}
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
bind("addConnection",async()=>{await api("/api/connections","POST",{name:$("connectionName").value,platform:$("platform").value,token_env:$("tokenEnv").value,secret_env:$("secretEnv").value,config:JSON.parse($("connectionConfig").value)});await refresh();status("Подключение создано. Проверьте токен и активируйте webhook.");});
bind("saveAdmin",async()=>{await api("/api/admins","PUT",{telegram_id:Number($("adminId").value),role:$("adminRole").value,connection_ids:selectedValues("adminConnections"),active:$("adminActive").checked});await refresh();status("Доступ сохранён");});
bind("preflight",async()=>{$("preflightResult").textContent=JSON.stringify(await api("/api/preflight"),null,2);});
bind("closePreview",()=>{$("preview").close();if(previewURL){URL.revokeObjectURL(previewURL);previewURL=null;}});
$("funnelName").oninput=()=>{if(current){current.name=$("funnelName").value;dirty=true;}};
$("funnelConnections").onchange=()=>{if(current){current.connection_ids=selectedValues("funnelConnections");dirty=true;options($("entryConnection"),connections.filter(c=>current.connection_ids.includes(c.id)).map(c=>[c.id,c.name]));}};
window.addEventListener("beforeunload",e=>{if(dirty){e.preventDefault();e.returnValue="";}});
(async()=>{
 try{
 options($("newType"),Object.entries(types).filter(([k])=>k!=="finish"));
 options($("assetKind"),["video","video_note","voice","photo","document"].map(k=>[k,types[k]]));
 for(const [id,label] of [["funnels","Воронки"],["media","Медиабанк"],["contacts","Контакты"],["publications","Публикации"],["delivery","Доставка"],["settings","Настройки"]])$("nav").append(button(label,async()=>{switchPage(id);if(["contacts","delivery"].includes(id))await refreshOperations();}));
 const tg=window.Telegram?.WebApp;if(!tg?.initData)throw Error("Откройте панель через меню административного бота Telegram");
 tg.ready();const response=await fetch("/auth",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({init_data:tg.initData})});
 const data=await response.json();if(!response.ok)throw Error(JSON.stringify(data.detail));
 token=data.token;role=data.role;$("testChat").value=String(tg.initDataUnsafe?.user?.id||"");
 await refresh();document.querySelector("main").hidden=false;status("Вход выполнен · "+role);
 }catch(e){status(e.message);}
})();
