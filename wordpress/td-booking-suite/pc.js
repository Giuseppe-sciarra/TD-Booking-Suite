"use strict";
// 2.17.0: Modalità PC: the booking form from the data the PC sent; the site keeps the request until the PC takes it
document.querySelectorAll(".tdbs-pc").forEach(box=>{
 const c=JSON.parse(box.dataset.config),f=box.querySelector("form"),el=n=>f.elements[n],q=s=>box.querySelector(s);
 const status=t=>{q(".tdbs-pc-status").textContent=t;};
 if(!window.UIkit)box.classList.add("tdbs-plain");  // 2.22.0: with no UIkit (YOOtheme) the plugin draws the fields itself
 let days={},pick=null,extra=[];  // 2.22.0: the services added to the visit after the first
 const fill=(sel,items,first)=>sel.replaceChildren(...(first?[new Option(first,"")]:[]),...items.map(([v,t])=>new Option(t,v)));
 const service=()=>c.services.find(s=>s.id===el("service").value);
 const label=d=>new Date(d+"T12:00:00").toLocaleDateString("it-IT",{weekday:"long",day:"numeric",month:"long"});
 fill(el("service"),c.services.map(s=>[s.id,s.name]));
 // the other appointment services of the same location can join the visit, one after the other
 const others=()=>c.services.filter(x=>x.id!==service().id&&x.location_id===service().location_id&&!extra.includes(x.id));
 function visit(){
  const box=q(".tdbs-pc-extra"),list=q(".tdbs-pc-extra-list");list.replaceChildren();
  extra.forEach(id=>{const x=c.services.find(s=>s.id===id);if(!x)return;const span=document.createElement("span");span.className="tdbs-pc-extra-item";span.textContent="+ "+x.name+" ";
   const b=document.createElement("button");b.type="button";b.className="tdbs-pc-remove";b.textContent="×";b.setAttribute("aria-label","Togli "+x.name);b.title="Togli";
   b.addEventListener("click",()=>{extra=extra.filter(v=>v!==id);visit();load();});span.append(b);list.append(span);});
  const add=q(".tdbs-pc-add");add.hidden=!others().length||extra.length>=4;box.hidden=!service().location_id||!c.services.some(x=>x.id!==service().id&&x.location_id===service().location_id);
  q(".tdbs-pc-pick")?.remove();
 }
 q(".tdbs-pc-add").addEventListener("click",()=>{
  q(".tdbs-pc-pick")?.remove();  // one picker at a time
  const sel=document.createElement("select");sel.className="uk-select tdbs-pc-pick";sel.setAttribute("aria-label","Servizio da aggiungere");
  fill(sel,others().map(x=>[x.id,x.name]),"Scegli il servizio");sel.addEventListener("change",()=>{if(sel.value){extra.push(sel.value);visit();load();}});
  q(".tdbs-pc-extra-list").append(sel);sel.focus();
 });
 function chosen(){
  const s=service();q(".tdbs-pc-description").textContent=s.description||"";q(".tdbs-pc-description-box").hidden=!s.description;extra=extra.filter(id=>id!==s.id&&c.services.some(x=>x.id===id&&x.location_id===s.location_id));visit();
  fill(el("option"),s.options.map(o=>[o.id,o.name]));q(".tdbs-pc-option").hidden=!s.options.length;
  fill(el("resource"),s.resources.map(r=>[r.id,r.name]),"Il primo disponibile");q(".tdbs-pc-resource").hidden=!s.picks||s.resources.length<2;
  load();
 }
 async function load(){
  pick=null;fill(el("time"),[],"Scegli l'orario");status("Carico gli orari…");
  const s=service(),p=new URLSearchParams({service:s.id,option:el("option").value,resource:el("resource").value,extra:extra.join(",")});
  try{const r=await fetch(c.rest+"slots?"+p,{credentials:"omit"});days=r.ok?(await r.json()).days:{};}catch{days={};}
  const keep=el("date").value;fill(el("date"),Object.keys(days).map(d=>[d,label(d)]));
  if(keep&&days[keep])el("date").value=keep;
  if(!Object.keys(days).length){status("Nessun orario libero nei prossimi giorni: contattaci.");fill(el("time"),[],"Nessun orario");return;}
  day();
 }
 function day(){
  pick=null;status("");
  const list=days[el("date").value]||[];
  fill(el("time"),list.map(([range])=>[range.slice(0,5),range.slice(0,5)]),"Scegli l'orario");  // 2.22.0: the start only, as at the desk: the end is never a promise
 }
 el("service").addEventListener("change",chosen);el("option").addEventListener("change",load);el("resource").addEventListener("change",load);el("date").addEventListener("change",day);
 el("time").addEventListener("change",()=>{const hit=(days[el("date").value]||[]).find(x=>x[0].slice(0,5)===el("time").value);pick=hit?[hit[0],hit[1]]:null;status("");});
 f.addEventListener("submit",async e=>{
  e.preventDefault();
  if(!pick){status("Scegli un orario.");el("time").focus();return;}
  if(!f.reportValidity())return;
  const body={nonce:c.nonce,service:service().id,option:el("option").value,resource:pick[1],any:!el("resource").value,date:el("date").value,time:pick[0].slice(0,5),extra,
   first_name:el("first_name").value,last_name:el("last_name").value,email:el("email").value,phone:el("phone").value,notes:el("notes").value,notices:el("notices").checked,marketing:!!el("marketing")?.checked};
  const button=f.querySelector("[type=submit]");button.disabled=true;
  try{
   const r=await fetch(c.rest+"book",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(body),credentials:"omit"}),d=await r.json();
   if(d.ok){f.hidden=true;const done=q(".tdbs-pc-done");done.textContent=d.message;done.hidden=false;return;}
   status(d.error||"Non è stato possibile inviare la richiesta: riprova.");if(d.reload)await load();
  }catch{status("Connessione assente: riprova.");}
  finally{button.disabled=false;}
 });
 chosen();
});
