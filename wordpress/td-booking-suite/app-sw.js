"use strict";
// 2.22.1: the agenda as an app on the phone: the page and its two files from the network, the last copy when offline
const CACHE="tdbs-app-1";
self.addEventListener("install",()=>self.skipWaiting());
self.addEventListener("activate",e=>e.waitUntil(self.clients.claim()));
self.addEventListener("fetch",e=>{
 if(e.request.method!=="GET")return;
 const u=new URL(e.request.url);
 if(!u.searchParams.has("tdbs_app")&&!/\/td-booking-suite\/(widget\.css|agenda\.js)$/.test(u.pathname))return;
 e.respondWith(fetch(e.request).then(r=>{if(r.ok){const c=r.clone();caches.open(CACHE).then(x=>x.put(e.request,c));}return r;})
  .catch(()=>caches.match(e.request).then(r=>r||new Response("<!doctype html><meta charset=utf-8><meta name=viewport content=\"width=device-width,initial-scale=1\"><p style=\"font-family:system-ui;padding:24px\">Sei offline: l’agenda si apre appena torna la connessione.</p>",{headers:{"Content-Type":"text/html; charset=utf-8"}}))));
});
