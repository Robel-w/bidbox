(() => {
'use strict';
const oldRoute = location.hash.slice(1).split('/')[0];
if (['ask', 'review', 'approval', 'report', 'process', 'economics', 'architecture', 'history', 'runs'].includes(oldRoute)) {
  location.replace('/app' + (oldRoute === 'runs' ? '#history' : location.hash));
  return;
}
const $ = s => document.querySelector(s), RM = matchMedia('(prefers-reduced-motion:reduce)').matches;
const PAL = [[10,63,184],[10,85,216],[76,141,255],[169,200,255],[234,242,255]];
const B = [0,32,8,40,2,34,10,42,48,16,56,24,50,18,58,26,12,44,4,36,14,46,6,38,60,28,52,20,62,30,54,22,3,35,11,43,1,33,9,41,51,19,59,27,49,17,57,25,15,47,7,39,13,45,5,37,63,31,55,23,61,29,53,21];
const h = (x,y,s) => {let n=Math.sin(x*127.1+y*311.7+s*74.7)*43758.5453;return n-Math.floor(n)};
const vn = (x,y,s) => {const i=Math.floor(x),j=Math.floor(y),u=x-i,v=y-j,a=u*u*(3-2*u),b=v*v*(3-2*v);return(h(i,j,s)*(1-a)+h(i+1,j,s)*a)*(1-b)+(h(i,j+1,s)*(1-a)+h(i+1,j+1,s)*a)*b};
const fbm = (x,y,s) => {let t=0,a=.5;for(let o=0;o<4;o++){t+=a*vn(x,y,s+o);x*=2;y*=2;a/=2}return t};
function dither(c) {
  const seed=+c.dataset.seed, H=+c.dataset.h, g=c.getContext('2d');
  let W,hh,im,off=0,vis=false,last=0,frame=0;
  function draw() {
    const d=im.data;
    for(let y=0;y<hh;y++) for(let x=0;x<W;x++) {
      let v=fbm((x+off)*.045,y*.06,seed);v=(v-.2)*1.6;
      const t=(B[(y&7)*8+(x&7)]+.5)/64-.5;v=Math.min(.999,Math.max(0,v+t*.55));
      const p=PAL[Math.floor(v*5)],k=(y*W+x)*4;
      d[k]=p[0];d[k+1]=p[1];d[k+2]=p[2];d[k+3]=255;
    }
    g.putImageData(im,0,0);
  }
  function resize() {
    W=Math.max(40,Math.round((c.parentElement.clientWidth||600)/4));hh=Math.round(H/4);
    c.width=W;c.height=hh;im=g.createImageData(W,hh);draw();
  }
  resize();
  new ResizeObserver(resize).observe(c.parentElement);
  if(RM)return;
  const f=n=>{if(!vis)return;if(n-last>41){last=n;off+=.35;draw()}frame=requestAnimationFrame(f)};
  new IntersectionObserver(e=>{
    const v=e[0].isIntersecting;
    if(v&&!vis){vis=true;frame=requestAnimationFrame(f)}
    else if(!v){vis=false;cancelAnimationFrame(frame)}
  }).observe(c);
}
document.querySelectorAll('canvas').forEach(dither);
const io=new IntersectionObserver(es=>es.forEach(e=>{if(e.isIntersecting){e.target.classList.add('in');io.unobserve(e.target)}}),{threshold:.15});
const rv=el=>io.observe(el);document.querySelectorAll('.rv').forEach(rv);
const nav=$('#nav'),tiles=[...document.querySelectorAll('.tile canvas')];
function scroll() {
  nav.classList.toggle('on',scrollY>300);
  if(RM)return;
  tiles.forEach(t=>{const r=t.parentElement.getBoundingClientRect();t.style.transform=`translateY(${((r.top+r.height/2-innerHeight/2)/Math.max(innerHeight,1)*-14).toFixed(1)}px)`});
}
addEventListener('scroll',scroll,{passive:true});scroll();
function tabs(box,names,panel,on) {
  box.replaceChildren();
  names.forEach((n,i)=>{
    const b=document.createElement('button');b.className='tab';b.role='tab';b.type='button';b.textContent=n;
    b.id=box.id+'-'+i;b.setAttribute('aria-controls',panel.id);b.setAttribute('aria-selected',i===0);b.tabIndex=i===0?0:-1;
    b.onclick=()=>{[...box.children].forEach(x=>{x.setAttribute('aria-selected',x===b);x.tabIndex=x===b?0:-1});panel.setAttribute('aria-labelledby',b.id);on(i)};
    b.onkeydown=e=>{
      const next=e.key==='ArrowRight'?(i+1)%names.length:e.key==='ArrowLeft'?(i+names.length-1)%names.length:e.key==='Home'?0:e.key==='End'?names.length-1:null;
      if(next!==null){e.preventDefault();box.children[next].focus();box.children[next].click()}
    };
    box.appendChild(b);
  });
  panel.setAttribute('aria-labelledby',box.firstElementChild.id);
}
const SH=['Ask','Live run','Approval','Report'];
function shot(i) {
  $('#shot').textContent='screenshot';
  $('#shot').setAttribute('aria-label',SH[i]+' screenshot not supplied');
}
tabs($('#st'),SH,$('#shot'),shot);shot(0);
const ST=[['pick a connector type','Choose database, file drop or web API.'],['map the fields','Say which columns hold the award, buyer and amount.'],['set credentials by name','Reference a secret in the host store, never paste it.'],['run the test','Confirm the connection and see the data label.']];
$('#steps').innerHTML=ST.map((s,i)=>`<div class="step rv"><b>0${i+1}</b><div><h3>&ldquo;${s[0]}&rdquo;</h3><p>${s[1]}</p></div></div>`).join('');
document.querySelectorAll('.step').forEach(rv);
const TX=['name: county-registry\nkind: database\nmap: ./fields.yaml\ncredentials: secret:REGISTRY_TOKEN\nlabel: sandbox','# Illustrative example, not a live test\n$ bidbox connector test county-registry\nconnection ......... ok\nfields mapped ...... ok\ncredentials ........ found by name\ndata label ......... sandbox'];
let cur=0,typed=[0,0],tm;
function type(i) {
  clearInterval(tm);const p=$('#tp');
  if(typed[i]||RM){p.textContent=TX[i];typed[i]=1;return}
  let n=0;tm=setInterval(()=>{p.textContent=TX[i].slice(0,++n);if(n>=TX[i].length){clearInterval(tm);typed[i]=1}},18);
}
tabs($('#tt'),['manifest','test'],$('#tp'),i=>{cur=i;type(i)});
new IntersectionObserver((e,o)=>{if(e[0].isIntersecting){type(cur);o.disconnect()}},{threshold:.4}).observe($('.term'));
$('#cp').onclick=async()=>{try{await navigator.clipboard.writeText(TX[cur]);$('#cp').textContent='Copied'}catch(e){$('#cp').textContent='Copy failed'}setTimeout(()=>$('#cp').textContent='Copy',1500)};
const FQ=[['What happens if a tool times out?','Each tool call waits 30 seconds and retries once. If it still fails, the review file notes the limitation and the run continues.'],['What needs approval?','Anything that leaves BidBox, such as filing or sending a report. A named person must approve it first.'],['Where do credentials live?','In the host secret store. Config files only hold the secret name.'],['Where does the data come from?','Public procurement data, plus any connector you add. Each result is labelled live or sandbox.'],['Can I use a local model?','Yes. BidBox can run against a model on your own hardware.'],['Does it replace reviewers?','No. It prepares the file; a person decides.']];
$('#fq').innerHTML=FQ.map((q,i)=>`<div class="q"><button aria-expanded="false" aria-controls="a${i}" id="b${i}">${q[0]}<svg class="i" viewBox="0 0 24 24"><path d="M12 5v14M5 12h14"/></svg></button><div class="a" id="a${i}" role="region" aria-labelledby="b${i}"><div><p>${q[1]}</p></div></div></div>`).join('');
document.querySelectorAll('.q button').forEach(b=>b.onclick=()=>{const q=b.parentElement,o=q.classList.toggle('open');b.setAttribute('aria-expanded',o)});
async function J(url) {
  const response=await fetch(url,{signal:AbortSignal.timeout(15000)});
  if(!response.ok)throw new Error('Data unavailable');
  return response.json();
}
J('/api/data/status').then(d=>{
  const live=d.counts?.live,sandbox=d.counts?.sandbox;
  if(!Number.isFinite(live)||!Number.isFinite(sandbox))throw new Error('Counts not reported');
  const label=live&&sandbox?'Mixed data':live?'Live data':sandbox?'Sandbox data':'No data';
  $('#badge').textContent=label+' - '+(live+sandbox).toLocaleString()+' records';
}).catch(()=>{$('#badge').textContent='Data status unavailable'});
const KI={database:'<ellipse cx="12" cy="6" rx="7" ry="3"/><path d="M5 6v12c0 1.7 3 3 7 3s7-1.3 7-3V6"/>',message:'<path d="M4 5h16v11H9l-5 4z"/>'};
J('/api/integrations').then(d=>{
  const el=$('#chips');el.replaceChildren();
  const labels={live:'Live',sandbox:'Sandbox',planned:'Planned',error:'Error',not_configured:'Not configured'};
  for(const item of d.integrations) {
    const chip=document.createElement('div');chip.className='chip'+(item.status==='live'?' live':'');
    chip.innerHTML='<svg class="i" viewBox="0 0 24 24" aria-hidden="true">'+(item.kind==='messaging'?KI.message:KI.database)+'</svg>';
    chip.append(document.createTextNode(String(item.name)));
    const status=document.createElement('span');status.textContent=labels[item.status]||'Not verified';chip.append(status);el.append(chip);
  }
  if(!d.integrations.length)el.textContent='No integrations configured';
}).catch(()=>{$('#chips').textContent='Integration status unavailable'});
J('/api/economics').then(d=>{
  const f=(v,fn)=>v==null||v===''?'-':fn?fn(v):String(v),n=v=>Number(v).toLocaleString(undefined,{maximumFractionDigits:4});
  const rows=[['Model',f(d.model)],['Tokens per answer',f(d.tokens_per_answer,n)],['Cost per completed review (USD)',f(d.cost_per_outcome_usd,v=>'$'+n(v))],['Cost per completed review (KES)',f(d.cost_per_outcome_kes,v=>'KES '+n(v))]];
  $('#eco').replaceChildren();
  for(const [label,value] of rows){const dt=document.createElement('dt'),dd=document.createElement('dd');dt.textContent=label;dd.textContent=value;$('#eco').append(dt,dd)}
}).catch(()=>{});
$('#f').onsubmit=e=>{
  e.preventDefault();
  $('#msg').textContent='Pilot signup is not configured yet. Your details have not been sent.';
};
})();
