(() => {
  const form = document.getElementById('assignmentForm');
  if (!form) return;
  const assignmentId = form.dataset.assignment;
  const submitted = form.dataset.submitted === 'True' || form.dataset.submitted === 'true';
  const state = document.getElementById('saveState');
  const timer = document.getElementById('activeTimer');
  let seconds = Number(timer?.dataset.seconds || 0);
  let dirty = false;

  function collectAnswers() {
    const data = {};
    const fd = new FormData(form);
    for (const [k, v] of fd.entries()) if (k.startsWith('q__')) data[k.slice(3)] = v;
    return data;
  }

  async function autosave() {
    if (submitted || !dirty) return;
    dirty = false;
    if (state) state.textContent = '● Đang lưu...';
    try {
      const r = await fetch('/api/autosave', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({assignment_id:assignmentId, answers:collectAnswers()})});
      if (!r.ok) throw new Error('save');
      if (state) state.textContent = '✓ Đã lưu tự động';
    } catch(e) {
      dirty = true;
      if (state) state.textContent = '⚠ Chưa lưu được — sẽ thử lại';
    }
  }

  async function heartbeat() {
    if (submitted || document.hidden) return;
    try {
      const r = await fetch('/api/heartbeat', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({assignment_id:assignmentId})});
      if (r.ok) {
        const j = await r.json(); seconds = Number(j.active_seconds || seconds); renderTimer();
      }
    } catch(e) {}
  }
  function renderTimer(){
    if(!timer) return;
    const h=Math.floor(seconds/3600), m=Math.floor((seconds%3600)/60), s=seconds%60;
    timer.textContent=h?`${h} giờ ${m} phút`:(m?`${m} phút ${s} giây`:`${s} giây`);
  }

  form.addEventListener('input', () => { dirty = true; if(state) state.textContent='● Có thay đổi chưa lưu'; });
  form.addEventListener('change', () => { dirty = true; });
  window.addEventListener('beforeunload', () => { if (dirty) navigator.sendBeacon?.('/api/autosave'); });
  setInterval(autosave, 12000);
  setInterval(heartbeat, 30000);
  setInterval(()=>{ if(!submitted && !document.hidden){ seconds += 1; renderTimer(); } },1000);
  heartbeat();
})();
