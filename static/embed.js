"use strict";
// 2.14.0: the booking page inside a site without WordPress, as tall as its content (booking.js tells its height).
// For the business's site: <iframe src="https://portal/book/slug/" data-tdbs></iframe><script src="https://portal/static/embed.js" async></script>
(()=>{
 const origin=new URL(document.currentScript.src).origin;
 addEventListener("message",event=>{
  const height=event.data?.tdbsHeight;
  if(event.origin!==origin||typeof height!=="number"||!Number.isFinite(height))return;
  for(const frame of document.querySelectorAll("iframe[data-tdbs]"))if(frame.contentWindow===event.source)frame.height=String(Math.max(500,Math.min(5000,Math.ceil(height))));
 });
})();
