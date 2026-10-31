"use strict";
(() => {
 const $=s=>document.querySelector(s);
 const choice=$("#theme-choice");
 // the person's own choice, otherwise the portal's default set in Impostazioni generali
 let theme=document.documentElement.dataset.theme||"auto";try{theme=localStorage.getItem("tdbs-theme")||theme;}catch{}
 const queryTheme=new URLSearchParams(location.search).get("theme");if(document.body.classList.contains("public")&&["auto","light","dark"].includes(queryTheme))theme=queryTheme;
 const apply=()=>{document.documentElement.dataset.theme=theme;if(choice)choice.value=theme;};
 apply();
 choice?.addEventListener("change",()=>{theme=choice.value;try{localStorage.setItem("tdbs-theme",theme);}catch{}apply();});
 // the menu entry for this page, or for the section it belongs to (a client card is under Clienti)
 const links=[...document.querySelectorAll(".sidebar nav a")];
 const current=links.find(a=>a.pathname===location.pathname)||links.filter(a=>a.pathname.split("/").length>3&&!/^\/t\/[^/]+\/$/.test(a.pathname)&&location.pathname.startsWith(a.pathname)).sort((a,b)=>b.pathname.length-a.pathname.length)[0];
 current?.classList.add("active");
 current?.closest("details")?.setAttribute("open","");  // 2.10.0: the folded group of the page in use opens
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
 // 2.10.0: the day agenda, placed on its grid here (the page allows no inline styles); again after each refresh
 const lay=()=>document.querySelectorAll("[data-agenda]").forEach(agenda=>{agenda.style.setProperty("--rows",agenda.dataset.rows);agenda.style.setProperty("--cols",agenda.dataset.cols);
  agenda.querySelectorAll("[data-row]").forEach(el=>{el.style.gridRow=el.dataset.row+" / "+(el.dataset.end||+el.dataset.row+1);el.style.gridColumn=el.dataset.col;});
  agenda.querySelectorAll(".agenda-head [data-col]").forEach(el=>{el.style.gridColumn=el.dataset.col;});
  // 2.11.0: a day of the week puts overlapping appointments side by side, one lane each (2.14.0: the day too)
  agenda.querySelectorAll("[data-lanes]").forEach(el=>el.style.setProperty("--lanes",el.dataset.lanes));
  agenda.querySelectorAll("[data-lane]").forEach(el=>el.style.setProperty("--lane",el.dataset.lane));
  // 2.14.0: the lower edge of an appointment to drag, to change how long it lasts
  agenda.querySelectorAll("[data-move]").forEach(el=>{if(!el.querySelector(".agenda-grip")){const g=document.createElement("span");g.className="agenda-grip";g.setAttribute("aria-hidden","true");el.append(g);}});
  const now=agenda.querySelector("[data-now]");if(now)now.style.top="calc(var(--row) * "+now.dataset.now+")";});
 lay();
 document.querySelectorAll("[data-agenda]").forEach(agenda=>(agenda.querySelector("[data-now]")||agenda.querySelector(".agenda-item"))?.scrollIntoView({block:"nearest"}));
 document.querySelectorAll("form[data-autosubmit] input[type=date],form[data-autosubmit] select,form[data-autosubmit] input[type=checkbox]").forEach(el=>el.addEventListener("change",()=>el.form.requestSubmit()));
 // 2.11.0: the tabs of the client card; without the script every panel shows, one after the other. A link to
 // something inside a panel (#nuovo-trattamento) opens that panel first
 document.querySelectorAll("[data-tabs]").forEach(box=>{
  const tabs=[...box.querySelectorAll("[data-tab]")];
  const show=name=>{if(!tabs.some(t=>t.dataset.tab===name))name=box.dataset.default;
   tabs.forEach(t=>{const on=t.dataset.tab===name,panel=document.getElementById(t.dataset.tab);t.setAttribute("aria-selected",on);t.tabIndex=on?0:-1;if(panel)panel.hidden=!on;});
   document.querySelectorAll("[data-tab-link]").forEach(a=>a.classList.toggle("is-current",a.dataset.tabLink===name));};
  const fromHash=()=>{const target=location.hash.length>1?document.getElementById(decodeURIComponent(location.hash.slice(1))):null;return target?.closest(".tab-panel")?.id||box.dataset.default;};
  show(fromHash());
  tabs.forEach((t,i)=>{
   t.addEventListener("click",e=>{e.preventDefault();show(t.dataset.tab);history.replaceState(null,"","#"+t.dataset.tab);});
   t.addEventListener("keydown",e=>{const step={ArrowRight:1,ArrowLeft:-1}[e.key];if(!step)return;e.preventDefault();const next=tabs[(i+step+tabs.length)%tabs.length];next.focus();next.click();});});
  document.querySelectorAll("[data-tab-link]").forEach(a=>a.addEventListener("click",()=>show(a.dataset.tabLink)));
  // 2.13.0: a link to something in a hidden panel (a day of the week on the dashboard) shows it once it is open
  window.addEventListener("hashchange",()=>{show(fromHash());document.getElementById(decodeURIComponent(location.hash.slice(1)))?.scrollIntoView({block:"nearest",behavior:"smooth"});});
 });
 // 2.13.0: the emails to the clients: the preview with an example booking, a field put where the cursor is, the
 // standard text back with one click
 document.querySelectorAll("[data-mails]").forEach(box=>{
  const sample=JSON.parse(document.getElementById("mail-sample").textContent);
  const fill=t=>t.replace(/\{(\w+)\}/g,(m,k)=>k in sample?sample[k]:m);
  box.querySelectorAll("[data-mail]").forEach(card=>{
   const subject=card.querySelector("[data-mail-subject]"),body=card.querySelector("[data-mail-body]"),on=card.querySelector("[data-mail-on]");
   let last=body;
   const draw=()=>{card.querySelector("[data-preview-subject]").textContent=fill(subject.value||subject.dataset.default);
    card.querySelector("[data-preview-body]").textContent=fill(body.value||body.dataset.default);
    card.classList.toggle("is-on",on.checked);on.nextElementSibling.textContent=on.checked?"Parte":"Spenta";};
   [subject,body].forEach(f=>{f.addEventListener("input",draw);f.addEventListener("focus",()=>{last=f;});});
   on.addEventListener("change",draw);
   card.querySelectorAll("[data-insert]").forEach(b=>b.addEventListener("click",()=>{
    const a=last.selectionStart??last.value.length,z=last.selectionEnd??a;
    last.setRangeText(b.dataset.insert,a,z,"end");last.focus();draw();}));
   card.querySelector("[data-mail-reset]")?.addEventListener("click",()=>{subject.value=subject.dataset.default;body.value=body.dataset.default;draw();});
   draw();});
 });
 // 2.13.0: Logo e aspetto: the chosen logo, colour and line are seen at once in the menu and on the booking page
 document.querySelectorAll("[data-brand]").forEach(form=>{
  const file=form.querySelector("[data-brand-file]"),color=form.querySelector("[data-brand-color]"),line=form.querySelector("[data-brand-tagline]");
  const remove=form.querySelector("[data-brand-remove]"),preview=form.querySelector("[data-brand-preview]"),marks=[...form.querySelectorAll("[data-brand-mark]")];
  const original=marks.map(m=>m.innerHTML),letter=(form.dataset.name||"?").trim().charAt(0).toUpperCase();
  const paint=()=>{form.style.setProperty("--pick",color.value);};
  const show=src=>marks.forEach((m,i)=>{m.replaceChildren();
   if(src){const img=document.createElement("img");img.src=src;img.alt="";m.append(img);}
   else if(src===null){const s=document.createElement("span");s.className="brand-letter";s.textContent=letter;m.append(s);}
   else m.innerHTML=original[i];});
  file.addEventListener("change",()=>{const f=file.files[0];if(remove)remove.checked=false;
   if(!f){show(undefined);return;}
   const pick=form.querySelector("[data-brand-pick]");pick.classList.remove("danger-text");
   if(f.size>1024*1024){pick.textContent="Troppo grande: il logo va al massimo di 1 MB";pick.classList.add("danger-text");file.value="";show(undefined);return;}
   pick.textContent=f.name;
   const r=new FileReader();r.onload=()=>show(r.result);r.readAsDataURL(f);});
  remove?.addEventListener("change",()=>{if(remove.checked){file.value="";show(null);}else show(undefined);});
  color.addEventListener("input",paint);
  form.querySelectorAll("[data-color]").forEach(b=>b.addEventListener("click",()=>{color.value=b.dataset.color;paint();}));
  line.addEventListener("input",()=>{preview.querySelector("[data-brand-tagline-out]").textContent=line.value;});
  paint();
 });
 // 2.12.0: a field's suggestions (a datalist) in the program's own list instead of the browser's black box:
 // what is typed filters them, arrows and Enter choose, Esc closes
 document.querySelectorAll("input[list]").forEach((input,n)=>{
  const source=document.getElementById(input.getAttribute("list"));if(!source)return;
  const values=[...new Set([...source.options].map(o=>o.value).filter(Boolean))];
  input.removeAttribute("list");input.autocomplete="off";
  const box=document.createElement("ul"),wrap=document.createElement("div");
  box.className="suggest";box.id="suggest-"+n;box.setAttribute("role","listbox");box.hidden=true;wrap.className="suggest-wrap";
  input.setAttribute("role","combobox");input.setAttribute("aria-autocomplete","list");input.setAttribute("aria-expanded","false");input.setAttribute("aria-controls",box.id);
  input.before(wrap);wrap.append(input,box);
  let active=-1;
  const close=()=>{box.hidden=true;input.setAttribute("aria-expanded","false");input.removeAttribute("aria-activedescendant");active=-1;};
  const pick=value=>{input.value=value;close();input.dispatchEvent(new Event("change",{bubbles:true}));};
  const mark=i=>{const items=[...box.children];active=i;items.forEach((li,j)=>li.setAttribute("aria-selected",String(j===i)));
   if(items[i]){input.setAttribute("aria-activedescendant",items[i].id);items[i].scrollIntoView({block:"nearest"});}};
  const open=()=>{
   const q=input.value.trim().toLocaleLowerCase("it");
   const found=values.filter(v=>{const t=v.toLocaleLowerCase("it");return t!==q&&(!q||t.includes(q));}).slice(0,8);
   box.replaceChildren(...found.map((v,j)=>{const li=document.createElement("li"),at=q?v.toLocaleLowerCase("it").indexOf(q):-1;
    li.id=box.id+"-"+j;li.setAttribute("role","option");
    if(at>=0){const strong=document.createElement("mark");strong.textContent=v.slice(at,at+q.length);li.append(v.slice(0,at),strong,v.slice(at+q.length));}else li.textContent=v;
    li.addEventListener("mousedown",e=>{e.preventDefault();pick(v);});return li;}));
   box.hidden=!found.length;input.setAttribute("aria-expanded",String(found.length>0));active=-1;};
  input.addEventListener("input",open);input.addEventListener("focus",open);input.addEventListener("blur",close);
  input.addEventListener("keydown",e=>{
   if(box.hidden){if(e.key==="ArrowDown"){e.preventDefault();open();}return;}
   const count=box.children.length;
   if(e.key==="ArrowDown"||e.key==="ArrowUp"){e.preventDefault();mark((active+(e.key==="ArrowDown"?1:-1)+count)%count);}
   else if(e.key==="Enter"&&active>=0){e.preventDefault();pick(box.children[active].textContent);}
   else if(e.key==="Escape"){e.preventDefault();close();}});
 });
 // 2.11.0: the client's discount asks how much only once a kind is chosen
 document.querySelectorAll("[data-discount-kind]").forEach(kind=>{const value=kind.form.querySelector("[data-discount-value]");kind.addEventListener("change",()=>{value.hidden=!kind.value;if(kind.value)value.querySelector("input").focus();});});
 // 2.10.0: an operator's working days show only with Ha giorni e orari suoi
 document.querySelectorAll("[data-own-hours]").forEach(box=>{const on=box.querySelector("[name=own_hours]"),grid=box.querySelector(".hours-grid");
  const sync=()=>{grid.hidden=!on.checked;};on.addEventListener("change",sync);sync();});
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
  custom.addEventListener("input",sync);sync();
  // another type replaces the catalog: it asks to be confirmed, the same type keeps everything
  const current=profileForm.dataset.current,change=profileForm.querySelector("[data-type-change]");
  const typed=()=>{const v=profileForm.querySelector("[name=business_type]:checked")?.value;const other=!!current&&!!v&&v!==current;
   if(change){change.hidden=!other;change.querySelector("input").required=other;}
   profileForm.querySelector("[data-apply-same]").hidden=other||!current;profileForm.querySelector("[data-apply-new]").hidden=!other&&!!current;};
  profileForm.querySelectorAll("[name=business_type]").forEach(r=>r.addEventListener("change",typed));typed();}
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
 // one listener for the whole page: the buttons of a list refreshed in place ask too (2.14.0)
 document.addEventListener("click",e=>{const button=e.target.closest("[data-confirm]");if(button&&!confirm(button.dataset.confirm))e.preventDefault();});
 const csrf=()=>document.querySelector("[name=csrfmiddlewaretoken]")?.value||"";
 // 2.14.0: the agenda at the desk. An appointment dragged to another time or operator (another day in the week), or
 // its lower edge to change how long it lasts: mouse at once, a finger after a long press, a confirmation, then the
 // desk's rules on the server, with Forza where the role has it. The Sposta page stays the way by keyboard
 const dialog=$("[data-drop-dialog]");
 const veiledName=text=>{const s=document.createElement("span");s.className="pv";s.textContent=text;return s;};
 const ask=(title,parts,yes,danger)=>new Promise(done=>{
  const ok=dialog.querySelector("[data-yes]"),no=dialog.querySelector("[data-no]");
  dialog.querySelector("[data-title]").textContent=title;dialog.querySelector("[data-text]").replaceChildren(...parts);
  ok.hidden=!yes;ok.textContent=yes||"";ok.className=danger?"danger":"primary";no.textContent=yes?"Annulla":"Chiudi";
  const end=v=>{ok.onclick=no.onclick=dialog.oncancel=null;dialog.close();done(v);};
  ok.onclick=()=>end(true);no.onclick=()=>end(false);dialog.oncancel=e=>{e.preventDefault();end(false);};
  dialog.showModal();(yes?ok:no).focus();});
 const say=text=>{const t=document.createElement("div");t.className="toast";t.setAttribute("role","status");t.textContent=text;document.body.append(t);setTimeout(()=>t.classList.add("out"),3600);setTimeout(()=>t.remove(),4200);};
 const hm=m=>String(Math.floor(m/60)).padStart(2,"0")+":"+String(m%60).padStart(2,"0");
 const lanes=agenda=>[...agenda.querySelectorAll("[data-drop]")];
 let drag=null,swallow=false;
 const settle=()=>{const d=drag;drag=null;if(!d)return;clearTimeout(d.timer);d.ghost?.remove();d.item.classList.remove("is-lifted");d.agenda.classList.remove("is-dragging");
  lanes(d.agenda).forEach(l=>l.classList.remove("is-target"));};
 const paint=()=>{const d=drag,t=d.to,b=d.body.getBoundingClientRect(),l=t.lane.getBoundingClientRect();
  d.ghost.style.left=(l.left-b.left+3)+"px";d.ghost.style.width=Math.max(40,l.width-6)+"px";
  d.ghost.style.top=(l.top-b.top+(t.start-d.first)/15*d.row+1)+"px";d.ghost.style.height=Math.max(d.row,t.minutes/15*d.row)-2+"px";
  d.tag.textContent=hm(t.start)+"–"+hm(t.start+t.minutes)+(t.lane!==d.lane?" · "+(t.lane.dataset.name||t.lane.dataset.label||""):"");
  lanes(d.agenda).forEach(x=>x.classList.toggle("is-target",x===t.lane));};
 const track=()=>{const d=drag,b=d.body.getBoundingClientRect(),at=m=>Math.round(m/d.snap)*d.snap;
  if(d.resize)d.to.minutes=Math.max(15,Math.min(d.last,at(d.first+(d.py-b.top)/d.row*15))-d.start);
  else{d.to.start=Math.max(d.first,Math.min(d.last-d.minutes,at(d.first+(d.py-d.dy-b.top)/d.row*15)));
   d.to.lane=lanes(d.agenda).find(l=>{const r=l.getBoundingClientRect();return d.px>=r.left&&d.px<r.right;})||d.to.lane;}
  paint();};
 const edge=()=>{const d=drag;if(!d?.live||d.asking)return;
  const top=d.agenda.querySelector(".agenda-head").getBoundingClientRect().bottom,dy=d.py<Math.max(top,0)+16?-12:d.py>innerHeight-40?12:0;if(dy){scrollBy(0,dy);track();}
  // narrow screens: the agenda scrolls sideways inside its card
  const card=d.agenda.closest(".agenda-card"),r=card?.getBoundingClientRect();
  if(card&&card.scrollWidth>card.clientWidth){const dx=d.px<r.left+44?-12:d.px>r.right-44?12:0;if(dx){card.scrollLeft+=dx;track();}}
  requestAnimationFrame(edge);};
 const lift=()=>{const d=drag;if(!d||d.live)return;d.live=true;
  d.body=d.agenda.querySelector(".agenda-body");d.row=parseFloat(getComputedStyle(d.agenda).getPropertyValue("--row"))||22;
  d.first=+d.agenda.dataset.first;d.last=d.first+ +d.agenda.dataset.rows*15;d.snap=d.resize?15:+d.item.dataset.snap||15;
  d.dy=d.y-d.item.getBoundingClientRect().top;
  d.ghost=d.item.cloneNode(true);d.ghost.className+=" agenda-ghost"+(d.resize?" is-resize":"");["href","data-move","data-row","data-col","data-lane"].forEach(a=>d.ghost.removeAttribute(a));
  d.ghost.style.gridRow=d.ghost.style.gridColumn="auto";d.ghost.removeAttribute("title");d.ghost.querySelector(".agenda-grip")?.remove();
  d.tag=document.createElement("span");d.tag.className="ghost-time";d.ghost.prepend(d.tag);
  d.body.append(d.ghost);d.item.classList.add("is-lifted");d.agenda.classList.add("is-dragging");
  try{d.item.setPointerCapture(d.id);}catch{}
  if(d.touch)navigator.vibrate?.(12);  // the long press took
  d.to={lane:d.lane,start:d.start,minutes:d.minutes};paint();requestAnimationFrame(edge);};
 document.addEventListener("pointerdown",e=>{const item=e.target.closest("[data-move]");if(!item||e.button>0||drag||!dialog)return;
  const agenda=item.closest("[data-agenda]"),resize=!!e.target.closest(".agenda-grip");
  const [h,m]=item.dataset.start.split(":");
  drag={item,agenda,resize,touch:e.pointerType!=="mouse",id:e.pointerId,x:e.clientX,y:e.clientY,px:e.clientX,py:e.clientY,start:h*60+ +m,minutes:+item.dataset.minutes,
   lane:item.closest("[data-drop]")||agenda.querySelector('[data-drop][data-col="'+item.dataset.col+'"]')};
  if(!drag.lane){drag=null;return;}
  if(resize){e.preventDefault();lift();}else if(drag.touch)drag.timer=setTimeout(lift,350);});
 document.addEventListener("pointermove",e=>{const d=drag;if(!d||e.pointerId!==d.id||d.asking)return;d.px=e.clientX;d.py=e.clientY;
  if(!d.live){if(Math.hypot(e.clientX-d.x,e.clientY-d.y)>(d.touch?10:4)){if(d.touch)settle();else lift();}return;}
  e.preventDefault();track();});
 document.addEventListener("touchmove",e=>{if(drag?.live)e.preventDefault();},{passive:false});
 document.addEventListener("contextmenu",e=>{if(drag&&e.target.closest("[data-move]"))e.preventDefault();});
 document.addEventListener("click",e=>{if(swallow&&e.target.closest("[data-move]")){e.preventDefault();e.stopPropagation();}},true);
 document.addEventListener("pointercancel",e=>{if(drag&&e.pointerId===drag.id&&!drag.asking)settle();});
 document.addEventListener("pointerup",async e=>{const d=drag;if(!d||e.pointerId!==d.id||d.asking)return;
  if(!d.live){settle();return;}
  swallow=true;setTimeout(()=>{swallow=false;},400);  // the click that ends a drag does not open the booking
  const t=d.to,lane=t.lane,name=d.item.dataset.name||"Cliente";
  if(lane===d.lane&&t.start===d.start&&t.minutes===d.minutes){settle();return;}
  d.asking=true;
  const other=lane!==d.lane,where=(other&&lane.dataset.label?" "+lane.dataset.label:"")+" alle "+hm(t.start)+(other&&lane.dataset.name?" con "+lane.dataset.name:"");
  const question=d.resize?["Cambiare la durata?",[veiledName(name),`: dalle ${hm(t.start)} alle ${hm(t.start+t.minutes)}, ${t.minutes} minuti invece di ${d.minutes}.`],"Cambia durata"]
   :["Spostare l'appuntamento?",["Spostare ",veiledName(name),where+"?"],"Sposta"];
  if(!await ask(...question)){settle();return;}
  const body={date:lane.dataset.day,time:hm(t.start),resource:lane.dataset.drop||"",minutes:t.minutes,force:"force" in d.agenda.dataset};
  for(;;){
   let r=null,out={};
   try{r=await fetch(d.item.dataset.move,{method:"POST",headers:{"Content-Type":"application/json","X-CSRFToken":csrf()},body:JSON.stringify(body),credentials:"same-origin"});out=await r.json();}
   catch{out={error:"Questa prenotazione non si sposta più da qui: aggiorna l'agenda e riprova."};}
   if(r?.ok){await refresh(true);settle();say(d.resize?`Durata cambiata: ${t.minutes} minuti`:"Spostata"+where);return;}
   // Forza: what the move goes beyond, said by the server, to confirm once more
   if(r?.status===409&&out.force){if(await ask("Forzare lo spostamento?",[out.error],"Forza",true)){body.force_ok=true;continue;}break;}
   await ask("Non si può spostare",[out.error||"Operazione non consentita."]);break;}
  settle();});

 // 2.14.0: the agenda and the Dashboard lists refresh by themselves, keeping the place on the page; never during a
 // drag, a question, a field being filled, or with the window in the background. The refresh does not count as
 // someone at the screen (X-TDBS-Refresh): the session still ends after the usual time
 const parts=()=>[...document.querySelectorAll("[data-refresh]")];
 let fetched=Date.now();
 const quiet=()=>drag||document.querySelector("dialog[open]")||parts().some(p=>p.querySelector("details[open]")||p.contains(document.activeElement)&&document.activeElement.matches("input,select,textarea"));
 async function refresh(now){
  if(!parts().length||!now&&(document.hidden||quiet()))return;
  fetched=Date.now();
  let r;try{r=await fetch(location.href,{headers:{"X-TDBS-Refresh":"1"},credentials:"same-origin"});}catch{return;}
  if(r.redirected&&new URL(r.url).pathname!==location.pathname){location.reload();return;}  // signed out meanwhile: the sign-in page
  if(!r.ok)return;
  const page=new DOMParser().parseFromString(await r.text(),"text/html");
  if(!now&&quiet())return;
  const card=$(".agenda-card"),left=card?.scrollLeft,x=scrollX,y=scrollY,focus=document.activeElement?.closest?.("[data-refresh]")&&document.activeElement.getAttribute("href");
  const seen=new Set([...document.querySelectorAll(".agenda-item")].map(a=>a.getAttribute("href")));
  parts().forEach(p=>{const fresh=page.querySelector('[data-refresh="'+p.dataset.refresh+'"]');if(fresh)p.replaceChildren(...fresh.childNodes);});
  lay();filter();veil();
  if(seen.size)document.querySelectorAll(".agenda-item").forEach(a=>{if(!seen.has(a.getAttribute("href")))a.classList.add("is-new");});
  if(focus)document.querySelector('[data-refresh] [href="'+CSS.escape(focus)+'"]')?.focus({preventScroll:true});
  const again=$(".agenda-card");if(again&&left)again.scrollLeft=left;scrollTo(x,y);}
 if(parts().length){const every=$("[data-agenda]")?45000:60000;setInterval(()=>refresh(),every);
  document.addEventListener("visibilitychange",()=>{if(!document.hidden&&Date.now()-fetched>every)refresh();});}

 // 2.14.0: Cerca nel giorno: the appointments that match stand out, the others fade; Enter opens the first
 const search=$("[data-quick-search]");
 function filter(){if(!search)return;const q=search.value.trim().toLocaleLowerCase("it");
  document.querySelectorAll(".agenda-item,.month-day li,.week-col li").forEach(el=>{const hit=el.textContent.toLocaleLowerCase("it").includes(q);el.classList.toggle("is-dim",!!q&&!hit);el.classList.toggle("is-hit",!!q&&hit);});}
 search?.addEventListener("input",filter);
 search?.addEventListener("keydown",e=>{if(e.key==="Escape"){search.value="";filter();search.blur();}
  else if(e.key==="Enter"){e.preventDefault();const hit=$(".is-hit");(hit?.closest("a")||hit?.querySelector("a"))?.click();}});

 // 2.14.0: the agenda from the keyboard: each key is the link or button marked with it (data-key); not while typing
 document.querySelectorAll('[data-key="?"]').forEach(b=>b.addEventListener("click",()=>$("[data-shortcuts]")?.showModal()));
 document.querySelectorAll("dialog [data-close]").forEach(b=>b.addEventListener("click",()=>b.closest("dialog").close()));
 if($("[data-shortcuts]"))document.addEventListener("keydown",e=>{
  if(e.key==="Escape"&&drag?.live&&!drag.asking){settle();return;}
  if(e.ctrlKey||e.metaKey||e.altKey||e.target.closest("input,textarea,select,[contenteditable]")||document.querySelector("dialog[open]"))return;
  if(e.key==="/"&&search){e.preventDefault();search.focus();search.select();return;}
  const key=document.querySelector('[data-key="'+CSS.escape(e.key.length===1?e.key.toLowerCase():e.key)+'"]');
  if(key){e.preventDefault();key.click();}});

 // 2.14.0: Nascondi nomi: names and prices blurred on a screen the clients can see, remembered on this device
 const privacy=$("[data-privacy-toggle]");
 let veiled=false;try{veiled=localStorage.getItem("tdbs-privacy")==="1";}catch{}
 function veil(){document.documentElement.toggleAttribute("data-privacy",veiled);
  // a name in a tooltip would show on hover
  document.querySelectorAll(".agenda-item[title],.agenda-item[data-title]").forEach(a=>{if(veiled&&a.title){a.dataset.title=a.title;a.removeAttribute("title");}else if(!veiled&&a.dataset.title){a.title=a.dataset.title;delete a.dataset.title;}});
  if(privacy){privacy.setAttribute("aria-pressed",String(veiled));privacy.querySelector("span").textContent=veiled?"Mostra nomi":"Nascondi nomi";}}
 privacy?.addEventListener("click",()=>{veiled=!veiled;try{localStorage.setItem("tdbs-privacy",veiled?"1":"0");}catch{}veil();});
 veil();
 document.querySelectorAll("[data-print]").forEach(b=>b.addEventListener("click",()=>print()));

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
   if(kind==="login"&&!email)throw Error($("#email")?.dataset.empty||"Inserisci prima la tua email.");
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
// 2.14.0: a consent signed on the tablet at the desk: the finger or the pen draws, the drawing goes as a PNG
document.querySelectorAll("canvas[data-sign]").forEach(c=>{const out=document.querySelector(c.dataset.sign),x=c.getContext("2d");let on=false;x.lineWidth=3;x.lineCap="round";x.lineJoin="round";x.strokeStyle="#111";
 const at=e=>{const r=c.getBoundingClientRect();return [(e.clientX-r.left)*c.width/r.width,(e.clientY-r.top)*c.height/r.height];};
 c.addEventListener("pointerdown",e=>{on=true;c.setPointerCapture(e.pointerId);x.beginPath();x.moveTo(...at(e));e.preventDefault();});
 c.addEventListener("pointermove",e=>{if(!on)return;x.lineTo(...at(e));x.stroke();});
 const end=()=>{if(on)out.value=c.toDataURL("image/png");on=false;};c.addEventListener("pointerup",end);c.addEventListener("pointercancel",end);
 c.closest("form").querySelector("[data-sign-clear]")?.addEventListener("click",()=>{x.clearRect(0,0,c.width,c.height);out.value="";});});
