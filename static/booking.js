"use strict";
(() => {
 const $=id=>document.getElementById(id),config=JSON.parse($("widget-config").textContent);
 // 2.14.0: the client's page in English (?lang=en or the browser's language, html lang); keys are the Italian texts
 const T={en:{"Sede":"Location","Scegli la sede e la data.":"Choose the location and the date.","Scegli la sede":"Choose the location","Servizio":"Service","Scegli un ":"Choose a ","Persone":"People","Ospiti":"Guests","Persone / quantità":"People / quantity","Prestazione standard":"Standard","Qualsiasi unità libera":"Any free unit","Il primo disponibile":"First available","Unità / camera":"Unit / room","Operatore / risorsa":"Staff / resource","Arrivo":"Arrival","Data":"Date","Seleziona":"Select","Scegli le date per vedere le disponibilità.":"Choose the dates to see what is available.","Scegli la data per vedere gli orari liberi.":"Choose the date to see the free times.","Scegli servizio e data per vedere gli orari liberi.":"Choose service and date to see the free times.","Controllo delle disponibilità…":"Checking availability…","Impossibile verificare le disponibilità. Riprova.":"Availability could not be checked. Please try again.","Disponibilità non verificabile.":"Availability could not be checked."," · fino a ":" · up to "," ospiti":" guests","fino alle ":"until ","Scegli l'unità che preferisci.":"Choose the unit you prefer.","Scegli uno degli orari disponibili.":"Choose one of the available times.","Nessuna unità libera per tutte le date scelte.":"No unit is free for all the dates chosen.","Nessun orario disponibile: prova un'altra data.":"No times available: try another date.","Tutto pieno in queste date.":"Fully booked on these dates.","Tutto pieno in questo giorno.":"Fully booked on this day.","Non trovi l'orario che vuoi?":"Can't find the time you want?","Spunta la casella per ricevere l'avviso.":"Tick the box to get the notice.","Non è andata: riprova tra poco.":"It did not work: please try again shortly.","Fatto: ti scriviamo appena si libera un posto.":"Done: we will write to you as soon as a place frees up.","Risposta non disponibile. Riprova con gli stessi dati: la richiesta viene riconosciuta.":"No answer. Try again with the same details: the request is recognised.","Prenotazione non completata.":"Booking not completed.","Riferimento: ":"Reference: ","Richiesta inviata":"Request sent","Gestisci / annulla la richiesta":"Manage / cancel the request","Indifferente":"No preference"}}[document.documentElement.lang]||{},t=s=>T[s]||s;
 const form=$("booking-form"),service=$("service"),option=$("option"),resource=$("resource"),party=$("party"),day=$("date"),departure=$("departure"),submit=$("booking-submit"),area=$("area");
 let selected=null,controller=null,sequence=0,busy=false,requestKey=crypto.randomUUID().replace(/-/g,""),forceOk=false;
 // 2.14.0: Forza at the desk: the time typed in for appointments and places, every date of the day for events
 const force=$("force"),forceAt=$("force-at"),label=submit.textContent;
 const forcing=()=>!!force?.checked&&current()?.kind!=="day",typed=()=>!config.walkin&&forcing()&&["resource","capacity"].includes(current()?.kind);
 const select=(el,items,label)=>{el.replaceChildren();if(label)el.add(new Option(label,""));items.forEach(v=>el.add(new Option(v.name,v.id)));};
 const current=()=>config.services.find(s=>s.id===service.value);
 // one service: nothing to choose. The same service in several locations: choose the location.
 // Different services in several locations: the service, with its location.
 const places=new Set(config.services.map(s=>s.location)).size,names=new Set(config.services.map(s=>s.name)).size;
 if(config.services.length===1){$("service-wrap").hidden=true;select(service,config.services,"");}
 else if(names===1&&places>1){$("service-label").textContent=t("Sede");$("slot-status").textContent=t("Scegli la sede e la data.");select(service,config.services.map(s=>({id:s.id,name:s.location})),t("Scegli la sede"));}
 else{const word=config.service_label||t("Servizio");$("service-label").textContent=word;select(service,config.services.map(s=>({id:s.id,name:(places>1?s.name+" ("+s.location+")":s.name)+(s.price?" · "+s.price:"")})),t("Scegli un ")+word.toLowerCase());}
 const kinds=new Set(config.services.map(s=>s.kind));if(kinds.size===1){const k=[...kinds][0];$("party-label").textContent=t(["shift","capacity","event"].includes(k)?"Persone":k==="day"?"Ospiti":"Persone / quantità");}
 day.min=config.today;day.value=config.prefill?.date&&config.prefill.date>=config.today?config.prefill.date:config.today;departure.min=config.today;
 // until a service is chosen, the number of people shows only if some service takes more than one
 $("party-wrap").hidden=Math.max(1,...config.services.map(s=>s.max_party||1))<=1;
 // 2.8.0: allergies and intolerances from the business's own list, as chips to tick
 const allergenBox=$("allergens-box");
 if(allergenBox&&config.allergens?.length&&!config.editing){
  allergenBox.hidden=false;
  for(const name of config.allergens){const label=document.createElement("label"),box=document.createElement("input"),text=document.createElement("span");
   label.className="chip-check";box.type="checkbox";box.value=name;box.dataset.allergen="";text.textContent=name;label.append(box,text);$("allergen-list").append(label);}
 }
 function buildFields(s){
  const holder=$("booking-fields");if(!holder)return;
  holder.replaceChildren();
  for(const field of s?.fields||[]){
   const wrap=document.createElement("div"),label=document.createElement("label");
   if(field.type==="checkbox"){
    // a yes/no question reads as one sentence with its box, across the whole row
    const box=document.createElement("input");box.type="checkbox";box.id="extra-"+field.key;box.dataset.field=field.key;box.required=field.required===true;
    label.className="check";label.append(box,field.label+(field.required?" *":""));wrap.className="span-two";wrap.append(label);holder.append(wrap);continue;
   }
   label.htmlFor="extra-"+field.key;label.textContent=field.label+(field.required?" *":"");
   const input=document.createElement(field.type==="select"?"select":"input");
   input.id="extra-"+field.key;input.dataset.field=field.key;input.required=field.required===true;
   if(field.type==="select"){input.add(new Option(t("Seleziona"),""));for(const c of field.choices)input.add(new Option(c,c));}
   else{input.type=field.type==="checkbox"?"checkbox":field.type==="number"?"number":"text";if(input.type==="text")input.maxLength=500;if(input.type==="number"){input.min=0;input.max=100000;}}
   wrap.append(label,input);holder.append(wrap);
  }
 }
 // 2.14.0: more services in the same visit, one after the other: the appointments of the same location
 const visitAdd=$("visit-add");let extras=config.editing?.extra||[];
 function visitPick(){
  const s=current(),wrap=$("visit-wrap");if(!wrap)return;
  const more=s?.kind==="resource"&&!config.walkin&&!config.editing?config.services.filter(x=>x.kind==="resource"&&x.location===s.location&&x.id!==s.id):[];
  wrap.hidden=!more.length&&!extras.length;visitAdd.hidden=!more.length||extras.length>=4;$("visit-add-label").hidden=visitAdd.hidden;
  select(visitAdd,more.map(x=>({id:x.id,name:x.name+(x.price?" · "+x.price:"")})),t("Nessun altro servizio"));
  $("visit-list").replaceChildren(...extras.map((x,i)=>{const chip=document.createElement("span");chip.className="pill";chip.textContent="+ "+x.name;
   if(!config.editing){const b=document.createElement("button");b.type="button";b.className="ghost small";b.textContent="×";b.setAttribute("aria-label",t("Togli ")+x.name);b.addEventListener("click",()=>{extras.splice(i,1);visitPick();loadSlots();});chip.append(b);}
   return chip;}));
 }
 visitAdd?.addEventListener("change",()=>{const x=config.services.find(v=>v.id===visitAdd.value);if(x){extras.push({id:x.id,name:x.name});visitPick();loadSlots();}});
 function changeService(){
  const s=current();if(!config.editing)extras=[];visitPick();select(option,(s?.options||[]).map(o=>({id:o.id,name:o.name+(o.price?" · "+o.price:"")})),t("Prestazione standard")+(s?.price?" · "+s.price:""));select(resource,s?.resources||[],t(s?.kind==="day"?"Qualsiasi unità libera":"Il primo disponibile"));
  // 2.10.0: from the agenda, the operator of the free space clicked
  if(config.prefill?.resource&&s?.resources.some(r=>r.id===config.prefill.resource))resource.value=config.prefill.resource;
  pickers();
  $("option-wrap").hidden=!s?.options.length;$("resource-wrap").hidden=!((s?.kind==="resource"||s?.kind==="day")&&s?.resources.length);
  $("resource-label").textContent=s?.resource_label||t(s?.kind==="day"?"Unità / camera":"Operatore / risorsa");$("departure-wrap").hidden=s?.kind!=="day";departure.required=s?.kind==="day";
  // 2.14.0: the desk also books a group bigger than the online limit, within the places
  const most=s?.max_party||1;party.max=config.internal&&most>1?1000:most;party.value=Math.min(Number(party.value)||1,Number(party.max));
  // one person per booking: no question to ask. Otherwise call it by what it counts
  $("party-wrap").hidden=most<=1;$("party-label").textContent=t(["shift","capacity","event"].includes(s?.kind)?"Persone":s?.kind==="day"?"Ospiti":"Persone / quantità");
  $("service-description").textContent=s?.description||"";groupNote();
  // 2.14.0: the room of a restaurant (Sala, Dehors) or Indifferente; never the table
  area.replaceChildren(new Option(t("Indifferente"),""),...(s?.areas||[]).map(a=>new Option(a.name,a.id)));$("area-wrap").hidden=!s?.areas?.length||!!config.editing;
  // nothing left to choose in the first block: skip it
  // the choices above the row only when there is something to choose; the row holds who, how many and when
  $("what-grid").hidden=$("service-wrap").hidden&&$("option-wrap").hidden;$("date-label").textContent=t(s?.kind==="day"?"Arrivo":"Data");
  buildFields(s);loadSlots();
 }
 // 2.14.0: a group above the threshold of the service is a request: said before the client sends it
 function groupNote(){const s=current(),n=s?.request_above||0,big=!config.internal&&!config.editing&&n>0&&Number(party.value)>n;
  $("group-note").hidden=!big;$("group-note").textContent=big?`Per gruppi oltre ${n} persone la richiesta va confermata dall'attività: ti scriviamo appena possibile.`:"";
  submit.textContent=big?"Invia la richiesta":label;}
 party.addEventListener("input",groupNote);
 async function loadSlots(){
  const seq=++sequence;controller?.abort();controller=new AbortController();selected=null;submit.disabled=true;$("slots").replaceChildren();if(waitBox)waitBox.hidden=true;
  const s=current();forceOk=false;groupNote();
  if(force){$("force-time").hidden=!typed();force.closest(".force-box").hidden=s?.kind==="day";day.min=forcing()?"":config.today;}
  // 2.14.0: a client without appointment starts now: nothing to choose but the service and with whom
  if(config.walkin){if(s){selected={start:""};submit.disabled=busy;}return;}
  if(typed()){
   if(day.value&&forceAt.value){selected={start:day.value+"T"+forceAt.value,resource:"",occurrence:""};submit.disabled=busy;}
   $("slot-status").textContent="Scrivi giorno e ora: quando confermi il programma dice cosa stai superando.";return;
  }
  if(!s||!day.value||(s.kind==="day"&&!departure.value)){$("slot-status").textContent=t($("service-label").textContent===t("Sede")?"Scegli la sede e la data.":s&&s.kind==="day"?"Scegli le date per vedere le disponibilità.":$("service-wrap").hidden?"Scegli la data per vedere gli orari liberi.":"Scegli servizio e data per vedere gli orari liberi.");return;}
  $("slot-status").textContent=t("Controllo delle disponibilità…");$("slots").setAttribute("aria-busy","true");
  try{
   const query=new URLSearchParams({service:s.id,date:day.value,option:option.value,resource:resource.value,party:party.value,end_date:departure.value,area:area.value,...(forcing()?{forza:"1"}:{}),...(extras.length?{extra:extras.map(x=>x.id).join(",")}:{})});
   const response=await fetch(config.slots+(config.slots.includes("?")?"&":"?")+query,{signal:controller.signal,credentials:"same-origin"});
   let result;try{result=await response.json();}catch{throw Error(t("Impossibile verificare le disponibilità. Riprova."));}
   if(seq!==sequence)return;
   if(!response.ok)throw Error(result.error||t("Disponibilità non verificabile."));
   const format=new Intl.DateTimeFormat("it-IT",{timeZone:config.timezone,hour:"2-digit",minute:"2-digit",hourCycle:"h23"});  // 2.6.0: 10:30, no CET
   const units=s.kind==="day"&&s.resources.length>0;
   const shown=resource.value||units?result.slots:result.slots.filter((v,i,a)=>a.findIndex(x=>x.start===v.start)===i);
   for(const slot of shown){
    const button=document.createElement("button");button.type="button";button.className="slot";button.setAttribute("aria-pressed","false");
    button.append(format.format(new Date(slot.start)));
    const sub=document.createElement("small");
    sub.textContent=units?slot.resource_name+(slot.capacity?t(" · fino a ")+slot.capacity+t(" ospiti"):""):(resource.value?slot.resource_name:"")||(s.kind==="capacity"&&s.turnover===false?"":t("fino alle ")+format.format(new Date(slot.end)));if(sub.textContent)button.append(sub);
    if(units&&slot.description){const d=document.createElement("small");d.textContent=slot.description;button.append(d);}
    button.addEventListener("click",()=>{selected=slot;forceOk=false;submit.textContent=label;$("slots").querySelectorAll("button").forEach(b=>b.setAttribute("aria-pressed",String(b===button)));submit.disabled=busy;});
    $("slots").append(button);
   }
   // 2.10.0: from the agenda, the time of the free space clicked is chosen already, when it is free for this service
   if(config.prefill?.time&&!selected){const hit=[...$("slots").querySelectorAll("button")].find(b=>b.firstChild?.textContent===config.prefill.time);if(hit){hit.click();hit.scrollIntoView({block:"nearest"});}}
   $("slot-status").textContent=t(result.slots.length?(units?"Scegli l'unità che preferisci.":"Scegli uno degli orari disponibili."):(units?"Nessuna unità libera per tutte le date scelte.":"Nessun orario disponibile: prova un'altra data."));
   if(!result.closed)waitShow(!result.slots.length);  // a closed day: nothing to wait for
  }catch(e){if(e.name!=="AbortError"&&seq===sequence)$("slot-status").textContent=e.message;}
  finally{if(seq===sequence)$("slots").removeAttribute("aria-busy");}
 }
 // 2.14.0: Avvisami se si libera: the day full, or not the hours wanted; name and email from the contacts above
 const waitBox=$("wait-box");
 function waitShow(empty){
  if(!waitBox||config.editing||waitBox.dataset.done)return;const s=current();
  waitBox.hidden=!s||!day.value;$("wait-title").textContent=t(empty?(s?.kind==="day"?"Tutto pieno in queste date.":"Tutto pieno in questo giorno."):"Non trovi l'orario che vuoi?");
  $("wait-range").hidden=s?.kind==="day";$("wait-last").min=day.value;
 }
 $("wait-open")?.addEventListener("click",()=>{$("wait-form").hidden=false;$("wait-open").hidden=true;$("wait-consent").focus();});
 $("wait-send")?.addEventListener("click",async()=>{
  const s=current(),b=$("wait-send");if(!s||![$("first-name"),$("customer-email")].every(el=>el.reportValidity()))return;
  if(!$("wait-consent").checked){$("wait-status").textContent=t("Spunta la casella per ricevere l'avviso.");return;}
  b.disabled=true;$("wait-status").textContent="";
  try{
   const body={service:s.id,resource:resource.value,party:party.value,first:day.value,last:s.kind==="day"?departure.value:($("wait-last").value||day.value),
    time_from:$("wait-from").value,time_to:$("wait-to").value,first_name:$("first-name").value,last_name:$("last-name").value,email:$("customer-email").value,phone:$("phone").value,consent:true};
   const r=await fetch(config.wait,{method:"POST",headers:{"Content-Type":"application/json","X-CSRFToken":form.querySelector("[name=csrfmiddlewaretoken]").value,"X-TDBS-Ticket":config.ticket},body:JSON.stringify(body),credentials:"same-origin"});
   let res;try{res=await r.json();}catch{throw Error(t("Non è andata: riprova tra poco."));}
   if(!r.ok)throw Error(res.error||t("Non è andata: riprova tra poco."));
   waitBox.dataset.done="1";$("wait-form").hidden=true;$("wait-title").textContent=t("Fatto: ti scriviamo appena si libera un posto.");
  }catch(e){$("wait-status").textContent=e.message;}finally{b.disabled=false;}
 });
 service.addEventListener("change",changeService);
 [option,resource,party,day,departure,force,forceAt,area].forEach(el=>el?.addEventListener("change",loadSlots));
 // 2.12.0: with operators' photos, one face each to choose with whom; the select stays, for the keyboard and the logic
 function pickers(){
  const box=$("resource-pick"),s=current();if(!box)return;mark();
  const people=s?.kind==="resource"?s.resources:[];
  box.replaceChildren();box.hidden=!people.some(r=>r.photo);resource.classList.toggle("has-pick",!box.hidden);
  if(box.hidden)return;
  const face=(r)=>{const b=document.createElement("button"),pic=document.createElement("span"),name=document.createElement("span");
   b.type="button";b.className="pick";b.dataset.value=r?r.id:"";pic.className="pick-face";name.className="pick-name";
   if(r&&r.photo){const img=document.createElement("img");img.src=r.photo;img.alt="";pic.append(img);}else pic.textContent=r?r.name.charAt(0).toUpperCase():"✱";
   name.textContent=r?r.name:t("Il primo disponibile");b.append(pic,name);
   b.addEventListener("click",()=>{resource.value=b.dataset.value;resource.dispatchEvent(new Event("change"));mark();});return b;};
  box.append(face(null),...people.map(face));mark();
 }
 function mark(){document.querySelectorAll("#resource-pick .pick").forEach(b=>b.setAttribute("aria-pressed",String(b.dataset.value===resource.value)));
  // the description of the operator or the room chosen, as its form promises
  const note=$("resource-description"),r=current()?.resources.find(x=>x.id===resource.value);
  if(note){note.textContent=r?.description||"";note.hidden=!note.textContent;}}
 resource.addEventListener("change",mark);
 form.addEventListener("submit",async e=>{
  e.preventDefault();if(busy||!selected||!form.reportValidity())return;
  busy=true;submit.disabled=true;$("booking-error").hidden=true;$("booking-error").className="notice error";
  try{
   const s=current(),answers={};
   document.querySelectorAll("[data-field]").forEach(el=>{answers[el.dataset.field]=el.type==="checkbox"?el.checked:el.value;});
   const payload={service:s.id,option:option.value,resource:resource.value||selected.resource||"",start:selected.start,occurrence:selected.occurrence,end_date:departure.value,party:party.value,area:area.value};
   if(!config.editing)Object.assign(payload,{first_name:$("first-name").value,last_name:$("last-name").value,email:$("customer-email").value,phone:$("phone").value,notes:$("notes").value,answers,marketing:$("marketing-consent").checked,notices:$("notices")?$("notices").checked:true,channel:$("channel")&&$("phone").value.trim()?$("channel").value:"",allergies:[...document.querySelectorAll("[data-allergen]:checked")].map(b=>b.value)});
   if(!config.editing&&clientId)payload.contact=clientId;
   if(config.walkin)payload.walkin=true;
   if(extras.length)Object.assign(payload,{extra:extras.map(x=>x.id),visit:true});
   if(forcing()){payload.force=true;if(forceOk)payload.force_ok=true;}
   // Persist only a fingerprint and random idempotency key, never personal fields.
   let cacheKey="";
   try{
    const digest=await crypto.subtle.digest("SHA-256",new TextEncoder().encode(JSON.stringify(payload)));
    const hash=Array.from(new Uint8Array(digest),v=>v.toString(16).padStart(2,"0")).join("");
    cacheKey="tdbs-request:"+config.submit+":"+hash;
    requestKey=sessionStorage.getItem(cacheKey)||crypto.randomUUID().replace(/-/g,"");
    sessionStorage.setItem(cacheKey,requestKey);
   }catch{}
   payload.request_key=requestKey;
   const response=await fetch(config.submit,{method:"POST",headers:{"Content-Type":"application/json","X-CSRFToken":form.querySelector("[name=csrfmiddlewaretoken]").value,"X-TDBS-Ticket":config.ticket},body:JSON.stringify(payload),credentials:"same-origin"});
   let result;try{result=await response.json();}catch{throw Error(t("Risposta non disponibile. Riprova con gli stessi dati: la richiesta viene riconosciuta."));}
   // 2.14.0: Forza says what is being exceeded; a second click confirms it
   if(response.status===409&&result.force){forceOk=true;submit.textContent="Conferma e forza";$("booking-error").className="notice warning";throw Error(result.error);}
   if(!response.ok)throw Error(result.error||t("Prenotazione non completata."));
   // done: the same data typed again later is a new booking, not this one (it may have been cancelled meanwhile)
   try{if(cacheKey)sessionStorage.removeItem(cacheKey);}catch{}
   if(result.redirect){location.assign(result.redirect);return;}
   form.hidden=true;$("booking-success").hidden=false;$("booking-reference").textContent=t("Riferimento: ")+result.reference;
   // 2.14.0: a request the business confirms later
   if(result.pending){$("booking-done").textContent=t("Richiesta inviata");$("booking-pending").hidden=false;$("cancel-link").textContent=t("Gestisci / annulla la richiesta");}
   // 2.14.0: the confirmed booking into the client's calendar
   if(result.calendar){$("cal-google").href=result.calendar.google;$("cal-outlook").href=result.calendar.outlook;$("cal-ics").href=result.calendar.ics;$("calendar-links").hidden=false;}
   $("cancel-link").href=result.cancel;$("booking-success").scrollIntoView({block:"center"});$("cancel-link").focus();
  }catch(e){$("booking-error").textContent=e.message;$("booking-error").hidden=false;}
  finally{busy=false;submit.disabled=!selected;}
 });
 if(config.editing){
  service.value=config.editing.service;changeService();option.value=config.editing.option;resource.value=config.editing.resource;party.value=config.editing.party;
  // 2.14.0: the client moving from his link keeps service, operator and people: he chooses only when
  if(config.locked)(config.pick_resource?["party-wrap"]:["resource-wrap","party-wrap"]).forEach(id=>{$(id).hidden=true;});  // 2.14.0: another operator, same service
  if(config.editing.extra)service.disabled=true;  // 2.14.0: a visit moves with its services
  loadSlots();
 }else if(config.services.some(s=>s.id===(config.prefill?.service||new URLSearchParams(location.search).get("service")))){service.value=config.prefill?.service||new URLSearchParams(location.search).get("service");changeService();}else if(config.services.length===1){service.value=config.services[0].id;changeService();}
 // 2.14.0: from the waiting list's link: how many, the departure and who, then the times of that day
 if(config.prefill?.contact){const p=config.prefill;party.value=p.party||1;if(p.end_date)departure.value=p.end_date;
  [["first-name","first_name"],["last-name","last_name"],["customer-email","email"],["phone","phone"]].forEach(([id,k])=>{if($(id))$(id).value=p.contact[k]||"";});loadSlots();}
 // inside the WordPress frame the page is as tall as its content, so the frame can also shrink
 if(window.parent!==window){document.documentElement.classList.add("framed");new ResizeObserver(()=>{window.parent.postMessage({tdbsHeight:document.body.scrollHeight},"*");}).observe(document.body);}
 // 2.7.0: manual booking, the client from the address book or a new one
 let clientId="";
 const pick=$("client-pick");
 if(pick){
  const box=$("client-search"),list=$("client-results"),chosen=$("client-chosen");let timer=null,seq=0;
  const fields=["first-name","last-name","phone","customer-email"];
  const choose=(c,focus=true)=>{clientId=c.id;$("first-name").value=c.first_name;$("last-name").value=c.last_name;$("phone").value=c.phone;$("customer-email").value=c.email;
   chosen.querySelector("span").textContent="Cliente: "+(c.first_name+" "+c.last_name).trim()+(c.phone?" · "+c.phone:"");chosen.hidden=false;list.hidden=true;box.value="";if(focus)$("client-clear").focus();};
  const clear=()=>{clientId="";chosen.hidden=true;fields.forEach(f=>$(f).value="");box.focus();};
  $("client-clear").addEventListener("click",clear);
  fields.forEach(f=>$(f).addEventListener("input",()=>{if(clientId){clientId="";chosen.hidden=true;}}));
  box.addEventListener("input",()=>{clearTimeout(timer);const q=box.value.trim();if(q.length<2){list.hidden=true;return;}
   timer=setTimeout(async()=>{const n=++seq;try{const r=await fetch(location.pathname.replace(/bookings\/new\/$/,"contacts/search/")+"?q="+encodeURIComponent(q),{credentials:"same-origin"});
    const data=await r.json();if(n!==seq)return;list.replaceChildren();
    if(!data.results.length){const li=document.createElement("li");li.className="muted";li.textContent="Nessun cliente trovato: scrivi i dati qui sotto";list.append(li);}
    for(const c of data.results){const li=document.createElement("li"),b=document.createElement("button");b.type="button";
     const name=document.createElement("strong");name.textContent=(c.first_name+" "+c.last_name).trim();const sub=document.createElement("small");sub.textContent=[c.phone,c.email].filter(Boolean).join(" · ");
     b.append(name,sub);b.addEventListener("click",()=>choose(c));li.append(b);list.append(li);}
    list.hidden=false;}catch{}},250);});
  if(config.client)choose(config.client,false);
 }
})();
