"use strict";
(() => {
 const $=s=>document.querySelector(s);
 const choice=$("#theme-choice");
 let theme="auto";try{theme=localStorage.getItem("tdbs-theme")||"auto";}catch{}
 const queryTheme=new URLSearchParams(location.search).get("theme");if(document.body.classList.contains("public")&&["auto","light","dark"].includes(queryTheme))theme=queryTheme;
 const apply=()=>{document.documentElement.dataset.theme=theme;if(choice)choice.value=theme;};
 apply();
 choice?.addEventListener("change",()=>{theme=choice.value;try{localStorage.setItem("tdbs-theme",theme);}catch{}apply();});
 // the menu entry for this page, or for the section it belongs to (a client card is under Clienti)
 const links=[...document.querySelectorAll(".sidebar nav a")];
 const current=links.find(a=>a.pathname===location.pathname)||links.filter(a=>a.pathname.split("/").length>3&&!/^\/t\/[^/]+\/$/.test(a.pathname)&&location.pathname.startsWith(a.pathname)).sort((a,b)=>b.pathname.length-a.pathname.length)[0];
 current?.classList.add("active");
 // phones: the whole menu behind one button instead of a strip that scrolls sideways
 document.querySelectorAll("[data-menu]").forEach(b=>b.addEventListener("click",()=>{const side=b.closest(".sidebar");const open=side.classList.toggle("open");b.setAttribute("aria-expanded",String(open));}));
 // service form for the agency: only the fields of the chosen kind of booking, with labels that fit it
 const kindSelect=document.querySelector("#editor-form #id_kind");
 if(kindSelect){
  const showKind=()=>{const k=kindSelect.value;
   // fields without an input to mark (an empty list of resources): by name
   try{Object.entries(JSON.parse(kindSelect.dataset.fieldKinds||"{}")).forEach(([name,kinds])=>{const el=document.getElementById("id_"+name);const box=el?.closest(".field");if(box)box.hidden=!kinds.includes(k);});}catch{}
   document.querySelectorAll("#editor-form [data-kinds]").forEach(input=>{
    const box=input.closest(".field");if(!box)return;box.hidden=!input.dataset.kinds.split(" ").includes(k);
    const label=box.querySelector("label"),help=box.querySelector("small");
    try{const l=JSON.parse(input.dataset.labels||"{}");if(l[k]&&label&&label.htmlFor)label.textContent=l[k];}catch{}
    try{const h=JSON.parse(input.dataset.helps||"{}");if(h[k]&&help)help.textContent=h[k];}catch{}
   });};
  kindSelect.addEventListener("change",showKind);showKind();
 }
 // places by capacity: without time slots the duration and the pause after do not apply
 const turnover=$("#id_turnover");
 if(turnover){const timed=()=>["#id_duration","#id_buffer_after"].forEach(sel=>{const box=$(sel)?.closest(".field");if(box&&!(box.dataset.kindHidden))box.classList.toggle("off-turnover",!turnover.checked);});
  turnover.addEventListener("change",timed);timed();}
 // type of business: as many location names as locations, added on the fly past the fifth
 const profileForm=$("[data-profile]");
 if(profileForm){
  const box=profileForm.querySelector(".loc-names"),custom=profileForm.querySelector("#loc-custom"),customWrap=profileForm.querySelector(".custom-count");
  const ordinal=["Sede principale","Seconda sede","Terza sede","Quarta sede","Quinta sede"];
  const count=()=>{const v=profileForm.querySelector("[name=locations]:checked")?.value||"1";
   if(v!=="custom")return +v;const n=Math.round(+custom.value||6);return Math.max(1,Math.min(+custom.max||50,n));};
  const sync=()=>{const custom_on=profileForm.querySelector("[name=locations]:checked")?.value==="custom";customWrap.hidden=!custom_on;custom.disabled=!custom_on;
   const n=count();let fields=[...box.querySelectorAll("[data-loc]")];
   for(let i=fields.length+1;i<=n;i++){const f=fields[0].cloneNode(true);f.dataset.loc=i;const l=f.querySelector("label"),inp=f.querySelector("input");
    l.htmlFor=inp.id="loc-"+i;inp.name="name_"+i;l.textContent="Nome della sede "+i;inp.value=ordinal[i-1]||("Sede "+i);box.append(f);}
   box.querySelectorAll("[data-loc]").forEach(f=>{const on=+f.dataset.loc<=n;f.hidden=!on;f.querySelector("input").disabled=!on;});};
  profileForm.querySelectorAll("[name=locations]").forEach(r=>r.addEventListener("change",()=>{sync();if(r.value==="custom"&&r.checked)custom.focus();}));
  custom.addEventListener("input",sync);sync();}
 // 2.6.0: password managers (1Password, Bitwarden, LastPass, Dashlane, the browser) stay out of every page.
 // <body data-autofill="all">: sign-in, second factor, confirmation, invitation and own password keep them.
 // <body data-autofill="contact">: the public booking page keeps the browser's own name, email and phone
 // suggestions for the customer, without the managers' pop-ups.
 const fill=document.body.dataset.autofill;
 if(fill!=="all"){
  const ignore=el=>{el.setAttribute("data-1p-ignore","");el.setAttribute("data-lpignore","true");el.setAttribute("data-bwignore","");el.setAttribute("data-form-type","other");};
  document.querySelectorAll("form").forEach(f=>{ignore(f);if(fill!=="contact")f.setAttribute("autocomplete","off");});
  document.querySelectorAll("input:not([type=hidden]):not([type=checkbox]):not([type=radio]):not([type=submit]):not([type=button]):not([type=file]),textarea,select").forEach(el=>{
   ignore(el);
   if(fill!=="contact"||el.type==="password")el.setAttribute("autocomplete",el.type==="password"?"new-password":"off");
  });
 }
 // login: the kind of business changes every few seconds, fading out and in
 const ticker=$(".ticker[data-words]");
 if(ticker){
  const words=ticker.dataset.words.split("|");let i=0;
  setInterval(()=>{ticker.classList.add("out");
   setTimeout(()=>{i=(i+1)%words.length;ticker.textContent=words[i];ticker.classList.remove("out");ticker.classList.add("pre");
    void ticker.offsetWidth;ticker.classList.remove("pre");},380);},2800);
 }
 // SMTP: a provider fills server and port and tells what to use as user, password and sender
 const hint=$("#provider-hint");
 document.querySelectorAll(".provider").forEach(b=>b.addEventListener("click",()=>{
  document.querySelectorAll(".provider").forEach(o=>o.setAttribute("aria-pressed",String(o===b)));
  const host=$(".smtp-form #id_host"),port=$(".smtp-form #id_port");
  if(b.dataset.host&&host)host.value=b.dataset.host;
  if(b.dataset.port&&port)port.value=b.dataset.port;
  if(hint){hint.textContent=b.dataset.hint;hint.hidden=false;}
  (b.dataset.host?$(".smtp-form #id_username"):host)?.focus();
 }));
 // Clienti: search, state filter, grid or list (the choice is remembered on this browser)
 const board=$("[data-clients]");
 if(board){
  const search=$("#client-search"),empty=board.querySelector("[data-no-results]");
  let view="grid",state="all";try{view=localStorage.getItem("tdbs-clients-view")==="list"?"list":"grid";}catch{}
  const show=()=>{
   const term=(search?.value||"").trim().toLocaleLowerCase("it");let shown=0;
   board.querySelectorAll("[data-pane]").forEach(pane=>pane.hidden=pane.dataset.pane!==view);
   board.querySelectorAll("[data-client]").forEach(item=>{
    const ok=(!term||item.dataset.search.includes(term))&&(state==="all"||item.dataset.state===state);
    item.hidden=!ok;if(ok&&item.closest("[data-pane]").dataset.pane===view)shown++;
   });
   if(empty)empty.hidden=shown>0;
   if(!shown)board.querySelectorAll("[data-pane]").forEach(pane=>pane.hidden=true);
   document.querySelectorAll("[data-view]").forEach(b=>b.setAttribute("aria-pressed",String(b.dataset.view===view)));
   document.querySelectorAll("[data-status-filter]").forEach(b=>b.setAttribute("aria-pressed",String(b.dataset.statusFilter===state)));
  };
  document.querySelectorAll("[data-view]").forEach(b=>b.addEventListener("click",()=>{view=b.dataset.view;try{localStorage.setItem("tdbs-clients-view",view);}catch{}show();}));
  document.querySelectorAll("[data-status-filter]").forEach(b=>b.addEventListener("click",()=>{state=b.dataset.statusFilter;show();}));
  search?.addEventListener("input",show);
  show();
 }
 document.querySelectorAll("[data-copy]").forEach(button=>button.addEventListener("click",async()=>{const text=document.querySelector(button.dataset.copy)?.textContent||"";try{await navigator.clipboard.writeText(text);button.textContent="Copiato";}catch{const r=document.createRange();r.selectNodeContents(document.querySelector(button.dataset.copy));const s=getSelection();s.removeAllRanges();s.addRange(r);button.textContent="Selezionato: premi Cmd+C";}}));
 document.querySelectorAll("[data-reveal]").forEach(button=>button.addEventListener("click",()=>{const field=document.querySelector(button.dataset.reveal);if(!field)return;const show=field.type==="password";field.type=show?"text":"password";button.textContent=show?"Nascondi":"Mostra";button.setAttribute("aria-pressed",String(show));field.focus();}));
 // sign-in page: spotlight and tilt that follow the pointer, counter, rotating toast, trial form
 const brand=$("[data-spotlight]"),tilt=$("[data-tilt]");
 if(brand){
  brand.addEventListener("pointermove",e=>{
   const r=brand.getBoundingClientRect(),x=(e.clientX-r.left)/r.width,y=(e.clientY-r.top)/r.height;
   brand.style.setProperty("--mx",(x*100).toFixed(1)+"%");brand.style.setProperty("--my",(y*100).toFixed(1)+"%");
   if(tilt){tilt.style.setProperty("--ry",((x-.5)*10).toFixed(2)+"deg");tilt.style.setProperty("--rx",((.5-y)*8).toFixed(2)+"deg");}
  });
  brand.addEventListener("pointerleave",()=>{tilt?.style.setProperty("--rx","0deg");tilt?.style.setProperty("--ry","0deg");});
 }
 document.querySelectorAll("[data-count]").forEach(el=>{
  const end=+el.dataset.count,t0=performance.now()+1300;el.textContent="0";
  const step=t=>{const k=Math.min(1,Math.max(0,(t-t0)/1100));el.textContent=String(Math.round(end*(1-Math.pow(1-k,3))));if(k<1)requestAnimationFrame(step);};
  requestAnimationFrame(step);
 });
 const toast=$("[data-toasts]");
 if(toast){const texts=toast.dataset.toasts.split("|");let i=0;toast.addEventListener("animationiteration",()=>{i=(i+1)%texts.length;toast.querySelector("small").textContent=texts[i];});}
 const loginBox=$("#login-box"),trialBox=$("#trial-box"),cta=$("[data-trial-open]");
 const swap=(show,hide,url)=>{if(!show||!hide)return false;hide.hidden=true;show.hidden=false;show.classList.remove("swap-in");void show.offsetWidth;show.classList.add("swap-in");if(cta)cta.hidden=show===trialBox;try{history.replaceState(null,"",url);}catch{}show.querySelector("input:not([type=hidden])")?.focus({preventScroll:true});return true;};
 cta?.addEventListener("click",e=>{if(swap(trialBox,loginBox,cta.getAttribute("href")))e.preventDefault();});
 document.querySelectorAll("[data-trial-close]").forEach(a=>a.addEventListener("click",e=>{if(swap(loginBox,trialBox,a.getAttribute("href")))e.preventDefault();}));
 document.querySelectorAll("[data-confirm]").forEach(button=>button.addEventListener("click",e=>{if(!confirm(button.dataset.confirm))e.preventDefault();}));
 const csrf=()=>document.querySelector("[name=csrfmiddlewaretoken]")?.value||"";
 const bytes=s=>Uint8Array.from(atob(s.replace(/-/g,"+").replace(/_/g,"/")),c=>c.charCodeAt(0));
 const encoded=b=>btoa(String.fromCharCode(...new Uint8Array(b))).replace(/\+/g,"-").replace(/\//g,"_").replace(/=+$/,"");
 const request=async(url,data)=>{
  const r=await fetch(url,{method:"POST",headers:{"Content-Type":"application/json","X-CSRFToken":csrf()},body:JSON.stringify(data),credentials:"same-origin"});
  // the server asks to confirm who you are first: do it, then come back to this page
  if(r.redirected&&new URL(r.url).pathname==="/verify/confirm/"){location.assign("/verify/confirm/?next="+encodeURIComponent(location.pathname));return new Promise(()=>{});}
  let result;try{result=await r.json();}catch{throw Error("Operazione non disponibile. Ricarica e riprova.");}
  if(!r.ok)throw Error(result.error||"Operazione non consentita.");return result;
 };
 async function passkey(kind){
  const output=$("#auth-status"),button=$("#passkey-"+(kind==="login"?"login":"register"));
  output.textContent="Segui le istruzioni del dispositivo.";button.disabled=true;
  try{
   if(!window.PublicKeyCredential||!navigator.credentials)throw Error("Questo browser non supporta le passkey. Usa Authenticator.");
   const email=$("#email")?.value.trim()||"";
   if(kind==="login"&&!email)throw Error("Inserisci prima la tua email.");
   const begin=await request("/passkey/"+kind+"/begin/",{email});
   const remember=!!$("#remember")?.checked;
   const options=begin.options;
   options.challenge=bytes(options.challenge);
   let credential;
   if(kind==="register"){
    options.user.id=bytes(options.user.id);
    options.excludeCredentials=(options.excludeCredentials||[]).map(x=>({...x,id:bytes(x.id)}));
    credential=await navigator.credentials.create({publicKey:options});
   }else{
    options.allowCredentials=(options.allowCredentials||[]).map(x=>({...x,id:bytes(x.id)}));
    credential=await navigator.credentials.get({publicKey:options});
   }
   const r=credential.response;
   const response={clientDataJSON:encoded(r.clientDataJSON)};
   if(r.attestationObject){response.attestationObject=encoded(r.attestationObject);response.transports=r.getTransports?.()||[];}
   else{response.authenticatorData=encoded(r.authenticatorData);response.signature=encoded(r.signature);response.userHandle=r.userHandle?encoded(r.userHandle):null;}
   const result=await request("/passkey/"+kind+"/finish/",{id:begin.id,remember,name:$("#key-name")?.value||"Passkey",credential:{id:credential.id,rawId:encoded(credential.rawId),type:credential.type,response,clientExtensionResults:credential.getClientExtensionResults()}});
   location.assign(result.redirect);
  }catch(e){output.textContent=e.name==="NotAllowedError"?"Operazione annullata o scaduta. Puoi riprovare.":e.message;}
  finally{button.disabled=false;}
 }
 $("#passkey-login")?.addEventListener("click",()=>passkey("login"));
 $("#passkey-register")?.addEventListener("click",()=>passkey("register"));
 const holder=$("#custom-fields"),hidden=$("#id_fields");
 if(holder&&hidden){
  let fields=[];try{fields=JSON.parse(hidden.value||"[]");}catch{}
  const sync=()=>{hidden.value=JSON.stringify(fields);};
  function render(){
   holder.replaceChildren();
   fields.forEach((f,i)=>{
    const row=document.createElement("div");row.className="custom-row";
    const label=document.createElement("label");label.textContent="Domanda "+(i+1);
    const input=document.createElement("input");input.value=f.label||"";input.maxLength=80;input.required=true;input.addEventListener("input",()=>{f.label=input.value;sync();});label.append(input);
    const typelabel=document.createElement("label");typelabel.textContent="Tipo di risposta";
    const type=document.createElement("select");
    [["text","Testo"],["number","Numero"],["checkbox","Casella sì/no"],["select","Scelta da elenco"]].forEach(([v,t])=>type.add(new Option(t,v)));
    type.value=f.type||"text";typelabel.append(type);
    const choiceslabel=document.createElement("label");choiceslabel.textContent="Scelte, una per riga";
    const choices=document.createElement("textarea");choices.rows=3;choices.value=(f.choices||[]).join("\n");choiceslabel.append(choices);choiceslabel.hidden=f.type!=="select";
    choices.addEventListener("input",()=>{f.choices=choices.value.split("\n").map(v=>v.trim()).filter(Boolean);sync();});
    type.addEventListener("change",()=>{f.type=type.value;choiceslabel.hidden=f.type!=="select";sync();});
    const required=document.createElement("label");required.className="check";
    const checkbox=document.createElement("input");checkbox.type="checkbox";checkbox.checked=f.required===true;checkbox.addEventListener("change",()=>{f.required=checkbox.checked;sync();});required.append(checkbox,"Risposta obbligatoria");
    const remove=document.createElement("button");remove.type="button";remove.className="ghost small";remove.textContent="Rimuovi domanda";remove.addEventListener("click",()=>{fields.splice(i,1);render();sync();});
    row.append(label,typelabel,choiceslabel,required,remove);holder.append(row);
   });
  }
  $("#add-field").addEventListener("click",()=>{if(fields.length>=12)return;fields.push({key:"campo_"+crypto.randomUUID().replace(/-/g,"").slice(0,12),label:"",type:"text",required:false,choices:[]});render();sync();});
  render();sync();
 }
 // 2.8.0: a link to a closed <details> (Registra il trattamento) opens it and puts the cursor on what was done
 const openTarget=()=>{let d=null;try{d=location.hash&&document.getElementById(decodeURIComponent(location.hash.slice(1)));}catch{}
  if(d&&d.tagName==="DETAILS"){d.open=true;(d.querySelector("[name=service]")||d.querySelector("input:not([type=hidden]),textarea"))?.focus({preventScroll:true});}};
 window.addEventListener("hashchange",openTarget);openTarget();
 document.addEventListener("click",e=>{const a=e.target.closest('a[href^="#"]');if(a&&a.getAttribute("href")===location.hash)openTarget();});
})();
