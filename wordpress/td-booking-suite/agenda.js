"use strict";
// 2.19.0: Nuovo appuntamento in the private agenda: a free time from the PC's data, or Altro orario that the PC checks
document.querySelectorAll(".tdbs-agenda-form").forEach(f=>{
 const c=JSON.parse(f.dataset.config),el=n=>f.elements[n],q=s=>f.querySelector(s);
 const slots=q(".tdbs-pc-slots"),status=t=>{q(".tdbs-pc-status").textContent=t;};
 let pick=null;
 const fill=(sel,items,first)=>sel.replaceChildren(...(first?[new Option(first,"")]:[]),...items.map(([v,t])=>new Option(t,v)));
 const service=()=>c.services.find(s=>s.id===el("service").value);
 const press=b=>slots.querySelectorAll("button").forEach(x=>x.setAttribute("aria-pressed",String(x===b)));
 fill(el("service"),c.services.map(s=>[s.id,s.name]));
 function chosen(){
  const s=service();
  fill(el("option"),s.options.map(o=>[o.id,o.name]));q(".tdbs-agenda-option").hidden=!s.options.length;
  fill(el("resource"),s.resources.map(r=>[r.id,r.name]),s.resources.length>1?"Chiunque":"");
  load();
 }
 async function load(){
  pick=null;slots.replaceChildren();
  if(!el("date").value){status("Scegli il giorno.");return;}
  status("Carico gli orari…");
  const p=new URLSearchParams({service:service().id,option:el("option").value,resource:el("resource").value});
  let days={};
  try{const r=await fetch(c.rest+"slots?"+p,{credentials:"omit"});days=r.ok?(await r.json()).days:{};}catch{status("Connessione assente: riprova.");return;}
  const list=days[el("date").value]||[];
  slots.replaceChildren(...list.map(([range])=>{
   const b=document.createElement("button");b.type="button";b.className="tdbs-pc-slot";b.textContent=range.slice(0,5);b.setAttribute("aria-pressed","false");
   b.addEventListener("click",()=>{pick=range.slice(0,5);el("other").value="";press(b);status("Orario scelto: "+pick+".");});
   return b;}));
  status(list.length?"Scegli l'orario, oppure scrivi un altro orario.":"Nessun orario libero in questo giorno: scrivi un altro orario.");
 }
 el("service").addEventListener("change",chosen);["option","resource","date"].forEach(n=>el(n).addEventListener("change",load));
 el("other").addEventListener("input",()=>{if(el("other").value){pick=null;press(null);}});
 f.addEventListener("submit",async e=>{
  e.preventDefault();
  const other=el("other").value;
  if(!pick&&!other){status("Scegli un orario libero o scrivi un altro orario.");return;}
  if(other&&!el("resource").value){status("Con «Altro orario» scegli l'operatore.");return;}
  if(!f.reportValidity())return;
  const body={nonce:c.nonce,service:service().id,option:el("option").value,resource:el("resource").value,date:el("date").value,time:other||pick,other:!!other,
   first_name:el("first_name").value,last_name:el("last_name").value,phone:el("phone").value,email:el("email").value,notes:el("notes").value};
  const button=f.querySelector("[type=submit]");button.disabled=true;
  try{
   const r=await fetch(c.rest+"agenda/book",{method:"POST",headers:{"Content-Type":"application/json","X-WP-Nonce":c.wp},body:JSON.stringify(body),credentials:"same-origin"}),d=await r.json();
   if(d.ok){const u=new URL(location.href);u.searchParams.set("vista","giorno");u.searchParams.set("giorno",body.date);u.searchParams.set("nuovo","1");location.assign(u);return;}
   status(d.error||d.message||"Non è stato possibile salvare l'appuntamento: riprova.");if(d.reload)await load();
  }catch{status("Connessione assente: riprova.");}
  finally{button.disabled=false;}
 });
 chosen();
});

// 2.22.1: the agenda grid: a tap on a booking opens its detail in the sheet; × , the backdrop or Esc close it
(()=>{const sheet=document.querySelector(".tdbs-ag-sheet");if(!sheet)return;
 const list=sheet.querySelector("[data-detail-list]"),labels={quando:"Quando",servizio:"Servizio",con:"Con",cliente:"Cliente",telefono:"Telefono",note:"Note",stato:"Stato"};
 document.addEventListener("click",e=>{const b=e.target.closest(".tdbs-ag-item");if(!b)return;
  let d={};try{d=JSON.parse(b.dataset.detail||"{}");}catch{}
  list.replaceChildren();
  for(const k in labels){const v=d[k];if(!v)continue;const dt=document.createElement("dt"),dd=document.createElement("dd");dt.textContent=labels[k];
   if(k==="telefono"){const a=document.createElement("a");a.href="tel:"+v.replace(/[^0-9+]/g,"");a.textContent=v;dd.append(a);}else dd.textContent=v;
   list.append(dt,dd);}
  if(typeof sheet.showModal==="function")sheet.showModal();else sheet.setAttribute("open","");});
 sheet.addEventListener("click",e=>{if(e.target===sheet||e.target.closest("[data-close]"))sheet.close?sheet.close():sheet.removeAttribute("open");});
 // the operators' drop-down closes with a click outside or Esc
 const pick=document.querySelector("[data-pick]");
 if(pick){document.addEventListener("click",e=>{if(pick.open&&!pick.contains(e.target))pick.open=false;});
  document.addEventListener("keydown",e=>{if(e.key==="Escape"&&pick.open){pick.open=false;pick.querySelector("summary").focus();}});}
 // the agenda opens on the working hours, not at the top of the night
 const scroll=document.querySelector(".tdbs-ag-scroll"),now=document.querySelector(".tdbs-ag-now"),first=document.querySelector(".tdbs-ag-item");
 if(scroll){const row=parseFloat(getComputedStyle(scroll.querySelector(".tdbs-ag")).getPropertyValue("--row"))||24;const at=now?parseFloat(now.style.getPropertyValue("--at")):first?parseFloat(first.style.getPropertyValue("--top")):0;scroll.scrollTop=Math.max(0,at*row-row*4);}
})();
