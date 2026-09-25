// Creator Brain UI
const API = '';
let currentJob = null;
let es = null;

async function apiGet(path) {
  const r = await fetch(API + path);
  if (!r.ok) throw new Error(`HTTP ${r.status}`);
  return r.json();
}
async function apiPost(path, body) {
  const r = await fetch(API + path, {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify(body || {})
  });
  if (!r.ok) throw new Error(`HTTP ${r.status}: ${(await r.text()).slice(0,200)}`);
  return r.json();
}

async function refreshState() {
  document.getElementById('state-loading').textContent = 'caricamento…';
  try {
    const s = await apiGet('/api/brain/state');
    document.getElementById('state-loading').style.display = 'none';
    document.getElementById('state-body').style.display = 'block';

    document.getElementById('active-version').textContent = s.active_version || '(nessuna)';
    const schema = (s.active_info && s.active_info.schema_version) || '—';
    const schemaEl = document.getElementById('active-schema');
    schemaEl.textContent = 'formato ' + String(schema).replace('0.', 'v');
    if (s.active_info && s.active_info.corpus) {
      const c = s.active_info.corpus;
      document.getElementById('state-loading').textContent = '';
    }

    // Catalogo
    try {
      const cat = await apiGet('/api/brain/catalog_state');
      const catEl = document.getElementById('catalog-list');
      if (catEl) {
        catEl.innerHTML = '';
        (cat.catalogs || []).forEach(c => {
          const isPrimary = (c.creator === 'jay.emme');
          // Nascondi creator non-primary senza video (cancellati)
          if (!isPrimary && (c.n_video_files || 0) === 0) return;
          const div = document.createElement('div');
          div.className = 'creator';
          const upd = c.updated_at ? c.updated_at.slice(0, 16).replace('T', ' ') : 'mai';
          const nv = c.n_video_files !== undefined ? c.n_video_files : c.n_total;
          const delBtn = isPrimary ? '' : `<button class="warn" onclick="deleteVideos('${c.creator}')" style="font-size:11px; padding:4px 10px; margin-left:6px">🗑️ Cancella video</button>`;
          div.innerHTML = `<span><b>@${c.creator}</b> <span style="color:#8a92a6; font-size:12px">${nv} video in disco · ${c.n_total} in catalogo · scan ${upd}</span></span>
            <span><button class="alt" onclick="fullScan('${c.creator}')" style="font-size:11px; padding:4px 10px">🔄 Full rescan</button>${delBtn}</span>`;
          catEl.appendChild(div);
        });
      }
    } catch (_) {}

    // Creators
    const cl = document.getElementById('creators-list');
    cl.innerHTML = '';
    (s.creators || []).forEach(c => {
      // Nascondi creator senza video.mp4 (tranne jay.emme)
      const hasVideos = (c.n_video_files || 0) > 0;
      if (!c.is_primary && !hasVideos) return;
      const div = document.createElement('div');
      div.className = 'creator';
      const badge = c.is_primary ? '<span class="badge primary">PRIMARY</span>' : '';
      div.innerHTML = `<span>@${c.creator} ${badge}</span>
        <span>${c.n_analyzed}/${c.n_videos} analizzati</span>`;
      cl.appendChild(div);
    });

    // Versions
    const vl = document.getElementById('versions-list');
    vl.innerHTML = '';
    (s.versions || []).forEach(v => {
      const div = document.createElement('div');
      div.className = 'vrow';
      const act = v.is_active ? '<span class="badge active">ATTIVA</span>' : '';
      const size = (v.size_bytes / 1024).toFixed(1) + ' KB';
      div.innerHTML = `<span><b>${v.version}</b> ${act} <span style="color:#8a92a6; font-size:12px">${size}</span></span>
        <span>${v.is_active ? '' : `<button class="alt" onclick="rollback('${v.version}')" style="font-size:12px; padding:5px 10px">↩ Attiva</button>`}</span>`;
      vl.appendChild(div);
    });

    // Archiviate cliccabili
    const al = document.getElementById('archive-list');
    if (al) {
      al.innerHTML = '';
      const arch = s.archived_versions || [];
      if (arch.length) {
        const title = document.createElement('div');
        title.innerHTML = `<div style="margin-top:10px; color:#8a92a6; font-size:12px">📦 Archiviate (${arch.length}):</div>`;
        al.appendChild(title);
        arch.forEach(v => {
          const div = document.createElement('div');
          div.className = 'vrow';
          const size = (v.size_bytes / 1024).toFixed(1) + ' KB';
          div.innerHTML = `<span><b>${v.version}</b> <span style="color:#8a92a6; font-size:12px">${size}</span></span>
            <span><button class="warn" onclick="rollback('${v.version}')" style="font-size:12px; padding:5px 10px">↩ Attiva</button></span>`;
          al.appendChild(div);
        });
      }
    }
    document.getElementById('archive-info').textContent =
      s.n_archive ? `📦 ${s.n_archive} versioni in archivio` : '';
  } catch (e) {
    document.getElementById('state-loading').textContent = '⚠ ' + e.message;
  }
}

function postToParent(msg) {
  try {
    if (window.parent && window.parent !== window) {
      window.parent.postMessage(msg, '*');
    }
  } catch (_) {}
}

function startSSE(jobId) {
  if (es) es.close();
  // La card "Log job corrente" e' stata rimossa: i log vanno al parent via postMessage
  // Notifica il parent: apri log-panel e workbar
  postToParent({type: 'brain:jobStart', jobId});
  postToParent({type: 'brain:progress', label: `brain: job #${jobId}`, progress: 0, status: 'running'});

  es = new EventSource(`/api/jobs/${jobId}/stream`);
  let jobCompleted = false;  // flag per distinguere chiusura pulita da errore
  es.onmessage = (e) => {
    try {
      const d = JSON.parse(e.data);
      if (d.type === 'log' && d.message) {
        postToParent({type: 'brain:log', message: d.message, level: d.level || 'info'});
      }
      if (d.type === 'progress') {
        const lbl = d.step_label || d.label || ('brain: job #' + jobId);
        postToParent({type: 'brain:progress', label: 'brain: ' + lbl, progress: d.progress || 0, status: 'running'});
      }
      if (d.type === 'state') {
        postToParent({type: 'brain:progress', label: 'brain: ' + (d.label || d.status || 'in corso'), progress: d.progress || 0, status: 'running'});
        if (Array.isArray(d.logs)) {
          d.logs.forEach(l => postToParent({type: 'brain:log', message: l.message, level: l.level || 'info'}));
        }
      }
      if (d.type === 'status' && (d.status === 'done' || d.status === 'error')) {
        jobCompleted = true;
        postToParent({type: 'brain:progress', label: 'brain: ' + (d.status === 'done' ? 'completato' : 'errore'), progress: d.progress || (d.status === 'done' ? 100 : 0), status: d.status, error: d.error});
        es.close(); es = null;
        setTimeout(refreshState, 1000);
      }
    } catch (_) {}
  };
  es.onerror = () => {
    es.close(); es = null;
    if (jobCompleted) return;
    // Fallback: polling ogni 8s fino a quando il job finisce
    postToParent({type: 'brain:progress', label: 'brain: connessione persa, polling fallback…', progress: 0, status: 'running'});
    let poll = 0;
    const pollTimer = setInterval(async () => {
      poll++;
      if (poll > 500) { clearInterval(pollTimer); return; }  // safety 66 min
      try {
        const r = await fetch('/api/jobs/' + jobId);
        if (!r.ok) return;
        const j = await r.json();
        const st = j.status || 'running';
        if (st === 'done' || st === 'error') {
          clearInterval(pollTimer);
          jobCompleted = true;
          postToParent({type: 'brain:progress',
            label: 'brain: ' + (st === 'done' ? 'completato' : 'errore'),
            progress: j.progress || (st === 'done' ? 100 : 0),
            status: st, error: j.error});
          setTimeout(refreshState, 1000);
        } else {
          postToParent({type: 'brain:progress',
            label: 'brain (poll): ' + (j.step_label || j.status || 'in corso'),
            progress: j.progress || 0, status: 'running'});
        }
      } catch (_) {}
    }, 8000);
  };
}

async function addPrimary() {
  const limitEl = document.getElementById('primary-limit');
  const limit = parseInt(limitEl.disabled ? (limitEl.dataset.savedValue || '20') : limitEl.value) || 20;
  const dateFrom = document.getElementById('primary-date-from').value || null;
  const dateTo = document.getElementById('primary-date-to').value || null;
  const btn = document.getElementById('btn-add-primary');
  btn.disabled = true;
  const rangeLabel = (dateFrom || dateTo) ? ` [${dateFrom || '…'} → ${dateTo || '…'}]` : '';
  document.getElementById('primary-status').textContent = `Avvio download @jay.emme (limit=${limit})${rangeLabel}…`;
  try {
    const r = await apiPost('/api/brain/add_and_compare', {
      handle: 'jay.emme', limit, date_from: dateFrom, date_to: dateTo
    });
    document.getElementById('primary-status').textContent = `Job #${r.job_id} avviato (scarica nuovi video di @jay.emme)`;
    startSSE(r.job_id);
  } catch (e) {
    document.getElementById('primary-status').textContent = '⚠ ' + e.message;
  } finally {
    btn.disabled = false;
  }
}

async function addAndCompare() {
  const handle = document.getElementById('handle').value.trim();
  const limitEl = document.getElementById('limit');
  const limit = parseInt(limitEl.disabled ? (limitEl.dataset.savedValue || '15') : limitEl.value) || 15;
  const dateFrom = document.getElementById('compare-date-from').value || null;
  const dateTo = document.getElementById('compare-date-to').value || null;
  const autoDelEl = document.getElementById('compare-auto-delete');
  const autoDel = autoDelEl ? autoDelEl.checked : true;
  if (!handle) { alert('Inserisci un username TikTok'); return; }
  const btn = document.getElementById('btn-add-compare');
  btn.disabled = true;
  const rangeLabel = (dateFrom || dateTo) ? ` [${dateFrom || '…'} → ${dateTo || '…'}]` : '';
  document.getElementById('add-status').textContent = `Avvio per @${handle}${rangeLabel}…`;
  try {
    const r = await apiPost('/api/brain/add_and_compare', {
      handle, limit, date_from: dateFrom, date_to: dateTo, auto_delete: autoDel
    });
    document.getElementById('add-status').textContent = `Job #${r.job_id} avviato`;
    startSSE(r.job_id);
  } catch (e) {
    document.getElementById('add-status').textContent = '⚠ ' + e.message;
  } finally {
    btn.disabled = false;
  }
}

async function buildBrain() {
  const btn = document.getElementById('btn-build');
  btn.disabled = true;
  document.getElementById('build-status').textContent = 'Rigenerazione…';
  try {
    const r = await apiPost('/api/brain/build', {});
    document.getElementById('build-status').textContent = `Job #${r.job_id} avviato`;
    startSSE(r.job_id);
  } catch (e) {
    document.getElementById('build-status').textContent = '⚠ ' + e.message;
  } finally {
    btn.disabled = false;
  }
}

async function fullScan(creator) {
  if (!confirm(`Forzare full scan di @${creator}? ~2 min.`)) return;
  try {
    const r = await apiPost('/api/brain/full_scan', {handle: creator});
    alert(`Full scan @${creator} avviato (job #${r.job_id})`);
    startSSE(r.job_id);
  } catch (e) {
    alert('Errore: ' + e.message);
  }
}

async function deleteVideos(creator) {
  if (!confirm(`Cancellare i video di @${creator}?\n\nVerranno rimossi i file video.mp4 ma NON le analisi (analysis.json).\nIl brain continuera' a funzionare.`)) return;
  try {
    const r = await apiPost('/api/brain/delete_videos', {handle: creator});
    alert(`Cancellazione @${creator} avviata (job #${r.job_id})`);
    startSSE(r.job_id);
  } catch (e) {
    alert('Errore: ' + e.message);
  }
}

async function rollback(version) {
  if (!confirm(`Attivare ${version}? La versione attuale verra' sostituita.`)) return;
  try {
    const r = await apiPost('/api/brain/rollback', {version});
    alert(`Rollback a ${version} in corso (job #${r.job_id})`);
    startSSE(r.job_id);
  } catch (e) {
    alert('Errore: ' + e.message);
  }
}


let _brainNotes = [];  // cache locale delle note

function renderNotes() {
  const el = document.getElementById('notes-list');
  if (!el) return;
  el.innerHTML = '';
  if (_brainNotes.length === 0) {
    el.innerHTML = '<div style="color:#8a92a6; font-size:12px; font-style:italic">Nessuna nota. Aggiungine una sotto.</div>';
    return;
  }
  _brainNotes.forEach((n, i) => {
    const div = document.createElement('div');
    div.style.cssText = 'display:flex; justify-content:space-between; align-items:center; padding:8px 10px; margin-bottom:6px; background:#0f1117; border:1px solid #232838; border-radius:6px; font-size:13px';
    div.innerHTML = `<span style="flex:1">📌 ${n.replace(/</g, '&lt;')}</span>
      <button class="warn" onclick="removeNote(${i})" style="font-size:11px; padding:3px 8px; margin-left:8px">✕</button>`;
    el.appendChild(div);
  });
}

function removeNote(idx) {
  _brainNotes.splice(idx, 1);
  renderNotes();
}

function addNote() {
  const inp = document.getElementById('new-note');
  const v = (inp.value || '').trim();
  if (!v) return;
  _brainNotes.push(v);
  inp.value = '';
  renderNotes();
}

async function loadSettings() {
  try {
    const s = await apiGet('/api/brain/settings');
    let notes = s.user_notes || [];
    if (typeof notes === 'string') notes = notes.split('\n').map(x => x.trim()).filter(Boolean);
    _brainNotes = notes;
    renderNotes();
  } catch (_) {}
}

async function saveSettings() {
  const btn = document.getElementById('btn-save-settings');
  const msg = document.getElementById('settings-status');
  btn.disabled = true;
  msg.textContent = 'Salvataggio…';
  try {
    const body = { user_notes: _brainNotes };
    await apiPost('/api/brain/settings', body);
    msg.textContent = '✓ Salvato (' + _brainNotes.length + ' note)';
    setTimeout(() => msg.textContent = '', 2000);
  } catch (e) {
    msg.textContent = '⚠ ' + e.message;
  } finally {
    btn.disabled = false;
  }
}

async function panicStop() {
  if (!confirm('🛑 Fermare TUTTI i processi in corso?\n\nVerranno uccisi:\n- brain_analyze.py\n- brain_build.py\n- yt-dlp\n\nE marcati come errore tutti i job running.')) return;
  const btn = document.getElementById('btn-panic');
  const msg = document.getElementById('panic-status');
  btn.disabled = true;
  msg.textContent = '🛑 Stop in corso…';
  try {
    const r = await apiPost('/api/brain/panic', {});
    msg.textContent = `✓ Job panic #${r.job_id} avviato`;
    // Notifica il parent (ferma workbar e log)
    postToParent({type: 'brain:progress', label: 'brain: PANIC STOP avviato', progress: 0, status: 'running'});
    startSSE(r.job_id);
    // Ricarica lo stato dopo 5 sec
    setTimeout(() => { refreshState(); msg.textContent = '✓ Completato'; }, 5000);
  } catch (e) {
    msg.textContent = '⚠ ' + e.message;
  } finally {
    btn.disabled = false;
  }
}

function bindLimitDisable(limitId, dateFromId, dateToId) {
  const limitEl = document.getElementById(limitId);
  const fromEl = document.getElementById(dateFromId);
  const toEl = document.getElementById(dateToId);
  if (!limitEl || !fromEl || !toEl) return;

  // Salva il valore iniziale per ripristinarlo
  if (limitEl.dataset.savedValue === undefined) {
    limitEl.dataset.savedValue = limitEl.value;
  }

  function upd() {
    const hasDate = !!(fromEl.value || toEl.value);
    if (hasDate) {
      // Salva valore corrente prima di disabilitare
      if (!limitEl.disabled) {
        limitEl.dataset.savedValue = limitEl.value;
      }
      limitEl.disabled = true;
      limitEl.classList.add('disabled-grey');
      limitEl.value = '';
      limitEl.placeholder = '—';
      limitEl.title = 'Disabilitato: filtro data attivo (scarica tutti i video nel range)';
    } else {
      limitEl.disabled = false;
      limitEl.classList.remove('disabled-grey');
      limitEl.placeholder = '';
      // Ripristina valore salvato
      const saved = limitEl.dataset.savedValue || '20';
      limitEl.value = saved;
      limitEl.title = 'max video';
    }
  }
  fromEl.addEventListener('change', upd);
  toEl.addEventListener('change', upd);
  fromEl.addEventListener('input', upd);
  toEl.addEventListener('input', upd);
  upd();
}

document.addEventListener('DOMContentLoaded', () => {
  refreshState();
  document.getElementById('btn-add-compare').addEventListener('click', addAndCompare);
  document.getElementById('btn-add-primary').addEventListener('click', addPrimary);
  document.getElementById('btn-build').addEventListener('click', buildBrain);
  document.getElementById('btn-refresh').addEventListener('click', refreshState);
  bindLimitDisable('primary-limit', 'primary-date-from', 'primary-date-to');
  bindLimitDisable('limit', 'compare-date-from', 'compare-date-to');
  loadSettings();
  const bs = document.getElementById('btn-save-settings');
  if (bs) bs.addEventListener('click', saveSettings);
  const bn = document.getElementById('btn-add-note');
  if (bn) bn.addEventListener('click', addNote);
  const bp = document.getElementById('btn-panic');
  if (bp) bp.addEventListener('click', panicStop);
  const nn = document.getElementById('new-note');
  if (nn) nn.addEventListener('keypress', (e) => {
    if (e.key === 'Enter') { e.preventDefault(); addNote(); }
  });
});
