'use strict';
(() => {
  const $ = id => document.getElementById(id);
  const nodes = {provider:$('m-provider'),docs:$('m-docs'),review:$('m-review'),status:$('m-status'),message:$('m-message'),incidents:$('m-incidents'),document:$('m-document'),report:$('m-report'),count:$('m-count')};
  let state = null, timer = null;
  const node = (tag,text,className) => { const e=document.createElement(tag); if(text!=null)e.textContent=String(text); if(className)e.className=className; return e; };
  const safeUrl = (u) => { try { const x=new URL(u); return x.protocol==='https:'?x.href:null; } catch(_) { return null; } };
  const types={auth:'Giriş',quota:'Kota / hız',eligibility:'Hesap koşulu',transient:'Geçici',schema:'Yanıt şeması',endpoint:'API adresi',unknown:'İstek uyumsuzluğu'};
  function render() {
    const selected=nodes.provider.value;
    nodes.provider.replaceChildren();
    (state.available_docs||[]).forEach(item=>{ const option=node('option',item.provider_id);option.value=item.provider_id;nodes.provider.append(option); });
    if([...nodes.provider.options].some(o=>o.value===selected))nodes.provider.value=selected;
    const pid=nodes.provider.value;
    const incidents=state.incidents||[];
    nodes.count.textContent=incidents.length+' kayıt';
    nodes.incidents.replaceChildren();
    if(!incidents.length){const tr=node('tr');const td=node('td','Şu anda kayıtlı sorun yok.');td.colSpan=4;tr.append(td);nodes.incidents.append(tr);}
    incidents.forEach(item=>{
      const tr=node('tr'),td=node('td',item.provider_id+(item.http_status?' · HTTP '+item.http_status:''));td.append(node('small',item.message));
      tr.append(td,node('td',types[item.kind]||item.kind||'Bilinmiyor'),node('td',item.count),node('td',item.status==='resolved'?'Düzeldi':item.agent_eligible?'Ajan incelemesine uygun':'Ajan gerektirmez'));
      nodes.incidents.append(tr);
    });
    nodes.docs.disabled=!!state.busy||!pid;
    nodes.review.disabled=!!state.busy||!incidents.some(i=>i.provider_id===pid&&i.agent_eligible&&i.status!=='resolved');
    nodes.status.textContent=state.busy?'İnceleniyor…':'Salt-okunur · otomatik uygulama kapalı';
    nodes.document.replaceChildren();
    const doc=(state.documents||{})[pid];
    if(doc){
      const safe = safeUrl(doc.url);
      if(safe){ const link=node('a',safe);link.href=safe;link.target='_blank';link.rel='noopener noreferrer';nodes.document.append(link); }
      else nodes.document.append(node('p',String(doc.url||'Geçersiz adres')));
      nodes.document.append(node('p',({baseline:'İlk belge kaydı alındı.',changed:'Belge metni değişmiş; API değişikliği ayrıca doğrulanmalı.',unchanged:'Önceki okumayla aynı belge.'})[doc.change]||doc.change));
      nodes.document.append(node('p','Son kontrol: '+new Date(doc.checked_at).toLocaleString('tr-TR')));
      nodes.document.append(node('small','SHA-256: '+doc.sha256));nodes.document.append(node('pre',doc.excerpt));
    }else nodes.document.append(node('p','Bu sağlayıcı için henüz belge denetlenmedi.'));
    const review=(state.reviews||{})[pid];
    nodes.report.textContent=review?review.report:'Henüz öneri yok. Uygun kalıcı hata varsa abonelik ajanı yalnızca inceleme raporu hazırlar.';
    if(state.job)nodes.message.textContent=state.job.message||'';
  }
  async function load() {
    try {
      const response=await fetch('/api/maintenance',{cache:'no-store'});if(!response.ok)throw new Error('Yerel bakım servisi okunamadı.');
      state=await response.json();render();
    }catch(error){nodes.status.textContent='Çevrimdışı';nodes.message.textContent=error.message;}
    clearTimeout(timer);timer=setTimeout(load,state&&state.busy?2000:15000);
  }
  async function request(action) {
    nodes.docs.disabled=true;nodes.review.disabled=true;nodes.message.textContent='İstek hazırlanıyor…';
    try{
      const response=await fetch('/api/maintenance/'+action,{method:'POST',headers:{'X-ProviderDeck-Request':'1','Content-Type':'application/json'},body:JSON.stringify({provider_id:nodes.provider.value})});
      const result=await response.json();if(!response.ok)throw new Error(result.error||'İşlem başlatılamadı.');
      await load();
    }catch(error){nodes.message.textContent=error.message;if(state)render();}
  }
  nodes.provider.addEventListener('change',render);
  nodes.docs.addEventListener('click',()=>request('docs'));
  nodes.review.addEventListener('click',()=>request('review'));
  $('m-theme').addEventListener('click',()=>{document.documentElement.dataset.theme=document.documentElement.dataset.theme==='light'?'dark':'light';});
  load();
})();
