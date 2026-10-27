"use strict";
document.querySelectorAll(".tdbs-frame").forEach(frame=>{
 window.addEventListener("message",event=>{
  if(event.source!==frame.contentWindow||event.origin!==frame.dataset.origin)return;
  const height=event.data?.tdbsHeight;
  if(typeof height==="number"&&Number.isFinite(height))frame.height=String(Math.max(500,Math.min(5000,Math.ceil(height))));
 });
});