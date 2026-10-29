"use strict";
(() => {
 const $=id=>document.getElementById(id),config=JSON.parse($("widget-config").textContent);
 const form=$("booking-form"),service=$("service"),option=$("option"),resource=$("resource"),party=$("party"),day=$("date"),departure=$("departure"),submit=$("booking-submit");
 let selected=null,controller=null,sequence=0,busy=false,requestKey=crypto.randomUUID().replace(/-/g,"");
 const select=(el,items,label)=>{el.replaceChildren();if(label)el.add(new Option(label,""));items.forEach(v=>el.add(new Option(v.name,v.id)));};
 const current=()=>config.services.find(s=>s.id===service.value);
 // one service: nothing to choose. The same service in several locations: choose the location.
 // Different services in several locations: the service, with its location.
 const places=new Set(config.services.map(s=>s.location)).size,names=new Set(config.services.map(s=>s.name)).size;
 if(config.services.length===1){$("service-wrap").hidden=true;select(service,config.services,"");}
 else if(names===1&&places>1){$("service-label").textContent="Sede";$("slot-status").textContent="Scegli la sede e la data.";select(service,config.services.map(s=>({id:s.id,name:s.location})),"Scegli la sede");}
 else{const word=config.service_label||"Servizio";$("service-label").textContent=word;select(service,config.services.map(s=>({id:s.id,name:(places>1?s.name+" ("+s.location+")":s.name)+(s.price?" · "+s.price:"")})),"Scegli un "+word.toLowerCase());}
 const kinds=new Set(config.services.map(s=>s.kind));if(kinds.size===1){const k=[...kinds][0];$("party-label").textContent=["shift","capacity","event"].includes(k)?"Persone":k==="day"?"Ospiti":"Persone / quantità";}
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
   if(field.type==="select"){input.add(new Option("Seleziona",""));for(const c of field.choices)input.add(new Option(c,c));}
   else{input.type=field.type==="checkbox"?"checkbox":field.type==="number"?"number":"text";if(input.type==="text")input.maxLength=500;if(input.type==="number"){input.min=0;input.max=100000;}}
   wrap.append(label,input);holder.append(wrap);
  }
 }
 function changeService(){
  const s=current();select(option,(s?.options||[]).map(o=>({id:o.id,name:o.name+(o.price?" · "+o.price:"")})),"Prestazione standard"+(s?.price?" · "+s.price:""));select(resource,s?.resources||[],s?.kind==="day"?"Qualsiasi unità libera":"Il primo disponibile");
  // 2.10.0: from the agenda, the operator of the free space clicked
  if(config.prefill?.resource&&s?.resources.some(r=>r.id===config.prefill.resource))resource.value=config.prefill.resource;
  pickers();
  $("option-wrap").hidden=!s?.options.length;$("resource-wrap").hidden=!(s?.kind==="resource"||(s?.kind==="day"&&s?.resources.length));
  $("resource-label").textContent=s?.resource_label||(s?.kind==="day"?"Unità / camera":"Operatore / risorsa");$("departure-wrap").hidden=s?.kind!=="day";departure.required=s?.kind==="day";
  party.max=s?.max_party||1;party.value=Math.min(Number(party.value)||1,Number(party.max));
  // one person per booking: no question to ask. Otherwise call it by what it counts
  $("party-wrap").hidden=Number(party.max)<=1;$("party-label").textContent=["shift","capacity","event"].includes(s?.kind)?"Persone":s?.kind==="day"?"Ospiti":"Persone / quantità";
  $("service-description").textContent=s?.description||"";
  // nothing left to choose in the first block: skip it
  // the choices above the row only when there is something to choose; the row holds who, how many and when
  $("what-grid").hidden=$("service-wrap").hidden&&$("option-wrap").hidden;$("date-label").textContent=s?.kind==="day"?"Arrivo":"Data";
  buildFields(s);loadSlots();
 }
 async function loadSlots(){
  const seq=++sequence;controller?.abort();controller=new AbortController();selected=null;submit.disabled=true;$("slots").replaceChildren();
  const s=current();
  if(!s||!day.value||(s.kind==="day"&&!departure.value)){$("slot-status").textContent=$("service-label").textContent==="Sede"?"Scegli la sede e la data.":s&&s.kind==="day"?"Scegli le date per vedere le disponibilità.":$("service-wrap").hidden?"Scegli la data per vedere gli orari liberi.":"Scegli servizio e data per vedere gli orari liberi.";return;}
  $("slot-status").textContent="Controllo delle disponibilità…";
  try{
   const query=new URLSearchParams({service:s.id,date:day.value,option:option.value,resource:resource.value,party:party.value,end_date:departure.value});
   const response=await fetch(config.slots+"?"+query,{signal:controller.signal,credentials:"same-origin"});
   let result;try{result=await response.json();}catch{throw Error("Impossibile verificare le disponibilità. Riprova.");}
   if(seq!==sequence)return;
   if(!response.ok)throw Error(result.error||"Disponibilità non verificabile.");
   const format=new Intl.DateTimeFormat("it-IT",{timeZone:config.timezone,hour:"2-digit",minute:"2-digit",hourCycle:"h23"});  // 2.6.0: 10:30, no CET
   const units=s.kind==="day"&&s.resources.length>0;
   const shown=resource.value||units?result.slots:result.slots.filter((v,i,a)=>a.findIndex(x=>x.start===v.start)===i);
   for(const slot of shown){
    const button=document.createElement("button");button.type="button";button.className="slot";button.setAttribute("aria-pressed","false");
    button.append(format.format(new Date(slot.start)));
    const sub=document.createElement("small");
    sub.textContent=units?slot.resource_name+(slot.capacity?" · fino a "+slot.capacity+" ospiti":""):(resource.value?slot.resource_name:"")||(s.kind==="capacity"&&s.turnover===false?"":"fino alle "+format.format(new Date(slot.end)));if(sub.textContent)button.append(sub);
    if(units&&slot.description){const d=document.createElement("small");d.textContent=slot.description;button.append(d);}
    button.addEventListener("click",()=>{selected=slot;$("slots").querySelectorAll("button").forEach(b=>b.setAttribute("aria-pressed",String(b===button)));submit.disabled=busy;});
    $("slots").append(button);
   }
   // 2.10.0: from the agenda, the time of the free space clicked is chosen already, when it is free for this service
   if(config.prefill?.time&&!selected){const hit=[...$("slots").querySelectorAll("button")].find(b=>b.firstChild?.textContent===config.prefill.time);if(hit){hit.click();hit.scrollIntoView({block:"nearest"});}}
   $("slot-status").textContent=result.slots.length?(units?"Scegli l'unità che preferisci.":"Scegli uno degli orari disponibili."):(units?"Nessuna unità libera per tutte le date scelte.":"Nessun orario disponibile: prova un'altra data.");
  }catch(e){if(e.name!=="AbortError"&&seq===sequence)$("slot-status").textContent=e.message;}
 }
 service.addEventListener("change",changeService);
 [option,resource,party,day,departure].forEach(el=>el.addEventListener("change",loadSlots));
 // 2.12.0: with operators' photos, one face each to choose with whom; the select stays, for the keyboard and the logic
 function pickers(){
  const box=$("resource-pick"),s=current();if(!box)return;mark();
  const people=s?.kind==="resource"?s.resources:[];
  box.replaceChildren();box.hidden=!people.some(r=>r.photo);resource.classList.toggle("has-pick",!box.hidden);
  if(box.hidden)return;
  const face=(r)=>{const b=document.createElement("button"),pic=document.createElement("span"),name=document.createElement("span");
   b.type="button";b.className="pick";b.dataset.value=r?r.id:"";pic.className="pick-face";name.className="pick-name";
   if(r&&r.photo){const img=document.createElement("img");img.src=r.photo;img.alt="";pic.append(img);}else pic.textContent=r?r.name.charAt(0).toUpperCase():"✱";
   name.textContent=r?r.name:"Il primo disponibile";b.append(pic,name);
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
  busy=true;submit.disabled=true;$("booking-error").hidden=true;
  try{
   const s=current(),answers={};
   document.querySelectorAll("[data-field]").forEach(el=>{answers[el.dataset.field]=el.type==="checkbox"?el.checked:el.value;});
   const payload={service:s.id,option:option.value,resource:resource.value||selected.resource||"",start:selected.start,occurrence:selected.occurrence,end_date:departure.value,party:party.value};
   if(!config.editing)Object.assign(payload,{first_name:$("first-name").value,last_name:$("last-name").value,email:$("customer-email").value,phone:$("phone").value,notes:$("notes").value,answers,marketing:$("marketing-consent").checked,notices:$("notices")?$("notices").checked:true,allergies:[...document.querySelectorAll("[data-allergen]:checked")].map(b=>b.value)});
   if(!config.editing&&clientId)payload.contact=clientId;
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
   let result;try{result=await response.json();}catch{throw Error("Risposta non disponibile. Riprova con gli stessi dati: la richiesta viene riconosciuta.");}
   if(!response.ok)throw Error(result.error||"Prenotazione non completata.");
   // done: the same data typed again later is a new booking, not this one (it may have been cancelled meanwhile)
   try{if(cacheKey)sessionStorage.removeItem(cacheKey);}catch{}
   if(result.redirect){location.assign(result.redirect);return;}
   form.hidden=true;$("booking-success").hidden=false;$("booking-reference").textContent="Riferimento: "+result.reference;
   $("cancel-link").href=result.cancel;$("booking-success").scrollIntoView({block:"center"});$("cancel-link").focus();
  }catch(e){$("booking-error").textContent=e.message;$("booking-error").hidden=false;}
  finally{busy=false;submit.disabled=!selected;}
 });
 if(config.editing){
  service.value=config.editing.service;changeService();option.value=config.editing.option;resource.value=config.editing.resource;party.value=config.editing.party;loadSlots();
 }else if(config.services.some(s=>s.id===new URLSearchParams(location.search).get("service"))){service.value=new URLSearchParams(location.search).get("service");changeService();}else if(config.services.length===1){service.value=config.services[0].id;changeService();}
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
