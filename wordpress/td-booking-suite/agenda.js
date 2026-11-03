"use strict";
// 2.17.1: the private agenda: the site gives only ciphertext; the password and the bookings stay in this page, decrypted here
document.querySelectorAll(".tdbs-agenda").forEach(box=>{
 const f=box.querySelector("form"),view=box.querySelector(".tdbs-agenda-view"),status=t=>{box.querySelector(".tdbs-agenda-status").textContent=t;};
 const KEY="tdbs-agenda",bin=s=>Uint8Array.from(atob(s),c=>c.charCodeAt(0)),text=b=>JSON.parse(new TextDecoder().decode(b));
 const h=(tag,cls,t)=>{const e=document.createElement(tag);if(cls)e.className=cls;if(t!=null)e.textContent=t;return e;};
 const shift=(d,n)=>{const x=new Date(d+"T12:00:00Z");x.setUTCDate(x.getUTCDate()+n);return x.toISOString().slice(0,10);};
 const label=d=>new Date(d+"T12:00:00Z").toLocaleDateString("it-IT",{weekday:"long",day:"numeric",month:"long",timeZone:"UTC"});
 let data=null,mode="day",day="";
 const today=()=>new Intl.DateTimeFormat("sv",{timeZone:data.timezone}).format(new Date());
 // the site's requests the PC has not taken yet: an AES key wrapped with the RSA key that travels inside the agenda
 async function pending(pkcs8,p){
  const priv=await crypto.subtle.importKey("pkcs8",bin(pkcs8),{name:"RSA-OAEP",hash:"SHA-1"},false,["decrypt"]);
  const key=await crypto.subtle.importKey("raw",await crypto.subtle.decrypt({name:"RSA-OAEP"},priv,bin(p.key)),"AES-GCM",false,["decrypt"]);
  return text(await crypto.subtle.decrypt({name:"AES-GCM",iv:bin(p.iv)},key,bin(p.ct)));
 }
 async function unlock(password){
  if(!window.crypto?.subtle){status("L’agenda si apre solo da una pagina sicura (https).");return null;}
  status("Apro l’agenda…");let list=null;
  try{const r=await fetch(box.dataset.rest+"agenda",{credentials:"omit",cache:"no-store"});
   if(r.status===429){status("Troppi tentativi: riprova tra un’ora.");return null;}
   if(r.ok)list=await r.json();}catch{}
  if(!list?.entries){status("Agenda non disponibile in questo momento.");return null;}
  const base=await crypto.subtle.importKey("raw",new TextEncoder().encode(password),"PBKDF2",false,["deriveKey"]);
  for(const e of list.entries){
   let d;
   try{const key=await crypto.subtle.deriveKey({name:"PBKDF2",hash:"SHA-256",salt:bin(e.salt),iterations:e.iterations},base,{name:"AES-GCM",length:256},false,["decrypt"]);
    d=text(await crypto.subtle.decrypt({name:"AES-GCM",iv:bin(e.nonce)},key,bin(e.ct)));}catch{continue;}
   if(e.pending)try{d.bookings.push(...await pending(d.key,e.pending));}catch{}
   delete d.key;d.checked=list.checked;return d;
  }
  status("Password non corretta.");return null;
 }
 function card(b){
  const el=h("details","tdbs-agenda-booking"+(b.pending?" is-pending":"")),s=h("summary"),when=b.start.slice(11)+"–"+b.end.slice(11),dl=h("dl");
  s.append(h("strong",null,when)," "+b.service+(b.client?" · "+b.client:""));
  if(b.pending)s.append(" ",h("span","tdbs-agenda-chip",b.status));
  [["Quando",label(b.start.slice(0,10))+", "+when],["Servizio",b.service],["Con",b.operator],["Cliente",b.client],["Telefono",b.phone],["Note",b.notes],["Stato",b.status]].forEach(([k,v])=>{
   if(!v)return;const dd=h("dd");
   if(k==="Telefono"){const a=h("a",null,v);a.href="tel:"+v.replace(/[^0-9+]/g,"");dd.append(a);}else dd.textContent=v;
   dl.append(h("dt",null,k),dd);});
  el.append(s,dl);return el;
 }
 function columns(d){
  const cols=data.operators.map(o=>({...o,items:[]})),other={name:"Senza operatore",items:[]};
  data.bookings.filter(b=>b.start.slice(0,10)===d).sort((a,b)=>a.start<b.start?-1:1).forEach(b=>(cols.find(c=>c.id===b.resource)||other).items.push(b));
  if(other.items.length)cols.push(other);
  const wrap=h("section","tdbs-agenda-day"),grid=h("div","tdbs-agenda-cols");
  cols.forEach(c=>{const col=h("div","tdbs-agenda-col");col.append(h("h4",null,c.name));
   if(!c.items.length)col.append(h("p","tdbs-agenda-empty","Nessuna prenotazione"));
   c.items.forEach(b=>col.append(card(b)));grid.append(col);});
  wrap.append(h("h3",null,label(d)),grid);return wrap;
 }
 function render(){
  const btn=(t,fn,pressed,name)=>{const b=h("button",null,t);b.type="button";if(pressed!=null)b.setAttribute("aria-pressed",String(pressed));if(name)b.setAttribute("aria-label",name);b.addEventListener("click",()=>{fn();render();});return b;};
  const step=mode==="day"?1:7,bar=h("div","tdbs-agenda-bar"),body=h("div","tdbs-agenda-body");
  bar.append(btn("Giorno",()=>{mode="day";},mode==="day"),btn("Settimana",()=>{mode="week";},mode==="week"),btn("‹",()=>{day=shift(day,-step);},null,"Indietro"),
   btn("Oggi",()=>{day=today();}),btn("›",()=>{day=shift(day,step);},null,"Avanti"),btn("Esci",close));
  if(mode==="day")body.append(columns(day));
  else{const first=shift(day,-((new Date(day+"T12:00:00Z").getUTCDay()+6)%7));for(let i=0;i<7;i++)body.append(columns(shift(first,i)));}
  const at=new Date(data.checked?data.checked*1000:Date.parse(data.generated));
  view.replaceChildren(h("p","tdbs-agenda-updated",data.label+" · Aggiornata alle "+at.toLocaleTimeString("it-IT",{hour:"2-digit",minute:"2-digit"})+" del "+at.toLocaleDateString("it-IT",{day:"2-digit",month:"2-digit"})),bar,body);
 }
 function close(){
  data=null;sessionStorage.removeItem(KEY);view.replaceChildren();view.hidden=true;f.hidden=false;status("Agenda chiusa.");
 }
 async function go(password,remember){
  const b=f.querySelector("[type=submit]");b.disabled=true;
  try{data=await unlock(password);}finally{b.disabled=false;}
  if(!data){sessionStorage.removeItem(KEY);return;}
  if(remember)sessionStorage.setItem(KEY,password);
  f.reset();f.hidden=true;view.hidden=false;status("");mode="day";day=today();render();
 }
 f.addEventListener("submit",e=>{e.preventDefault();go(f.elements.password.value,f.elements.remember.checked);});
 const saved=sessionStorage.getItem(KEY);if(saved)go(saved,true);
});
