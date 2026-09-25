// === Health ===
async function refreshHealth() {


// === STUB: elementi sidebar rimossi (evita crash JS) ===
(function addSidebarStubs() {
  // ID rimossi dal DOM: creo stub invisibili subito, PRIMA che altri script
  // facciano getElementById().addEventListener
  const removedIds = ['tk-user', 'tk-max', 'btn-fetch', 'btn-confirm',
                       'btn-clear-refs', 'pf-name', 'btn-learn',
                       'refs-info', 'sel-info', 'profiles-list'];
  removedIds.forEach(id => {
    if (!document.getElementById(id)) {
      const stub = document.createElement('div');
      stub.id = id;
      stub.style.display = 'none';
      stub.dataset.stub = '1';
      document.body.appendChild(stub);
    }
  });
  console.log('✅ sidebar stubs added:', removedIds.length);
})();

  try {
    const r = await fetch('/api/health');
    const d = await r.json();
    const ok = d.anthropic_key && d.db;
    document.getElementById('top-status').textContent = ok ? 'pronto' : 'configurazione incompleta';
  } catch {}
}
refreshHealth();
setInterval(refreshHealth, 10000);

// === Utilities ===
function escapeHtml(s) {
  return (s || '').replace(/[&<>"']/g, c => ({
    '&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'
  }[c]));
}

// === TikTok picker ===
const state = { videos: [], selected: new Set() };

function renderGrid() {
  const grid = document.getElementById('grid');
  if (!state.videos.length) {
    grid.innerHTML = '<div class="empty">Nessun video. Carica una lista dalla sidebar.</div>';
    return;
  }
  grid.innerHTML = state.videos.map(v => `
    <div class="card ${state.selected.has(v.id) ? 'selected' : ''}" data-id="${v.id}">
      <input type="checkbox" class="check" ${state.selected.has(v.id) ? 'checked' : ''}>
      <img class="thumb" src="${v.thumbnail || ''}" alt="" loading="lazy"
           onerror="this.style.display='none'">
      <div class="meta">
        <div class="title">${escapeHtml(v.title)}</div>
        <div class="date">${v.date}</div>
      </div>
    </div>
  `).join('');

  grid.querySelectorAll('.card').forEach(card => {
    card.addEventListener('click', e => {
      if (e.target.tagName === 'INPUT') return;
      toggleSelect(card.dataset.id);
    });
  });
  grid.querySelectorAll('input.check').forEach(cb => {
    cb.addEventListener('change', () => toggleSelect(cb.parentElement.dataset.id));
  });
  updateSelInfo();
}

function toggleSelect(id) {
  if (state.selected.has(id)) state.selected.delete(id);
  else state.selected.add(id);
  renderGrid();
}

function updateSelInfo() {
  document.getElementById('sel-info').textContent =
    `${state.selected.size} video selezionati`;
  document.getElementById('btn-confirm').disabled = state.selected.size === 0;
}

// === Workbar / omino ===
function setWorker(st) {
  document.getElementById('worker').dataset.state = st;
}
function appendLog(line, level) {
  const el = document.getElementById('log-body');
  const cls = level === 'error' ? 'lvl-error' : '';
  el.innerHTML += `<span class="${cls}">${escapeHtml(line)}</span>\n`;
  el.scrollTop = el.scrollHeight;
}
function clearLog() { document.getElementById('log-body').textContent = ''; }

let currentSource = null;
function startJobSSE(jobId) {
  if (currentSource) currentSource.close();
  const es = new EventSource(`/api/jobs/${jobId}/stream`);
  currentSource = es;
  es.onmessage = (ev) => {
    let d; try { d = JSON.parse(ev.data); } catch { return; }
    if (d.type === 'state') {
      setWorker('walking');
      document.getElementById('workbar-label').textContent = d.label || d.status;
      document.getElementById('workbar-pct').textContent = d.progress + '%';
      document.getElementById('progress-fill').style.width = d.progress + '%';
      clearLog();
      (d.logs || []).forEach(l => appendLog(l.message, l.level));
    } else if (d.type === 'progress') {
      setWorker('walking');
      if (d.label) document.getElementById('workbar-label').textContent = d.label;
      document.getElementById('workbar-pct').textContent = d.progress + '%';
      document.getElementById('progress-fill').style.width = d.progress + '%';
    } else if (d.type === 'log') {
      appendLog(d.message, d.level);
    } else if (d.type === 'done') {
      setWorker('done');
      document.getElementById('workbar-label').textContent = 'completato';
      document.getElementById('workbar-pct').textContent = '100%';
      document.getElementById('progress-fill').style.width = '100%';
      es.close(); currentSource = null;
      const btnLearn = document.getElementById('btn-learn');
      if (btnLearn) btnLearn.disabled = false;
      document.getElementById('fetch-msg').textContent = '✓ job completato';
      loadProfiles();
    } else if (d.type === 'error') {
      setWorker('idle');
      document.getElementById('workbar-label').textContent = 'errore — vedi log';
      es.close(); currentSource = null;
      const btnLearn = document.getElementById('btn-learn');
      if (btnLearn) btnLearn.disabled = false;
    }
  };
  es.onerror = () => { es.close(); currentSource = null; setWorker('idle'); };
}

// === Bottone "Carica lista" ===
document.getElementById('btn-fetch').addEventListener('click', async () => {
  const username = document.getElementById('tk-user').value.trim();
  const max = parseInt(document.getElementById('tk-max').value || '20', 10);
  const msg = document.getElementById('fetch-msg');
  if (!username) { msg.textContent = '⚠ inserisci un username'; return; }
  msg.textContent = '⏳ caricamento lista…';
  document.getElementById('btn-fetch').disabled = true;
  try {
    const r = await fetch('/api/tiktok/list', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ username, max_videos: max })
    });
    const d = await r.json();
    if (!r.ok) throw new Error(d.error || 'errore');
    state.videos = d.videos || [];
    state.selected.clear();
    renderGrid();
    msg.textContent = `✓ ${d.count} video trovati`;
  } catch (e) {
    msg.textContent = '✗ ' + e.message;
  } finally {
    document.getElementById('btn-fetch').disabled = false;
  }
});

document.getElementById('select-all').addEventListener('change', e => {
  if (e.target.checked) state.videos.forEach(v => state.selected.add(v.id));
  else state.selected.clear();
  renderGrid();
});

// === Bottone "Conferma riferimenti" ===
document.getElementById('btn-confirm').addEventListener('click', async () => {
  const videos = state.videos
    .filter(v => state.selected.has(v.id))
    .map(v => ({ id: v.id, url: v.url, title: v.title, date: v.date }));
  if (!videos.length) return;
  document.getElementById('btn-confirm').disabled = true;
  document.getElementById('fetch-msg').textContent = '⏳ avvio download…';
  setWorker('walking');
  document.getElementById('workbar-label').textContent = 'avvio…';
  document.getElementById('log-panel').classList.remove('hidden');
  try {
    const r = await fetch('/api/jobs/tiktok/import', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ videos })
    });
    const d = await r.json();
    if (!r.ok) throw new Error(d.error || 'errore avvio job');
    startJobSSE(d.job_id);
  } catch (e) {
    setWorker('idle');
    document.getElementById('workbar-label').textContent = 'errore: ' + e.message;
  }
});

// === Bottone "Analizza stile" ===
document.getElementById('btn-learn').addEventListener('click', async () => {
  const name = document.getElementById('pf-name').value.trim();
  const acc = document.getElementById('tk-user').value.trim();
  const msg = document.getElementById('fetch-msg');
  if (!name) { msg.textContent = '⚠ inserisci un nome profilo'; return; }
  document.getElementById('btn-learn').disabled = true;
  msg.textContent = '⏳ avvio analisi stile…';
  setWorker('walking');
  document.getElementById('workbar-label').textContent = 'avvio…';
  document.getElementById('log-panel').classList.remove('hidden');
  try {
    const r = await fetch('/api/styles/learn', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ profile_name: name, tiktok_account: acc })
    });
    const d = await r.json();
    if (!r.ok) throw new Error(d.error || 'errore');
    startJobSSE(d.job_id);
    msg.textContent = `analisi in corso su ${d.videos} video…`;
  } catch (e) {
    setWorker('idle');
    msg.textContent = '✗ ' + e.message;
    document.getElementById('btn-learn').disabled = false;
  }
});

// === Log panel ===
document.getElementById('btn-log-toggle').addEventListener('click', () => {
  document.getElementById('log-panel').classList.toggle('hidden');
});
document.getElementById('log-close').addEventListener('click', () => {
  document.getElementById('log-panel').classList.add('hidden');
});

// === Profiles list ===
let currentProfileId = null;

async function loadProfiles() {
  try {
    const r = await fetch('/api/styles');
    const d = await r.json();
    const ul = document.getElementById('profiles-list');
    if (!d.profiles || !d.profiles.length) {
      ul.innerHTML = '<li class="muted">nessun profilo</li>';
      return;
    }
    ul.innerHTML = d.profiles.map(p => `
      <li data-id="${p.id}" class="profile-item">
        <div class="profile-info">
          <strong>${escapeHtml(p.name)}</strong>
          <span class="pid">#${p.id} · @${escapeHtml(p.tiktok_account || '?')}</span>
        </div>
        <button class="profile-del" data-del-id="${p.id}" title="Elimina profilo" aria-label="Elimina profilo">
          <svg viewBox="0 0 16 16" width="14" height="14" fill="none" stroke="currentColor" stroke-width="1.2" stroke-linecap="round" stroke-linejoin="round">
            <path d="M 2.5 4 L 13.5 4"/>
            <path d="M 6.5 4 L 6.5 2 L 9.5 2 L 9.5 4"/>
            <path d="M 3.5 4 L 4.5 14 Q 4.5 14.5, 5 14.5 L 11 14.5 Q 11.5 14.5, 11.5 14 L 12.5 4"/>
            <path d="M 6.5 7 L 6.5 12 M 8 7 L 8 12 M 9.5 7 L 9.5 12"/>
          </svg>
        </button>
      </li>
    `).join('');
    ul.querySelectorAll('.profile-item').forEach(li => {
      li.addEventListener('click', (e) => {
        if (e.target.closest('.profile-del')) return;
        showInspector(li.dataset.id);
      });
    });
    ul.querySelectorAll('.profile-del').forEach(btn => {
      btn.addEventListener('click', async (e) => {
        e.stopPropagation();
        const id = btn.dataset.delId;
        if (!confirm('Eliminare il profilo #' + id + '? Operazione irreversibile.')) return;
        try {
          const r2 = await fetch('/api/styles/' + id, { method: 'DELETE' });
          if (!r2.ok) {
            const err = await r2.json().catch(() => ({}));
            alert('Errore: ' + (err.error || r2.status));
            return;
          }
          if (currentProfileId == id) {
            document.getElementById('inspector-body').innerHTML =
              '<p class="muted">Profilo #' + id + ' eliminato.</p>';
            currentProfileId = null;
          }
          loadProfiles();
        } catch (e) {
          alert('Errore di rete: ' + e.message);
        }
      });
    });
  } catch {}
}
loadProfiles();
setInterval(loadProfiles, 15000);

// === Inspector ===
async function showInspector(profileId) {
  currentProfileId = profileId;
  const body = document.getElementById('inspector-body');
  body.innerHTML = '<p class="muted">carico…</p>';
  try {
    const [rProfile, rFonts] = await Promise.all([
      fetch('/api/styles/' + profileId),
      fetch('/api/styles/' + profileId + '/font-previews')
    ]);
    const p = await rProfile.json();
    const f = await rFonts.json();
    const s = p.style || {};
    const sub = s.subtitle_style || {};
    const nar = s.narrative || {};
    const met = s.metrics || {};
    const esc = escapeHtml;

    body.innerHTML = `
      <div class="insp-section">
        <h4>${esc(p.name)} <span class="pid">#${p.id}</span></h4>
        <div class="insp-kv"><b>Account:</b> @${esc(p.tiktok_account || '?')}</div>
        <div class="insp-kv"><b>Creato:</b> ${esc(p.created_at)}</div>
      </div>

      <div class="insp-section">
        <h4>Metriche</h4>
        <div class="insp-kv"><b>Shot medi:</b> ${met.avg_shot_duration_sec ?? '?'}s</div>
        <div class="insp-kv"><b>Totale shot:</b> ${met.total_shots ?? '?'}</div>
        <div class="insp-kv"><b>Video analizzati:</b> ${met.total_videos ?? '?'}</div>
      </div>

      <div class="insp-section">
        <h4>Stile tecnico</h4>
        <div class="insp-kv"><b>Transizione:</b> ${esc((s.technical_style||{}).dominant_transition || '—')}</div>
        <div class="insp-kv"><b>Movimento:</b> ${esc((s.technical_style||{}).dominant_motion || '—')}</div>
        <div class="insp-kv"><b>Composizione:</b> ${esc((s.technical_style||{}).dominant_composition || '—')}</div>
      </div>

      <div class="insp-section">
        <h4>Narrativa</h4>
        <div class="insp-kv"><b>Hook:</b><div class="insp-text">${esc(nar.hook_style || '—')}</div></div>
        <div class="insp-kv" style="margin-top:6px"><b>Struttura:</b><div class="insp-text">${esc(nar.narrative_structure || '—')}</div></div>
        <div class="insp-kv" style="margin-top:6px"><b>Tono:</b><div class="insp-text">${esc(nar.tone_of_voice || '—')}</div></div>
        <div class="insp-kv" style="margin-top:6px"><b>CTA:</b><div class="insp-text">${esc(nar.call_to_action_style || '—')}</div></div>
      </div>

      <div class="insp-section">
        <h4>Font sottotitoli (Top ${(f.fonts || []).length})</h4>
        <div class="insp-kv" style="margin-bottom:8px">
          <b>Match attuale:</b>
          <span style="color:var(--accent)">${esc(sub.font_name || '—')}</span>
          ${sub.font_overridden ? '<span class="muted small"> (override manuale)</span>' : ''}
        </div>
        ${(f.fonts || []).map(fo => `
          <div class="font-row ${fo.is_best ? 'best' : ''}">
            <img class="font-preview" src="${fo.preview}" alt="">
            <div>
              <div class="font-name">${fo.rank}. ${esc(fo.name)}
                <button class="font-use" data-font="${esc(fo.name)}">usa</button>
              </div>
              <div class="font-meta">
                score ${(fo.score ?? 0).toFixed(3)} ·
                ssim ${(fo.ssim ?? 0).toFixed(3)} ·
                ar ${(fo.ar ?? 0).toFixed(2)} ·
                ink ${(fo.ink ?? 0).toFixed(2)}
              </div>
            </div>
          </div>
        `).join('')}
      </div>

      <div class="insp-section">
        <h4>Testo OCR campionato</h4>
        <div class="insp-text">${(sub.sample_texts || []).map(esc).join(' · ')}</div>
      </div>
    `;

    body.querySelectorAll('.font-use').forEach(btn => {
      btn.addEventListener('click', async (e) => {
        e.stopPropagation();
        const fontName = btn.dataset.font;
        const r = await fetch('/api/styles/' + profileId + '/set-font', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ font_name: fontName })
        });
        if (r.ok) {
          btn.textContent = '✓';
          setTimeout(() => showInspector(profileId), 400);
        }
      });
    });
  } catch (e) {
    body.innerHTML = '<p class="muted">errore: ' + esc(e.message) + '</p>';
  }
}

// === Elimina video di riferimento ===
document.getElementById('btn-clear-refs').addEventListener('click', async () => {
  if (!confirm('Eliminare TUTTI i video in media/style_ref/?')) return;
  const msg = document.getElementById('fetch-msg');
  try {
    const r = await fetch('/api/style_ref/clear', { method: 'POST' });
    const d = await r.json();
    if (!r.ok) throw new Error(d.error || 'errore');
    msg.textContent = '✓ ' + (d.message || 'fatto');
  } catch (e) {
    msg.textContent = '✗ ' + e.message;
  }
});

// === Bridge workbar: job dal brain (iframe) ===
window.addEventListener('message', (ev) => {
  const d = ev.data;
  if (!d || typeof d !== 'object' || typeof d.type !== 'string') return;
  if (!d.type.startsWith('brain:')) return;

  const wl = document.getElementById('workbar-label');
  const wp = document.getElementById('workbar-pct');
  const pf = document.getElementById('progress-fill');
  const lp = document.getElementById('log-panel');

  if (d.type === 'brain:jobStart') {
    if (lp) lp.classList.remove('hidden');
    setWorker('walking');
    if (wl) wl.textContent = `brain: job #${d.jobId} avviato`;
    if (wp) wp.textContent = '0%';
    if (pf) pf.style.width = '0%';
    // mostra workbar se collassata
    const wb = document.getElementById('workbar');
    if (wb) wb.classList.remove('collapsed');
    // svuota log-panel per nuovo job
    clearLog();
    appendLog(`[brain] job #${d.jobId} avviato`, 'info');
    return;
  }

  if (d.type === 'brain:log') {
    if (lp) lp.classList.remove('hidden');
    appendLog(d.message, d.level || 'info');
    return;
  }

  if (d.type === 'brain:progress') {
    const st = d.status || 'running';
    if (st === 'done') {
      setWorker('done');
      if (wl) wl.textContent = d.label || 'brain: completato';
      if (wp) wp.textContent = '100%';
      if (pf) pf.style.width = '100%';
    } else if (st === 'error') {
      setWorker('idle');
      if (wl) wl.textContent = d.label || 'brain: errore';
      if (wp) wp.textContent = d.error ? (d.error.slice(0, 80)) : '—';
    } else {
      setWorker('walking');
      if (wl) wl.textContent = d.label || 'brain: in corso';
      if (wp) wp.textContent = (d.progress || 0) + '%';
      if (pf) pf.style.width = (d.progress || 0) + '%';
    }
  }
});

// === Step 8.1: mode switcher ===
document.querySelectorAll('.mode-tab').forEach(tab => {
  tab.addEventListener('click', () => {
    document.querySelectorAll('.mode-tab').forEach(t => t.classList.remove('active'));
    tab.classList.add('active');
    const mode = tab.dataset.mode;
    const ml = document.getElementById('mode-learn');
    if (ml) ml.classList.toggle('hidden', mode !== 'learn');
    const studioPanel = document.getElementById('mode-studio');
    if (studioPanel) studioPanel.classList.toggle('hidden', mode !== 'studio');
    if (mode === 'studio' && typeof studioLoad === 'function') studioLoad();
    document.getElementById('mode-apply').classList.toggle('hidden', mode !== 'apply');
    const st = document.getElementById('mode-storage');
    if (st) st.classList.toggle('hidden', mode !== 'storage');
    const bn = document.getElementById('mode-brain');
    if (bn) bn.classList.toggle('hidden', mode !== 'brain');
    const sbs = document.getElementById('mode-subs');
    if (sbs) sbs.classList.toggle('hidden', mode !== 'subs');

    if (mode === 'apply') {
      loadRawList();
      loadApplyProfiles();
    }
    if (mode === 'brain') {
      const ifr = document.getElementById('brain-iframe');
      if (ifr && ifr.dataset.loaded !== '1') {
        ifr.src = '/brain?embed=1';
        ifr.dataset.loaded = '1';
      } else if (ifr && ifr.contentWindow && ifr.contentWindow.refreshState) {
        try { ifr.contentWindow.refreshState(); } catch(_) {}
      }
    }
  });
});

// === Step 8.1: upload raw ===
const rawInput = document.getElementById('raw-input');
const dropzone = document.getElementById('dropzone');
const rawProgress = document.getElementById('raw-progress');
const rawProgressFill = document.getElementById('raw-progress-fill');

['dragenter', 'dragover'].forEach(ev => {
  dropzone.addEventListener(ev, e => { e.preventDefault(); dropzone.classList.add('dragover'); });
});
['dragleave', 'drop'].forEach(ev => {
  dropzone.addEventListener(ev, e => { e.preventDefault(); dropzone.classList.remove('dragover'); });
});
dropzone.addEventListener('drop', e => {
  if (e.dataTransfer.files.length) uploadRawFiles(e.dataTransfer.files);
});
rawInput.addEventListener('change', () => {
  if (rawInput.files.length) uploadRawFiles(rawInput.files);
  rawInput.value = '';
});

function uploadRawFiles(files) {
  const fd = new FormData();
  for (const f of files) fd.append('files', f);

  rawProgress.classList.remove('hidden');
  rawProgressFill.style.width = '0%';

  const xhr = new XMLHttpRequest();
  xhr.open('POST', '/api/raw/upload');
  xhr.upload.onprogress = (e) => {
    if (e.lengthComputable) {
      const pct = Math.round((e.loaded / e.total) * 100);
      rawProgressFill.style.width = pct + '%';
    }
  };
  xhr.onload = () => {
    rawProgress.classList.add('hidden');
    if (xhr.status >= 200 && xhr.status < 300) {
      const d = JSON.parse(xhr.responseText);
      document.getElementById('apply-msg').textContent = `✓ caricati ${d.count} file`;
      loadRawList();
    } else {
      try {
        const d = JSON.parse(xhr.responseText);
        document.getElementById('apply-msg').textContent = '✗ ' + (d.error || 'errore upload');
      } catch { document.getElementById('apply-msg').textContent = '✗ errore upload'; }
    }
  };
  xhr.onerror = () => {
    rawProgress.classList.add('hidden');
    document.getElementById('apply-msg').textContent = '✗ errore di rete';
  };
  xhr.send(fd);
}

async function loadRawList() {
  try {
    const r = await fetch('/api/raw/list');
    const d = await r.json();
    const grid = document.getElementById('raw-grid');
    document.getElementById('raw-info').textContent = `${d.count} file`;
    if (typeof loadAnalysisInfo === 'function') loadAnalysisInfo();
    if (!d.files || !d.files.length) {
      grid.innerHTML = '<div class="empty">Nessun video raw caricato.</div>';
      return;
    }
    grid.innerHTML = d.files.map(f => `
      <div class="raw-card">
        <button class="raw-del" data-name="${escapeHtml(f.name)}" title="Elimina">🗑</button>
        <div class="raw-name">${escapeHtml(f.name)}</div>
        <div class="raw-meta">${f.size_mb} MB</div>
      </div>
    `).join('');
    grid.querySelectorAll('.raw-del').forEach(btn => {
      btn.addEventListener('click', async () => {
        const name = btn.dataset.name;
        if (!confirm('Eliminare "' + name + '"?')) return;
        const r2 = await fetch('/api/raw/delete', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ name })
        });
        if (r2.ok) loadRawList();
        else alert('Errore eliminazione');
      });
    });
  } catch {}
}

document.getElementById('btn-raw-clear').addEventListener('click', async () => {
  if (!confirm('Eliminare TUTTI i video raw?')) return;
  const r = await fetch('/api/raw/clear', { method: 'POST' });
  const d = await r.json();
  document.getElementById('apply-msg').textContent = '✓ ' + (d.message || 'fatto');
  loadRawList();
});

// === Step 8.1: lista profili nel select Apply ===
async function loadApplyProfiles() {
  try {
    const r = await fetch('/api/styles');
    const d = await r.json();
    const sel = document.getElementById('apply-profile-select');
    const current = sel.value;
    sel.innerHTML = '<option value="">— scegli —</option>' +
      (d.profiles || []).map(p =>
        `<option value="${p.id}">#${p.id} · ${escapeHtml(p.name)}</option>`
      ).join('');
    if (current) sel.value = current;
  } catch {}
}

// === Step 8.2: analizza raw ===
document.getElementById('btn-raw-analyze').addEventListener('click', async () => {
  const msg = document.getElementById('apply-msg');
  msg.textContent = '⏳ avvio analisi raw…';
  setWorker('walking');
  document.getElementById('workbar-label').textContent = 'analisi raw…';
  document.getElementById('log-panel').classList.remove('hidden');

  try {
    const r = await fetch('/api/raw/analyze', { method: 'POST' });
    const d = await r.json();
    if (!r.ok) throw new Error(d.error || 'errore');
    startJobSSE(d.job_id);
    msg.textContent = `analisi in corso su ${d.videos} video…`;
  } catch (e) {
    setWorker('idle');
    msg.textContent = '✗ ' + e.message;
  }
});

async function loadAnalysisInfo() {
  try {
    const r = await fetch('/api/analysis/list');
    const d = await r.json();
    const el = document.getElementById('analysis-info');
    if (!d.manifests || !d.manifests.length) {
      el.textContent = 'nessuna analisi';
      return;
    }
    const m = d.manifests[0];
    el.textContent = `ultima analisi: ${m.videos}/${m.total} video`;
  } catch {}
}
loadAnalysisInfo();

// === Step 8.3-bis: curation scene ===
document.getElementById('btn-curate').addEventListener('click', async () => {
  const sel = document.getElementById('apply-profile-select');
  const pid = sel ? sel.value : null;
  const msg = document.getElementById('apply-msg');
  msg.textContent = '⏳ avvio curation…';
  setWorker('walking');
  document.getElementById('workbar-label').textContent = 'selezione scene buone…';
  document.getElementById('log-panel').classList.remove('hidden');
  try {
    const r = await fetch('/api/curate/run', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ profile_id: pid || null })
    });
    const d = await r.json();
    if (!r.ok) throw new Error(d.error || 'errore');
    startJobSSE(d.job_id);
  } catch (e) {
    setWorker('idle');
    msg.textContent = '✗ ' + e.message;
  }
});

async function loadCuratedInfo() {
  try {
    const r = await fetch('/api/curated/latest');
    const d = await r.json();
    const el = document.getElementById('curated-info');
    if (!d.exists) {
      el.textContent = 'nessuna curation';
      return;
    }
    el.textContent = `curation: ${d.keep} keep / ${d.skip} skip (${d.total})`;
  } catch {}
}
loadCuratedInfo();

// === Step 8.4: render finale ===
document.getElementById('btn-render').addEventListener('click', async () => {
  const sel = document.getElementById('apply-profile-select');
  const pid = (sel && sel.value) ? sel.value : '16';  // default 16, opzionale
  const msg = document.getElementById('apply-msg');
  msg.textContent = '⏳ render in corso…';
  setWorker('walking');
  document.getElementById('workbar-label').textContent = 'render finale…';
  document.getElementById('log-panel').classList.remove('hidden');
  try {
    const r = await fetch('/api/render/run', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ profile_id: pid })
    });
    const rawText = await r.text();
    if (!r.ok) throw new Error(`HTTP ${r.status} · ${rawText.slice(0,200)}`);
    const d = JSON.parse(rawText);
    startJobSSE(d.job_id);
    // al termine, refresh video
    setTimeout(loadLatestRender, 100);
  } catch (e) {
    setWorker('idle');
    msg.textContent = '✗ ' + e.message;
  }
});

async function loadLatestRender() {
  try {
    const r = await fetch('/api/render/latest');
    const d = await r.json();
    const info = document.getElementById('render-info');
    if (!d.exists) {
      info.textContent = 'nessun render';
      return;
    }
    info.textContent = `render #${d.job_id} · ${d.size_mb} MB`;
    const box = document.getElementById('render-result');
    box.classList.remove('hidden');
    const vid = document.getElementById('preview-video');
    // Cache-bust per evitare che il browser mostri la versione precedente
    vid.src = d.url + '?t=' + Date.now();
    document.getElementById('render-file').textContent = d.file;
    document.getElementById('render-size').textContent = d.size_mb + ' MB';

    // Costruisci lo script voce dai testi correnti delle scene dell'EDL
    try {
      const rEdl = await fetch('/api/edl/latest');
      const dEdl = await rEdl.json();
      if (dEdl.exists && dEdl.edl && dEdl.edl.timeline) {
        const lines = dEdl.edl.timeline.map(seg => (seg.subtitle_text || '').trim()).filter(Boolean);
        document.getElementById('render-script').textContent =
          lines.length ? lines.join('\n') : '(nessun testo nelle scene)';
      } else {
        document.getElementById('render-script').textContent =
          d.voiceover_script || '(nessuno script)';
      }
    } catch (e) {
      document.getElementById('render-script').textContent =
        d.voiceover_script || '(nessuno script)';
    }
    document.getElementById('btn-download-mp4').href = d.url;
    const srtBtn = document.getElementById('btn-download-srt');
    if (d.srt_url) { srtBtn.href = d.srt_url; srtBtn.style.display = 'inline-block'; }
    else { srtBtn.style.display = 'none'; }
  } catch {}
}
loadLatestRender();

// Override startJobSSE per triggerare loadLatestRender a fine render
const _origStartJobSSE_8_4 = startJobSSE;
startJobSSE = function(jobId) {
  if (currentSource) currentSource.close();
  const es = new EventSource(`/api/jobs/${jobId}/stream`);
  currentSource = es;
  es.onmessage = (ev) => {
    let d; try { d = JSON.parse(ev.data); } catch { return; }
    if (d.type === 'state') {
      setWorker('walking');
      document.getElementById('workbar-label').textContent = d.label || d.status;
      document.getElementById('workbar-pct').textContent = d.progress + '%';
      document.getElementById('progress-fill').style.width = d.progress + '%';
      clearLog();
      (d.logs || []).forEach(l => appendLog(l.message, l.level));
    } else if (d.type === 'progress') {
      setWorker('walking');
      if (d.label) document.getElementById('workbar-label').textContent = d.label;
      document.getElementById('workbar-pct').textContent = d.progress + '%';
      document.getElementById('progress-fill').style.width = d.progress + '%';
    } else if (d.type === 'log') {
      appendLog(d.message, d.level);
    } else if (d.type === 'done') {
      setWorker('done');
      document.getElementById('workbar-label').textContent = 'completato';
      document.getElementById('workbar-pct').textContent = '100%';
      document.getElementById('progress-fill').style.width = '100%';
      es.close(); currentSource = null;
      const btnLearn = document.getElementById('btn-learn');
      if (btnLearn) btnLearn.disabled = false;
      document.getElementById('fetch-msg').textContent = '✓ job completato';
      const am = document.getElementById('apply-msg');
      if (am) am.textContent = '✓ job completato';
      loadProfiles();
      loadLatestRender();
    } else if (d.type === 'error') {
      setWorker('idle');
      const errText = d.error || 'errore';
      document.getElementById('workbar-label').textContent = '❌ ' + errText.slice(0, 120);
      const am = document.getElementById('apply-msg');
      if (am) am.textContent = '❌ ' + errText.slice(0, 200);
      document.getElementById('log-panel').classList.remove('hidden');
      es.close(); currentSource = null;
    }
  };
  es.onerror = () => { es.close(); currentSource = null; setWorker('idle'); };
};

// === FIX: handler mancante btn-apply-generate ===
(function bindGenerateEdl() {
  const btn = document.getElementById('btn-apply-generate');
  if (!btn) { console.warn('btn-apply-generate non trovato'); return; }
  if (btn.dataset.bound === '1') { return; }
  btn.dataset.bound = '1';

  btn.addEventListener('click', async () => {
    const sel = document.getElementById('apply-profile-select');
    const pid = sel ? sel.value : null;
    const msg = document.getElementById('apply-msg');
    if (!pid) { if (msg) msg.textContent = '⚠ scegli un profilo di stile'; return; }

    if (msg) msg.textContent = '⏳ generazione EDL in corso…';
    if (typeof setWorker === 'function') setWorker('walking');
    const wl = document.getElementById('workbar-label');
    if (wl) wl.textContent = 'EDL con Claude…';
    const lp = document.getElementById('log-panel');
    if (lp) lp.classList.remove('hidden');

    try {
      const venueName = (document.getElementById('venue-name')?.value || '').trim();
      const r = await fetch('/api/apply/generate', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ profile_id: pid, venue_name: venueName })
      });
      const raw = await r.text();
      if (!r.ok) throw new Error(`HTTP ${r.status} · ${raw.slice(0,200)}`);
      const d = JSON.parse(raw);
      if (typeof startJobSSE === 'function') startJobSSE(d.job_id);
      if (msg) msg.textContent = 'EDL in corso…';
    } catch (e) {
      if (typeof setWorker === 'function') setWorker('idle');
      if (msg) msg.textContent = '✗ ' + e.message;
      console.error(e);
    }
  });

  console.log('✅ handler btn-apply-generate bindato');
})();

// === Brain generate handler (Creator Brain) ===
(function bindBrainGenerate() {
  const btn = document.getElementById('btn-brain-generate');
  if (!btn) { console.warn('btn-brain-generate non trovato'); return; }
  if (btn.dataset.bound === '1') { return; }
  btn.dataset.bound = '1';

  btn.addEventListener('click', async () => {
    const sel = document.getElementById('apply-profile-select');
    const pid = (sel && sel.value) ? sel.value : 16;  // default 16, NON obbligatorio
    const msg = document.getElementById('apply-msg');

    if (msg) msg.textContent = '🧠 generazione EDL con Creator Brain…';
    if (typeof setWorker === 'function') setWorker('walking');
    const wl = document.getElementById('workbar-label');
    if (wl) wl.textContent = 'EDL con Brain…';
    const lp = document.getElementById('log-panel');
    if (lp) lp.classList.remove('hidden');

    try {
      const venueName = (document.getElementById('venue-name')?.value || '').trim() || 'Wing Stop Milano';
      const r = await fetch('/api/brain/generate', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ profile_id: pid, venue_name: venueName })
      });
      const raw = await r.text();
      if (!r.ok) throw new Error(`HTTP ${r.status} · ${raw.slice(0,200)}`);
      const d = JSON.parse(raw);
      if (typeof startJobSSE === 'function') startJobSSE(d.job_id);
      if (msg) msg.textContent = '🧠 EDL Brain in corso…';
    } catch (e) {
      if (typeof setWorker === 'function') setWorker('idle');
      if (msg) msg.textContent = '✗ ' + e.message;
      console.error(e);
    }
  });

  console.log('✅ handler btn-brain-generate bindato');
})();

// === Brain Edit from Master (approccio B) ===
(function bindBrainFromMaster() {
  const btn = document.getElementById('btn-brain-master');
  if (!btn) return;
  if (btn.dataset.bound === '1') return;
  btn.dataset.bound = '1';

  btn.addEventListener('click', async () => {
    const sel = document.getElementById('apply-profile-select');
    const pid = (sel && sel.value) ? sel.value : 16;
    const msg = document.getElementById('apply-msg');

    if (msg) msg.textContent = '🎬 Generazione da Master (approccio B)…';
    if (typeof setWorker === 'function') setWorker('walking');
    const wl = document.getElementById('workbar-label');
    if (wl) wl.textContent = 'Edit da Master…';
    const lp = document.getElementById('log-panel');
    if (lp) lp.classList.remove('hidden');

    try {
      const venueName = (document.getElementById('venue-name')?.value || '').trim() || 'Wing Stop Milano';
      const r = await fetch('/api/brain/generate_from_master', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ profile_id: pid, venue_name: venueName })
      });
      const raw = await r.text();
      if (!r.ok) throw new Error(`HTTP ${r.status} · ${raw.slice(0,200)}`);
      const d = JSON.parse(raw);
      if (typeof startJobSSE === 'function') startJobSSE(d.job_id);
      if (msg) msg.textContent = `🎬 Master in corso (${d.n_raws} raw)…`;
    } catch (e) {
      if (typeof setWorker === 'function') setWorker('idle');
      if (msg) msg.textContent = '✗ ' + e.message;
      console.error(e);
    }
  });

  console.log('✅ handler btn-brain-master bindato');
})();


// ============================================================
// ==== STEP 8.7: Script voce per scena + registrazione ======
// ============================================================
// Variabile globale per essere visibile dagli altri moduli
window.currentEdlJobId = null;

(function voiceModule() {
  // Rileva il mimetype audio supportato dal browser
  function pickAudioMime() {
    const candidates = [
      'audio/webm;codecs=opus',
      'audio/webm',
      'audio/mp4;codecs=mp4a.40.2',
      'audio/mp4',
      'audio/ogg;codecs=opus',
      'audio/ogg',
    ];
    if (typeof MediaRecorder === 'undefined' || !MediaRecorder.isTypeSupported) {
      return '';
    }
    for (const c of candidates) {
      if (MediaRecorder.isTypeSupported(c)) return c;
    }
    return '';
  }

  const AUDIO_MIME = pickAudioMime();
  const AUDIO_EXT = AUDIO_MIME.includes('mp4') ? 'm4a' : 'webm';
  console.log('🎤 Codec audio scelto:', AUDIO_MIME || '(default)');

  let currentEdl = null;
  let currentEdlJobId = null;
  let mediaRecorders = {};   // scene_id -> MediaRecorder
  let recordedBlobs = {};    // scene_id -> Blob (per riascolto locale)
  const recordingsRemote = {};  // scene_id -> true se sul server

  const $ = (id) => document.getElementById(id);

  async function loadEdl() {
    try {
      const r = await fetch('/api/edl/latest');
      const d = await r.json();
      if (!d.exists) {
        $('voice-grid').innerHTML = '<div class="empty">Nessun EDL. Genera prima il montaggio.</div>';
        return;
      }
      currentEdl = d.edl;
      currentEdlJobId = d.edl.edl_job_id;
      window.currentEdlJobId = currentEdlJobId;
      await loadVoiceStatus();
      renderVoiceGrid();
    } catch (e) {
      console.error('loadEdl', e);
    }
  }

  async function loadVoiceStatus() {
    if (!currentEdlJobId) return;
    try {
      const r = await fetch(`/api/voice/status/${currentEdlJobId}`);
      const d = await r.json();
      Object.keys(d.recordings || {}).forEach(sid => { recordingsRemote[sid] = true; });
    } catch (e) { console.error(e); }
  }

  function renderVoiceGrid() {
    const grid = $('voice-grid');
    if (!currentEdl || !currentEdl.timeline) {
      grid.innerHTML = '<div class="empty">Nessun EDL.</div>';
      return;
    }
    grid.innerHTML = currentEdl.timeline.map((seg, i) => {
      const sid = seg.scene_id;
      const has = recordingsRemote[String(sid)] || recordedBlobs[sid];
      return `
        <div class="voice-card ${has ? 'recorded' : ''}" data-sid="${sid}">
          <div class="voice-thumb-wrap">
            <video class="voice-thumb-video" muted playsinline preload="metadata"
                   src="/api/scene/${currentEdlJobId}/${sid}/clip.mp4"
                   data-sid="${sid}"></video>
            <div class="voice-thumb-progress" data-sid="${sid}"></div>
          </div>
          <div class="voice-body">
            <div class="voice-header">
              <b>Scena ${i + 1}</b> · ${escapeHtml(seg.clip_name || '')} ·
              ${Number(seg.duration || 0).toFixed(2)}s · ${escapeHtml(seg.role || '')}
            </div>
            <textarea class="voice-text" data-sid="${sid}" rows="2">${escapeHtml(seg.subtitle_text || '')}</textarea>
          </div>
          <div class="voice-actions-cell">
            <button class="btn-rec ${has ? 'has-rec' : ''}" data-sid="${sid}">
              ${has ? '🔁 Riregistra' : '🎤 Registra'}
            </button>
            <button class="btn-mini btn-play" data-sid="${sid}" ${has ? '' : 'disabled'}>▶ Riascolta</button>
            <button class="btn-mini btn-rec-del" data-sid="${sid}" data-has="${has ? '1' : '0'}" title="Cancella registrazione">🗑 Cancella</button>
            <div class="voice-status" data-sid="${sid}">${has ? 'registrata' : 'non registrata'}</div>
          </div>
        </div>
      `;
    }).join('');

    // Textarea → salva modifiche (debounce su blur)
    grid.querySelectorAll('.voice-text').forEach(ta => {
      ta.addEventListener('blur', async () => {
        const sid = parseInt(ta.dataset.sid, 10);
        const text = ta.value;
        // aggiorna EDL in memoria
        const seg = currentEdl.timeline.find(s => s.scene_id === sid);
        if (seg) seg.subtitle_text = text;
        try {
          await fetch(`/api/edl/${currentEdlJobId}/update-subtitle`, {
            method: 'POST',
            headers: {'Content-Type':'application/json'},
            body: JSON.stringify({ scene_id: sid, subtitle_text: text })
          });
        } catch (e) { console.error(e); }
      });
    });

    // Bottone registra / stop
    grid.querySelectorAll('.btn-rec').forEach(btn => {
      btn.addEventListener('click', () => toggleRecord(parseInt(btn.dataset.sid, 10), btn));
    });

    // Bottone riascolta
    grid.querySelectorAll('.btn-play').forEach(btn => {
      btn.addEventListener('click', () => playRecording(parseInt(btn.dataset.sid, 10)));
    });

    // Bottone cancella registrazione
    grid.querySelectorAll('.btn-rec-del').forEach(btn => {
      const hasRec = btn.getAttribute('data-has') === '1';
      if (!hasRec) return;

      btn.addEventListener('click', async (e) => {
        e.stopPropagation();
        const sid = parseInt(btn.dataset.sid, 10);
        if (!confirm(`Cancellare la registrazione della Scena #${sid + 1}?`)) return;

        try {
          const r = await fetch('/api/voice/delete', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ edl_job_id: window.currentEdlJobId, scene_id: sid })
          });
          const d = await r.json();
          if (!r.ok) throw new Error(d.error || 'errore');

          // Rimuovi dal blob locale e dal remote
          delete recordedBlobs[sid];
          delete recordingsRemote[String(sid)];

          // Ricarica il grid
          renderVoiceGrid();
        } catch (err) {
          alert('Errore: ' + err.message);
        }
      });
    });

    // Video preview: mostra solo il primo frame, no hover, no autoplay
    grid.querySelectorAll('.voice-thumb-video').forEach(v => {
      const sid = parseInt(v.dataset.sid, 10);
      const progress = grid.querySelector(`.voice-thumb-progress[data-sid="${sid}"]`);
      v.addEventListener('loadedmetadata', () => {
        v.currentTime = 0.01; // forza rendering del primo frame come poster
      });
      v.addEventListener('timeupdate', () => {
        if (progress && v.duration) {
          progress.style.width = (v.currentTime / v.duration * 100) + '%';
        }
      });
    });

    updateSummary();
  }

  async function toggleRecord(sid, btn) {
    const status = document.querySelector(`.voice-status[data-sid="${sid}"]`);

    // Se sta registrando → stop
    if (mediaRecorders[sid] && mediaRecorders[sid].state === 'recording') {
      mediaRecorders[sid].stop();
      return;
    }

    // Altrimenti inizia
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      const mrOptions = AUDIO_MIME ? { mimeType: AUDIO_MIME } : {};
      const mr = new MediaRecorder(stream, mrOptions);
      const chunks = [];
      mr.ondataavailable = (e) => { if (e.data.size > 0) chunks.push(e.data); };
      mr.onstop = async () => {
        stream.getTracks().forEach(t => t.stop());

        // Ferma e resetta il video della scena
        const vStop = document.querySelector(`.voice-thumb-video[data-sid="${sid}"]`);
        if (vStop) {
          delete vStop.dataset.recording;
          vStop.pause();
          vStop.loop = false;
          vStop.currentTime = 0;
          const prog = document.querySelector(`.voice-thumb-progress[data-sid="${sid}"]`);
          if (prog) prog.style.width = '0%';
        }

        const blob = new Blob(chunks, { type: AUDIO_MIME || 'audio/webm' });
        recordedBlobs[sid] = blob;
        // Upload
        const fd = new FormData();
        fd.append('audio', blob, `scene_${sid}.${AUDIO_EXT}`);
        fd.append('edl_job_id', currentEdlJobId);
        fd.append('scene_id', sid);
        if (window.__voiceState) window.__voiceState(sid, 'uploading');
        else { status.textContent = 'carico…'; status.className = 'voice-status'; }
        try {
          const r = await fetch('/api/voice/upload', { method: 'POST', body: fd });
          const d = await r.json();
          if (r.ok) {
            recordingsRemote[String(sid)] = true;
            if (window.__voiceState) window.__voiceState(sid, 'registered');
            renderVoiceGrid();
          } else {
            if (window.__voiceState) window.__voiceState(sid, 'error');
            else status.textContent = '✗ ' + (d.error || 'errore');
          }
        } catch (e) {
          if (window.__voiceState) window.__voiceState(sid, 'error');
          else status.textContent = '✗ ' + e.message;
        }
      };

      mediaRecorders[sid] = mr;
      mr.start();
      btn.classList.add('recording');
      btn.textContent = '⏹ Stop';
      if (window.__voiceState) window.__voiceState(sid, 'rec');
      else { status.textContent = '● REC…'; status.className = 'voice-status rec'; }

      // Avvia il video della scena UNA VOLTA (no loop) per sincronizzare la durata
      const v = document.querySelector(`.voice-thumb-video[data-sid="${sid}"]`);
      if (v) {
        v.dataset.recording = '1';
        v.currentTime = 0;
        v.loop = false;
        v.play().catch(() => {});
      }
    } catch (e) {
      status.textContent = '✗ ' + e.message;
    }
  }

  function playRecording(sid) {
    const blob = recordedBlobs[sid];
    if (blob) {
      const url = URL.createObjectURL(blob);
      new Audio(url).play();
      return;
    }
    // Prova dal server — cerca sia webm che m4a
    if (currentEdlJobId && recordingsRemote[String(sid)]) {
      const tryUrls = [
        `/voiceovers/edl_${currentEdlJobId}/scene_${sid}.m4a`,
        `/voiceovers/edl_${currentEdlJobId}/scene_${sid}.webm`,
      ];
      const a = new Audio();
      a.src = tryUrls[0];
      a.onerror = () => { a.src = tryUrls[1]; a.play().catch(() => {}); };
      a.play().catch(() => {});
    }
  }

  function updateSummary() {
    if (!currentEdl) return;
    const total = currentEdl.timeline.length;
    const done = currentEdl.timeline.filter(s =>
      recordingsRemote[String(s.scene_id)] || recordedBlobs[s.scene_id]
    ).length;
    $('voice-summary').textContent = `${done}/${total} scene registrate`;
    $('btn-assemble').disabled = done < 1;
    $('assemble-info').textContent = done === total
      ? '✓ pronto per il montaggio finale'
      : `${total - done} scene senza voce`;
  }

  // Bottone monta voce
  document.addEventListener('click', async (e) => {
    if (e.target && e.target.id === 'btn-assemble') {
      if (!currentEdlJobId) return;
      if (!confirm('Montare la voce sul video? Verrà creato un nuovo MP4 con la tua voce.')) return;
      e.target.disabled = true;
      $('assemble-info').textContent = '⏳ montaggio in corso…';
      try {
        const r = await fetch(`/api/voice/assemble/${currentEdlJobId}`, { method: 'POST' });
        const d = await r.json();
        if (!r.ok) throw new Error(d.error || 'errore');
        if (typeof startJobSSE === 'function') startJobSSE(d.job_id);
      } catch (err) {
        $('assemble-info').textContent = '✗ ' + err.message;
        e.target.disabled = false;
      }
    }
  });

  // Trigger caricamento all'apertura tab Apply e quando un EDL è generato
  const origLoadLatestRender = window.loadLatestRender;
  // Probe periodico se compare un EDL nuovo
  let lastEdlJob = null;
  setInterval(async () => {
    try {
      const r = await fetch('/api/edl/latest');
      const d = await r.json();
      if (d.exists && d.edl.edl_job_id !== lastEdlJob) {
        lastEdlJob = d.edl.edl_job_id;
        await loadEdl();
      }
    } catch {}
  }, 4000);
  // Carica subito
  loadEdl();

  // === Espongo API voce per lo Studio ===
  
  // Helper: aggiorna stato voce in TUTTI i .voice-status e bottoni studio
  function updateVoiceState(sid, state) {
    // state: 'idle' | 'rec' | 'uploading' | 'registered' | 'error'
    var txt, cls, btnTxt;
    if (state === 'rec') { txt = '\u25cf REC\u2026'; cls = 'voice-status rec'; btnTxt = '\u23f9 Stop'; }
    else if (state === 'uploading') { txt = 'carico\u2026'; cls = 'voice-status'; btnTxt = '\u23f9 Stop'; }
    else if (state === 'registered') { txt = 'registrata \u2713'; cls = 'voice-status'; btnTxt = '\ud83c\udfa4'; }
    else if (state === 'error') { txt = '\u2717 errore'; cls = 'voice-status'; btnTxt = '\ud83c\udfa4'; }
    else { txt = 'non registrata'; cls = 'voice-status'; btnTxt = '\ud83c\udfa4'; }

    document.querySelectorAll('.voice-status[data-sid="' + sid + '"]').forEach(function(el) {
      el.textContent = txt;
      el.className = cls;
    });
    document.querySelectorAll('.studio-voice-rec[data-sid="' + sid + '"]').forEach(function(b) {
      b.textContent = btnTxt;
      if (state === 'rec') b.classList.add('recording');
      else b.classList.remove('recording');
    });
  }

  // Espongo helper
  window.__voiceState = updateVoiceState;

  window.voiceAPI = {
    toggleRecord: toggleRecord,
    playRecording: playRecording,
    loadVoiceStatus: loadVoiceStatus,
    renderVoiceGrid: renderVoiceGrid,
    getRecordingsRemote: function() { return recordingsRemote; },
    setEdlJobId: function(v) {
      try { currentEdlJobId = v; window.currentEdlJobId = v; } catch(e) {}
    }
  };

})();

// === Indicatore "video di riferimento già caricati" ===
async function loadRefsCount() {
  try {
    const r = await fetch('/api/style_ref/count');
    const d = await r.json();
    const el = document.getElementById('refs-info');
    if (!el) return;
    if (d.count === 0) {
      el.textContent = '0 video di riferimento caricati';
      el.style.color = '';
    } else {
      el.textContent = `✓ ${d.count} video di riferimento già caricati (${d.size_mb} MB)`;
      el.style.color = 'var(--ok)';
    }
  } catch (e) {
    console.error('loadRefsCount', e);
  }
}

// Carica subito
loadRefsCount();

// Ricontrolla ogni 30s (in caso di operazioni da altri tab)
setInterval(loadRefsCount, 30000);

// Ricarica anche quando si cambia tab (Learn/Apply)
document.querySelectorAll('.mode-tab').forEach(t => {
  t.addEventListener('click', () => setTimeout(loadRefsCount, 500));
});

// === Bottone Aggiorna sottotitoli ===
(function bindReburn() {
  const btn = document.getElementById('btn-reburn');
  if (!btn) { console.warn('btn-reburn non trovato'); return; }
  if (btn.dataset.bound === '1') return;
  btn.dataset.bound = '1';

  btn.addEventListener('click', async () => {
    const edlJobId = window.currentEdlJobId;
    if (!edlJobId) { alert('Nessun EDL caricato. Apri una tab Apply con un EDL generato.'); return; }
    if (!confirm('Rigenerare i sottotitoli dal testo attuale e ri-bruciarli sul video?')) return;

    const info = document.getElementById('assemble-info');
    if (info) info.textContent = '⏳ aggiornamento in corso…';
    if (typeof setWorker === 'function') setWorker('walking');
    const wl = document.getElementById('workbar-label');
    if (wl) wl.textContent = 'reburn sottotitoli…';
    const lp = document.getElementById('log-panel');
    if (lp) lp.classList.remove('hidden');

    try {
      const r = await fetch(`/api/render/${edlJobId}/reburn`, { method: 'POST' });
      const raw = await r.text();
      if (!r.ok) throw new Error(`HTTP ${r.status} · ${raw.slice(0,200)}`);
      const d = JSON.parse(raw);
      if (typeof startJobSSE === 'function') startJobSSE(d.job_id);
    } catch (e) {
      if (typeof setWorker === 'function') setWorker('idle');
      if (info) info.textContent = '✗ ' + e.message;
      console.error('reburn error:', e);
    }
  });
  console.log('✅ handler btn-reburn bindato');
})();

// === Refresh del pannello "Script voce" quando si modifica una scena ===
(function refreshScriptOnEdit() {
  // Osserva le textarea delle scene nel voice grid
  const observer = new MutationObserver(() => {
    document.querySelectorAll('.voice-text').forEach(ta => {
      if (ta.dataset.scriptBound === '1') return;
      ta.dataset.scriptBound = '1';
      ta.addEventListener('blur', () => {
        // Ricostruisci il pannello Script voce dal DOM corrente
        const lines = [];
        document.querySelectorAll('.voice-text').forEach(el => {
          const t = (el.value || '').trim();
          if (t) lines.push(t);
        });
        const box = document.getElementById('render-script');
        if (box && lines.length) {
          box.textContent = lines.join('\n');
        }
      });
    });
  });
  observer.observe(document.body, { childList: true, subtree: true });
})();

// ============================================================
// ==== STORAGE MANAGEMENT (Fase 1) ====
// ============================================================
(function storageModule() {
  let currentRenders = [];
  const selectedIds = new Set();

  // Esponi globalmente per il tab switcher
  window.loadStorageSummary = loadStorageSummary;
  window.loadRenders = loadRenders;

  // Gestione click sul tab "Spazio"
  document.querySelectorAll('.mode-tab').forEach(tab => {
    tab.addEventListener('click', () => {
      const mode = tab.dataset.mode;
      if (mode === 'storage') {
        loadStorageSummary();
        loadRenders();
      }
    });
  });

  async function loadStorageSummary() {
    try {
      const r = await fetch('/api/storage/summary');
      const d = await r.json();
      renderDisk(d.disk);
      renderQuickClean(d.categories);
      renderCategories(d.categories);
    } catch (e) {
      console.error('storage summary', e);
    }
  }

  function renderDisk(disk) {
    document.getElementById('disk-used').textContent = disk.used_gb + ' GB';
    document.getElementById('disk-free').textContent = disk.free_gb + ' GB';
    document.getElementById('disk-total').textContent = disk.total_gb + ' GB';
    document.getElementById('disk-fill').style.width = disk.used_pct + '%';
    document.getElementById('storage-info').textContent =
      `${disk.used_gb} GB / ${disk.total_gb} GB (${disk.used_pct}%)`;
  }

  function renderQuickClean(cats) {
    const el = document.getElementById('quick-clean');
    const cleanables = cats.filter(c => c.cleanable);
    if (!cleanables.length) {
      el.innerHTML = '<div class="empty">Nessuna cartella pulibile.</div>';
      return;
    }
    el.innerHTML = cleanables.map(c => `
      <div class="quick-clean-card" data-key="${c.key}">
        <h4>${escapeHtml(c.label)}</h4>
        <div class="qc-size">${c.size_mb} MB</div>
        <div class="qc-count">${c.count} file</div>
        <button data-key="${c.key}" ${c.count === 0 ? 'disabled' : ''}>
          🧹 Pulisci
        </button>
      </div>
    `).join('');

    el.querySelectorAll('button[data-key]').forEach(btn => {
      btn.addEventListener('click', async () => {
        const key = btn.dataset.key;
        const card = btn.closest('.quick-clean-card');
        const label = card.querySelector('h4').textContent;
        if (!confirm(`Pulire "${label}"?\\n\\nI file verranno cancellati ma si rigenerano automaticamente quando servono.`)) return;

        btn.disabled = true;
        btn.textContent = '⏳…';
        try {
          const r = await fetch('/api/storage/cleanup', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ key })
          });
          const d = await r.json();
          if (!r.ok) throw new Error(d.error || 'errore');
          btn.textContent = `✓ ${d.freed_mb} MB`;
          setTimeout(loadStorageSummary, 800);
        } catch (e) {
          btn.textContent = '✗ errore';
          btn.disabled = false;
          alert('Errore: ' + e.message);
        }
      });
    });
  }

  function renderCategories(cats) {
    const el = document.getElementById('storage-categories');
    el.innerHTML = cats.map(c => `
      <div class="storage-cat ${c.cleanable ? '' : 'protected'}">
        <div>
          <div class="sc-label">${escapeHtml(c.label)}</div>
          <div class="sc-count">${c.count} file</div>
        </div>
        <div class="sc-size">${c.size_mb} MB</div>
      </div>
    `).join('');
  }

  async function loadRenders() {
    try {
      const r = await fetch('/api/storage/renders');
      const d = await r.json();
      currentRenders = d.renders || [];
      selectedIds.clear();
      renderRenders();
    } catch (e) {
      console.error('storage renders', e);
    }
  }

  function renderRenders() {
    const el = document.getElementById('renders-list');
    if (!currentRenders.length) {
      el.innerHTML = '<div class="empty">Nessun render salvato.</div>';
      return;
    }

    el.innerHTML = currentRenders.map(r => {
      const badges = [];
      if (r.has_voice) badges.push('voce');
      if (r.has_srt) badges.push('srt');
      if (r.has_merged) badges.push('merge');
      return `
        <div class="render-item ${selectedIds.has(r.edl_job_id) ? 'selected' : ''}"
             data-id="${r.edl_job_id}">
          <input type="checkbox" ${selectedIds.has(r.edl_job_id) ? 'checked' : ''}>
          <div>
            <div class="ri-title">EDL #${r.edl_job_id}</div>
            <div class="ri-meta">${escapeHtml(r.date)} · ${escapeHtml(r.file)}</div>
            <div class="ri-badges">${badges.join(' · ') || '—'}</div>
          </div>
          <div class="ri-size">${r.size_mb} MB</div>
        </div>
      `;
    }).join('');

    el.querySelectorAll('.render-item').forEach(item => {
      const id = parseInt(item.dataset.id, 10);
      const cb = item.querySelector('input[type="checkbox"]');
      const toggle = () => {
        if (selectedIds.has(id)) selectedIds.delete(id);
        else selectedIds.add(id);
        item.classList.toggle('selected', selectedIds.has(id));
        cb.checked = selectedIds.has(id);
        updateDeleteButton();
      };
      // Click sul resto della riga
      item.addEventListener('click', (e) => {
        if (e.target === cb) return; // gestito dal change listener
        toggle();
      });
      // Click direttamente sulla checkbox
      cb.addEventListener('change', () => {
        if (cb.checked) selectedIds.add(id);
        else selectedIds.delete(id);
        item.classList.toggle('selected', selectedIds.has(id));
        updateDeleteButton();
      });
    });

    updateDeleteButton();
  }

  function updateDeleteButton() {
    const btn = document.getElementById('btn-render-delete');
    const n = selectedIds.size;
    btn.textContent = n > 0 ? `🗑 Elimina ${n} selezionati` : '🗑 Elimina selezionati';
    btn.disabled = n === 0;
  }

  document.getElementById('btn-render-delete').addEventListener('click', async () => {
    if (selectedIds.size === 0) return;
    const ids = Array.from(selectedIds);
    const delVoice = document.getElementById('del-voice').checked;
    const delSrt = document.getElementById('del-srt').checked;
    const delMerged = document.getElementById('del-merged').checked;

    const totalMb = currentRenders
      .filter(r => selectedIds.has(r.edl_job_id))
      .reduce((s, r) => s + r.size_mb, 0);

    let msg = `Eliminare ${ids.length} render (${totalMb.toFixed(1)} MB)?\\n\\n`;
    msg += `✓ File render_*.mp4 (base)\\n`;
    msg += delVoice ? '✓ File con voce\\n' : '✗ File con voce NON cancellati\\n';
    msg += delSrt ? '✓ SRT + script voce\\n' : '✗ SRT + script NON cancellati\\n';
    msg += delMerged ? '✓ Merge persistente (reburn non funzionerà più)\\n' : '✗ Merge persistenti mantenuti\\n';

    if (!confirm(msg)) return;

    try {
      const r = await fetch('/api/storage/delete-renders', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          edl_job_ids: ids,
          delete_voice: delVoice,
          delete_srt: delSrt,
          delete_merged: delMerged,
        })
      });
      const d = await r.json();
      if (!r.ok) throw new Error(d.error || 'errore');
      alert(`✓ Eliminati ${d.deleted_count} file (${d.freed_mb} MB liberati)`);
      loadStorageSummary();
      loadRenders();
    } catch (e) {
      alert('Errore: ' + e.message);
    }
  });
})();

// ============================================================
// ==== ANCHORS (primo/ultimo frame) ====
// ============================================================
(function anchorsModule() {
  const $ = (id) => document.getElementById(id);

  function setupSlot(kind) {
    const input = $(`anchor-${kind}-input`);
    const preview = $(`anchor-${kind}-preview`);
    const placeholder = $(`anchor-${kind}-placeholder`);
    const delBtn = $(`anchor-${kind}-del`);
    const drop = input ? input.closest('.anchor-drop') : null;

    if (!input || !drop) return;

    // Click → input file
    drop.addEventListener('click', () => input.click());

    // Drag & drop
    ['dragenter', 'dragover'].forEach(ev => {
      drop.addEventListener(ev, e => {
        e.preventDefault();
        drop.style.borderColor = 'var(--accent)';
      });
    });
    ['dragleave', 'drop'].forEach(ev => {
      drop.addEventListener(ev, e => {
        e.preventDefault();
        drop.style.borderColor = '';
      });
    });
    drop.addEventListener('drop', (e) => {
      if (e.dataTransfer.files.length) uploadAnchor(kind, e.dataTransfer.files[0]);
    });

    // File selected
    input.addEventListener('change', () => {
      if (input.files.length) uploadAnchor(kind, input.files[0]);
      input.value = '';
    });

    // Delete
    delBtn.addEventListener('click', async (e) => {
      e.stopPropagation();
      if (!confirm('Rimuovere questa immagine anchor?')) return;
      try {
        const r = await fetch('/api/anchors/delete', {
          method: 'POST',
          headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({ kind })
        });
        if (!r.ok) throw new Error('errore');
        loadAnchors();
      } catch (err) {
        alert('Errore: ' + err.message);
      }
    });
  }

  async function uploadAnchor(kind, file) {
    const fd = new FormData();
    fd.append('file', file);
    fd.append('kind', kind);

    const preview = $(`anchor-${kind}-preview`);
    const placeholder = $(`anchor-${kind}-placeholder`);
    const delBtn = $(`anchor-${kind}-del`);

    try {
      const r = await fetch('/api/anchors/upload', { method: 'POST', body: fd });
      const d = await r.json();
      if (!r.ok) throw new Error(d.error || 'errore upload');

      preview.src = d.url + '?t=' + Date.now();
      preview.classList.remove('hidden');
      placeholder.classList.add('hidden');
      delBtn.classList.remove('hidden');
      const configBox = $(`anchor-${kind}-config`);
      if (configBox) configBox.classList.remove('hidden');
      console.log(`✓ anchor ${kind} caricato: ${d.size_kb} KB`);
      // Sincronizza lo stato (bottone Rimuovi + preview) con il server
      setTimeout(loadAnchors, 100);
    } catch (err) {
      alert('Errore: ' + err.message);
    }
  }

  async function loadAnchors() {
    try {
      const r = await fetch('/api/anchors/list');
      const d = await r.json();

      ['first', 'last'].forEach(kind => {
        const preview = $(`anchor-${kind}-preview`);
        const placeholder = $(`anchor-${kind}-placeholder`);
        const delBtn = $(`anchor-${kind}-del`);

        const configBox = $(`anchor-${kind}-config`);
        const durInput = $(`anchor-${kind}-duration`);
        const textInput = $(`anchor-${kind}-text`);

        if (d[kind]) {
          preview.src = d[kind].url + '?t=' + (d[kind].mtime || Date.now());
          preview.classList.remove('hidden');
          placeholder.classList.add('hidden');
          delBtn.classList.remove('hidden');
          if (configBox) configBox.classList.remove('hidden');
          if (durInput) durInput.value = d[kind].duration || 1.0;
          if (textInput) textInput.value = d[kind].text || '';
        } else {
          preview.classList.add('hidden');
          preview.removeAttribute('src');
          placeholder.classList.remove('hidden');
          delBtn.classList.add('hidden');
          if (configBox) configBox.classList.add('hidden');
          if (durInput) durInput.value = 1.0;
          if (textInput) textInput.value = '';
        }
      });
    } catch (e) {
      console.error('loadAnchors', e);
    }
  }

  // Espone globalmente per il tab switcher
  window.loadAnchors = loadAnchors;

  // Salva config su blur dei campi
  function setupConfigAutosave(kind) {
    const durInput = $(`anchor-${kind}-duration`);
    const textInput = $(`anchor-${kind}-text`);
    if (!durInput || !textInput) return;

    const save = async () => {
      try {
        await fetch('/api/anchors/config', {
          method: 'POST',
          headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({
            kind,
            duration: parseFloat(durInput.value) || 1.0,
            text: textInput.value || '',
          })
        });
      } catch (e) { console.error('save anchor config', e); }
    };
    durInput.addEventListener('blur', save);
    textInput.addEventListener('blur', save);
    // Debounce anche su input per sicurezza
    let timeout;
    const debouncedSave = () => {
      clearTimeout(timeout);
      timeout = setTimeout(save, 800);
    };
    durInput.addEventListener('input', debouncedSave);
    textInput.addEventListener('input', debouncedSave);
  }

  // Setup
  setupSlot('first');
  setupSlot('last');
  setupConfigAutosave('first');
  setupConfigAutosave('last');
  loadAnchors();

  console.log('✅ anchors module pronto');
})();

// === Duration range custom ===
(function durationRangeModule() {
  const $ = (id) => document.getElementById(id);

  async function load() {
    try {
      const r = await fetch('/api/apply/duration-range');
      const d = await r.json();
      $('dur-min').value = d.min > 0 ? d.min : '';
      $('dur-max').value = d.max > 0 ? d.max : '';
      updateStatus(d.min, d.max);
    } catch (e) { console.error(e); }
  }

  function updateStatus(dmin, dmax) {
    const el = $('dur-status');
    if (!el) return;
    if (dmin > 0 || dmax > 0) {
      el.textContent = `Range attivo: ${dmin || '?'} – ${dmax || '?'} sec`;
      el.style.color = 'var(--ok)';
    } else {
      el.textContent = 'Nessun range impostato — uso il target del profilo';
      el.style.color = 'var(--text-dim)';
    }
  }

  async function save() {
    const dmin = parseFloat($('dur-min').value) || 0;
    const dmax = parseFloat($('dur-max').value) || 0;
    const el = $('dur-status');
    el.textContent = '⏳ salvataggio…';
    try {
      const r = await fetch('/api/apply/duration-range', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({min: dmin, max: dmax})
      });
      const d = await r.json();
      if (!r.ok) throw new Error(d.error || 'errore');
      updateStatus(d.duration_range.min, d.duration_range.max);
    } catch (e) {
      el.textContent = '✗ ' + e.message;
      el.style.color = 'var(--err)';
    }
  }

  const btn = $('btn-save-dur');
  if (btn) btn.addEventListener('click', save);
  // Auto-save su blur
  ['dur-min', 'dur-max'].forEach(id => {
    const el = $(id);
    if (el) el.addEventListener('blur', save);
  });

  load();
})();


// === Simple subtitles upload (2 step) ===
let _subsSelectedFile = null;
let _subsJobId = null;

(function bindSubs() {
  const dropzone = document.getElementById('subs-dropzone');
  const input = document.getElementById('subs-input');
  if (!dropzone || !input) return;
  if (dropzone.dataset.bound === '1') return;
  dropzone.dataset.bound = '1';

  ['dragenter','dragover'].forEach(ev => {
    dropzone.addEventListener(ev, e => { e.preventDefault(); dropzone.classList.add('dragover'); });
  });
  ['dragleave','drop'].forEach(ev => {
    dropzone.addEventListener(ev, e => { e.preventDefault(); dropzone.classList.remove('dragover'); });
  });
  dropzone.addEventListener('drop', e => {
    if (e.dataTransfer.files.length) subsSelectFile(e.dataTransfer.files[0]);
  });
  input.addEventListener('change', () => {
    if (input.files.length) subsSelectFile(input.files[0]);
    input.value = '';
  });

  const btnEl = document.getElementById('btn-subs-elabora');
  if (btnEl) btnEl.addEventListener('click', subsElabora);
  const btnReset = document.getElementById('btn-subs-reset');
  if (btnReset) btnReset.addEventListener('click', subsReset);

  loadSubsFonts();
  console.log('✅ handler subs bindato');
})();

async function loadSubsFonts() {
  try {
    const r = await fetch('/api/subs/fonts');
    const d = await r.json();
    const sel = document.getElementById('subs-font');
    if (!sel) return;
    sel.innerHTML = '';
    (d.fonts || []).forEach(f => {
      const opt = document.createElement('option');
      opt.value = f.family;
      opt.textContent = f.label || f.family;
      if (f.family === 'ObelixPro') opt.selected = true;
      sel.appendChild(opt);
    });
    sel.addEventListener('change', updateFontPreview);
    updateFontPreview();

    const colorEl = document.getElementById('subs-color');
    const hlEl = document.getElementById('subs-highlight');
    if (colorEl) colorEl.addEventListener('input', () => {
      const lab = document.getElementById('subs-color-label');
      if (lab) lab.textContent = colorEl.value.toUpperCase();
      updateFontPreview();
    });
    if (hlEl) hlEl.addEventListener('input', () => {
      const lab = document.getElementById('subs-highlight-label');
      if (lab) lab.textContent = hlEl.value.toUpperCase();
    });
  } catch (_) {}
}

const _loadedFonts = new Set();

function ensureFontLoaded(family) {
  if (!family || _loadedFonts.has(family)) return;
  const safeFamily = family.replace(/[^A-Za-z0-9_-]/g, '_');
  const styleId = 'fontface_' + safeFamily;
  if (!document.getElementById(styleId)) {
    const url = '/api/subs/font-file/' + encodeURIComponent(family);
    const st = document.createElement('style');
    st.id = styleId;
    st.textContent = '@font-face { font-family: "' + family + '"; src: url("' + url + '") format("truetype"); font-display: swap; }';
    document.head.appendChild(st);
  }
  if (document.fonts && document.fonts.load) {
    document.fonts.load('40px "' + family + '"').catch(() => {});
  }
  _loadedFonts.add(family);
}

function updateFontPreview() {
  const sel = document.getElementById('subs-font');
  const preview = document.getElementById('subs-font-preview');
  const colorEl = document.getElementById('subs-color');
  if (!preview) return;
  const family = sel ? sel.value : 'ObelixPro';
  ensureFontLoaded(family);
  preview.style.fontFamily = '"' + family + '", sans-serif';
  if (colorEl) preview.style.color = colorEl.value;
}

function subsSelectFile(file) {
  _subsSelectedFile = file;
  const step1 = document.getElementById('subs-step1');
  const step2 = document.getElementById('subs-step2');
  const step3 = document.getElementById('subs-step3');
  const result = document.getElementById('subs-result');
  const preview = document.getElementById('subs-preview');
  const info = document.getElementById('subs-file-info');

  if (step1) step1.classList.add('hidden');
  if (step2) step2.classList.remove('hidden');
  if (step3) step3.classList.add('hidden');
  if (result) result.classList.add('hidden');
  if (preview) preview.src = URL.createObjectURL(file);
  if (info) info.textContent = `${file.name} · ${(file.size/1024/1024).toFixed(1)} MB`;
}

function subsReset() {
  _subsSelectedFile = null;
  _subsJobId = null;
  document.getElementById('subs-step1').classList.remove('hidden');
  document.getElementById('subs-step2').classList.add('hidden');
  document.getElementById('subs-step3').classList.add('hidden');
  document.getElementById('subs-result').classList.add('hidden');
  document.getElementById('subs-input').value = '';
}

async function subsElabora() {
  if (!_subsSelectedFile) { alert('carica un video prima'); return; }
  const btn = document.getElementById('btn-subs-elabora');
  const step2 = document.getElementById('subs-step2');
  const step3 = document.getElementById('subs-step3');
  const statusEl = document.getElementById('subs-status');
  const fill = document.getElementById('subs-progress-fill');
  const result = document.getElementById('subs-result');
  const videoRes = document.getElementById('subs-video-result');
  const dlEl = document.getElementById('subs-download');

  btn.disabled = true;
  if (step3) step3.classList.remove('hidden');
  if (result) result.classList.add('hidden');
  if (statusEl) statusEl.textContent = '⬆ upload…';
  if (fill) fill.style.width = '10%';

  const modeEl = document.getElementById('subs-karaoke-mode');
  const opts = {
    font_name: document.getElementById('subs-font').value,
    font_size: parseInt(document.getElementById('subs-size').value) || 62,
    primary_color_hex: document.getElementById('subs-color').value.replace('#',''),
    highlight_color_hex: document.getElementById('subs-highlight').value.replace('#',''),
    outline_width: parseInt(document.getElementById('subs-outline').value) || 5,
    margin_v: parseInt(document.getElementById('subs-margin').value) || 155,
    karaoke_mode: modeEl ? modeEl.value : 'phrase',
  };

  const fd = new FormData();
  fd.append('file', _subsSelectedFile);
  fd.append('options', JSON.stringify(opts));

  try {
    const r = await fetch('/api/subs/upload', { method: 'POST', body: fd });
    const d = await r.json();
    if (!r.ok) throw new Error(d.error || 'upload fallito');
    _subsJobId = d.job_id;
    if (statusEl) statusEl.textContent = `job #${d.job_id} · trascrizione…`;
    if (fill) fill.style.width = '30%';

    let poll = 0;
    const timer = setInterval(async () => {
      poll++;
      if (poll > 300) { clearInterval(timer); return; }
      try {
        const rr = await fetch('/api/subs/result/' + _subsJobId);
        const jj = await rr.json();
        if (jj.status === 'done') {
          clearInterval(timer);
          if (fill) fill.style.width = '100%';
          if (statusEl) statusEl.textContent = `✓ completato (${jj.result.size_mb || '?'} MB, ${jj.result.n_segments || '?'} frasi)`;
          if (result) result.classList.remove('hidden');
          if (videoRes) videoRes.src = jj.video_url + '?t=' + Date.now();
          if (dlEl) { dlEl.href = jj.video_url; dlEl.download = 'karaoke_' + _subsJobId + '.mp4'; }
          btn.disabled = false;
        } else if (jj.status === 'error') {
          clearInterval(timer);
          if (statusEl) statusEl.textContent = '❌ ' + (jj.error || 'errore');
          btn.disabled = false;
        } else {
          if (statusEl) statusEl.textContent = `job #${_subsJobId} · elaborazione…`;
          if (fill) fill.style.width = '60%';
        }
      } catch (_) {}
    }, 3000);
  } catch (e) {
    if (statusEl) statusEl.textContent = '❌ ' + e.message;
    btn.disabled = false;
  }
}


// === INIT: popola Apply al caricamento pagina ===
function initApplyTab() {
  try {
    // 1. Mostra il panel del tab attivo (rimuovi hidden)
    const activeTab = document.querySelector('.mode-tab.active');
    if (activeTab) {
      const mode = activeTab.dataset.mode;
      const panel = document.getElementById('mode-' + mode);
      if (panel) panel.classList.remove('hidden');
    } else {
      // fallback: mostra mode-apply
      const p = document.getElementById('mode-apply');
      if (p) p.classList.remove('hidden');
    }
    // 2. Popola i loader
    if (typeof loadRawList === 'function') loadRawList();
    if (typeof loadApplyProfiles === 'function') loadApplyProfiles();
    if (typeof loadCuratedInfo === 'function') loadCuratedInfo();
    console.log('✅ initApplyTab');
  } catch(e) { console.warn('initApplyTab err', e); }
}
if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', initApplyTab);
} else {
  initApplyTab();
}


// === STUDIO ===
let _studioEDL = null;
let _studioJobId = null;

async function studioLoad() {
  const info = document.getElementById('studio-info');
  const empty = document.getElementById('studio-empty');
  const content = document.getElementById('studio-content');
  if (info) info.textContent = 'caricamento…';
  try {
    const r = await fetch('/api/studio/edl');
    if (r.status === 404) {
      if (info) info.textContent = 'nessun EDL';
      if (empty) empty.style.display = 'block';
      if (content) content.style.display = 'none';
      return;
    }
    const d = await r.json();
    _studioEDL = d.edl;
    await studioLoadRawDurations();
    await studioLoadFilmstrips();
    renderStudioEDL();
    if (empty) empty.style.display = 'none';
    if (content) content.style.display = 'block';
  } catch (e) {
    if (info) info.textContent = '⚠ ' + e.message;
  }
}

function renderStudioEDL() {
  const e = _studioEDL;
  if (!e) return;
  const t = document.getElementById('studio-title');
  const m = document.getElementById('studio-meta');
  const info = document.getElementById('studio-info');
  if (t) t.textContent = e.title || 'EDL';
  if (m) m.textContent = `${e.n_clips} shot · ${e.total_duration}s · engine: ${e.engine || '?'}`;
  if (info) info.textContent = `${e.n_clips} shot`;

  const list = document.getElementById('studio-shot-list');
  if (!list) return;
  list.innerHTML = '';

  (e.timeline || []).forEach((s, i) => {
    const row = document.createElement('div');
    row.style.cssText = 'display:flex; gap:10px; padding:10px; border-bottom:1px solid #1a1f2c; align-items:flex-start';
    const kf = (s.keyframes && s.keyframes[0]) || '';
    const kfUrl = kf ? ('/api/studio/keyframe?path=' + encodeURIComponent(kf)) : '';

    // Costruisci HTML
    row.innerHTML = `
      <div style="font-size:12px; color:#8a92a6; min-width:26px; padding-top:6px; font-weight:600">${i+1}</div>
      <div style="width:70px; height:94px; background:#222; border-radius:4px; flex-shrink:0; ${kfUrl ? `background-image:url('${kfUrl}'); background-size:cover; background-position:center` : ''}"></div>
      <div style="flex:1; min-width:0">
        <div style="font-size:11px; color:#8a92a6; margin-bottom:3px">
          <b style="color:#b6bdcc">${s.role || '?'}</b> · ${s.duration}s · ${(s.clip_name || '').slice(0,22)}
        </div>
        <textarea class="studio-comment" data-idx="${i}" style="width:100%; background:#0b0d13; color:#e7e9ef; border:1px solid #232838; border-radius:5px; padding:6px 8px; font-size:12px; font-family:inherit; resize:vertical; min-height:38px" placeholder="Commento VO...">${(s.subtitle_text || '').replace(/</g, '&lt;')}</textarea>
        <div style="display:flex; align-items:center; gap:8px; margin-top:4px; font-size:11px; color:#8a92a6">
          <span class="voice-status" data-sid="${s.scene_id}">non registrata</span>
          <video class="voice-thumb-video" data-sid="${s.scene_id}" muted playsinline preload="metadata" src="/api/scene/${(typeof currentEdlJobId !== 'undefined' ? currentEdlJobId : (window.currentEdlJobId || 0))}/${s.scene_id}/clip.mp4" style="display:none"></video>
          <div class="voice-thumb-progress" data-sid="${s.scene_id}" style="display:none; width:0%"></div>
        </div>
        ${makeFilmstrip(s)}
        ${makeClipBar(s, i)}
        <div style="display:flex; gap:6px; margin-top:5px; align-items:center; font-size:11px; color:#8a92a6">
          <label style="display:flex; gap:3px; align-items:center">in <input type="number" class="studio-in" data-idx="${i}" value="${s.in_sec || 0}" step="0.1" min="0" style="width:60px; background:#0b0d13; color:#e7e9ef; border:1px solid #232838; border-radius:4px; padding:2px 4px; font-size:11px"></label>
          <label style="display:flex; gap:3px; align-items:center">out <input type="number" class="studio-out" data-idx="${i}" value="${s.out_sec || 0}" step="0.1" min="0" style="width:60px; background:#0b0d13; color:#e7e9ef; border:1px solid #232838; border-radius:4px; padding:2px 4px; font-size:11px"></label>
          <span style="color:#666">clip ${s.duration}s</span>
        </div>
      </div>
      <div style="display:flex; flex-direction:column; gap:3px; padding-top:4px">
        <button class="studio-voice-rec" data-sid="${s.scene_id}" title="Registra voce" style="background:#c9372a; border:0; color:#fff; width:32px; height:24px; border-radius:4px; cursor:pointer; font-size:11px">🎤</button>
        <button class="studio-voice-play" data-sid="${s.scene_id}" title="Riascolta" style="background:#1a1a1a; border:1px solid #2a2a2a; color:#fff; width:32px; height:24px; border-radius:4px; cursor:pointer; font-size:11px">▶</button>
        <button class="studio-voice-del" data-sid="${s.scene_id}" title="Cancella voce" style="background:#1a1a1a; border:1px solid #2a2a2a; color:#ff6b6b; width:32px; height:24px; border-radius:4px; cursor:pointer; font-size:11px">🗑</button>
        <button class="studio-act" data-act="up" data-idx="${i}" title="Sposta su" style="background:#1a1a1a; border:1px solid #2a2a2a; color:#fff; width:28px; height:24px; border-radius:4px; cursor:pointer">▲</button>
        <button class="studio-act" data-act="down" data-idx="${i}" title="Sposta giù" style="background:#1a1a1a; border:1px solid #2a2a2a; color:#fff; width:28px; height:24px; border-radius:4px; cursor:pointer">▼</button>
        <button class="studio-act" data-act="dup" data-idx="${i}" title="Duplica" style="background:#1a1a1a; border:1px solid #2a2a2a; color:#fff; width:28px; height:24px; border-radius:4px; cursor:pointer">⧉</button>
        <button class="studio-act" data-act="del" data-idx="${i}" title="Elimina" style="background:#3a1a1a; border:1px solid #5a2020; color:#ff6b6b; width:28px; height:24px; border-radius:4px; cursor:pointer">🗑</button>
      </div>
    `;
    list.appendChild(row);
  });

  // Bind textarea comment
  list.querySelectorAll('.studio-comment').forEach(ta => {
    ta.addEventListener('input', () => {
      const i = parseInt(ta.dataset.idx);
      _studioEDL.timeline[i].subtitle_text = ta.value;
    });
  });

  // Bind trim in/out
  list.querySelectorAll('.studio-in, .studio-out').forEach(inp => {
    inp.addEventListener('change', () => {
      const i = parseInt(inp.dataset.idx);
      const s = _studioEDL.timeline[i];
      let v = parseFloat(inp.value) || 0;
      if (inp.classList.contains('studio-in')) {
        s.in_sec = Math.max(0, v);
        if (s.out_sec <= s.in_sec) s.out_sec = s.in_sec + 0.5;
      } else {
        s.out_sec = Math.max(s.in_sec + 0.5, v);
      }
      s.duration = round2(s.out_sec - s.in_sec);
      inp.parentElement.parentElement.querySelector('span[style*="color:#666"]').textContent = 'clip ' + s.duration + 's';
    });
  });

  // Bind pulsanti voce
  list.querySelectorAll('.studio-voice-rec').forEach(btn => {
    btn.addEventListener('click', () => {
      if (window.voiceAPI) window.voiceAPI.toggleRecord(parseInt(btn.dataset.sid, 10), btn);
    });
  });
  list.querySelectorAll('.studio-voice-play').forEach(btn => {
    btn.addEventListener('click', () => {
      if (window.voiceAPI) window.voiceAPI.playRecording(parseInt(btn.dataset.sid, 10));
    });
  });
  list.querySelectorAll('.studio-voice-del').forEach(btn => {
    btn.addEventListener('click', async () => {
      const sid = parseInt(btn.dataset.sid, 10);
      if (!confirm('Cancellare la voce della Scena ' + (sid + 1) + '?')) return;
      try {
        const r = await fetch('/api/voice/delete', {
          method: 'POST', headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({ edl_job_id: window.currentEdlJobId, scene_id: sid })
        });
        if (!r.ok) throw new Error('errore ' + r.status);
        if (window.voiceAPI) window.voiceAPI.loadVoiceStatus().then(() => {
          // Aggiorna stato visivo
          document.querySelectorAll('.voice-status[data-sid="' + sid + '"]').forEach(el => {
            el.textContent = 'non registrata';
          });
        });
      } catch (e) { alert('Errore: ' + e.message); }
    });
  });

  // Aggiorna gli stati visivi (registrata / non)
  if (window.voiceAPI) {
    const rr = window.voiceAPI.getRecordingsRemote() || {};
    list.querySelectorAll('.voice-status').forEach(el => {
      const sid = el.dataset.sid;
      el.textContent = rr[sid] ? 'registrata ✓' : 'non registrata';
    });
  }

  // Bind azioni
  list.querySelectorAll('.studio-act').forEach(btn => {
    btn.addEventListener('click', () => {
      const i = parseInt(btn.dataset.idx);
      const act = btn.dataset.act;
      const tl = _studioEDL.timeline;
      if (act === 'up' && i > 0) {
        [tl[i-1], tl[i]] = [tl[i], tl[i-1]];
        renderStudioEDL();
      } else if (act === 'down' && i < tl.length - 1) {
        [tl[i], tl[i+1]] = [tl[i+1], tl[i]];
        renderStudioEDL();
      } else if (act === 'dup') {
        const copy = JSON.parse(JSON.stringify(tl[i]));
        tl.splice(i + 1, 0, copy);
        renderStudioEDL();
      } else if (act === 'del') {
        if (!confirm('Eliminare shot ' + (i+1) + '?')) return;
        tl.splice(i, 1);
        renderStudioEDL();
      }
      recalcStudioMeta();
    });
  });

  // Bind drag handles
  list.querySelectorAll('.clip-bar').forEach(bar => {
    bindBarDrag(bar);
  });
}

function round2(n) { return Math.round(n * 100) / 100; }

function recalcStudioMeta() {
  const e = _studioEDL;
  if (!e) return;
  e.n_clips = (e.timeline || []).length;
  e.total_duration = round2((e.timeline || []).reduce((a, s) => a + (s.duration || 0), 0));
  // Rigenera voiceover_script
  e.voiceover_script = (e.timeline || []).map(s => s.subtitle_text || '').join(' ').trim();
  const m = document.getElementById('studio-meta');
  if (m) m.textContent = `${e.n_clips} shot · ${e.total_duration}s · engine: ${e.engine || '?'}`;
  const info = document.getElementById('studio-info');
  if (info) info.textContent = `${e.n_clips} shot`;
}

async function studioSave() {
  if (!_studioEDL) { alert('nessun EDL'); return; }
  try {
    const r = await fetch('/api/studio/edl', {
      method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({edl: _studioEDL})
    });
    const d = await r.json();
    if (!r.ok) throw new Error(d.error || 'errore');
    alert('✓ EDL salvato');
  } catch (e) {
    alert('Errore: ' + e.message);
  }
}

async function studioLoadMusic() {
  try {
    const r = await fetch('/api/studio/music');
    const d = await r.json();
    const sel = document.getElementById('studio-music-select');
    if (!sel) return;
    sel.innerHTML = '<option value="">— nessuna —</option>';
    (d.tracks || []).forEach(t => {
      const opt = document.createElement('option');
      opt.value = t.name;
      opt.textContent = t.name + ' (' + t.size_mb + ' MB)';
      sel.appendChild(opt);
    });
  } catch (_) {}
}

async function studioUploadMusic(file) {
  const fd = new FormData();
  fd.append('file', file);
  try {
    const r = await fetch('/api/studio/music/upload', { method: 'POST', body: fd });
    const d = await r.json();
    if (!r.ok) throw new Error(d.error || 'errore');
    await studioLoadMusic();
    const sel = document.getElementById('studio-music-select');
    if (sel) sel.value = d.track.name;
  } catch (e) {
    alert('Errore: ' + e.message);
  }
}

async function studioDeleteMusic() {
  const sel = document.getElementById('studio-music-select');
  if (!sel || !sel.value) { alert('nessuna musica selezionata'); return; }
  if (!confirm('Eliminare ' + sel.value + '?')) return;
  try {
    await fetch('/api/studio/music/' + encodeURIComponent(sel.value), { method: 'DELETE' });
    await studioLoadMusic();
  } catch (e) {
    alert('Errore: ' + e.message);
  }
}

function studioBind() {
  const reload = document.getElementById('btn-studio-reload');
  if (reload) reload.addEventListener('click', studioLoad);
  const save = document.getElementById('btn-studio-save');
  if (save) save.addEventListener('click', studioSave);
  const up = document.getElementById('btn-studio-music-up');
  const fi = document.getElementById('studio-music-file');
  if (up && fi) {
    up.addEventListener('click', () => fi.click());
    fi.addEventListener('change', () => {
      if (fi.files.length) studioUploadMusic(fi.files[0]);
      fi.value = '';
    });
  }
  const del = document.getElementById('btn-studio-music-del');
  if (del) del.addEventListener('click', studioDeleteMusic);
  const loadBtn = document.getElementById('btn-studio-load-edl');
  const latestBtn = document.getElementById('btn-studio-load-latest');
  const edlSel = document.getElementById('studio-edl-select');
  if (loadBtn && edlSel) {
    loadBtn.addEventListener('click', () => studioLoadByName(edlSel.value));
  }
  if (latestBtn) {
    latestBtn.addEventListener('click', () => {
      // Prende il primo EDL della lista
      if (edlSel && edlSel.options.length > 1) {
        studioLoadByName(edlSel.options[1].value);
      } else {
        studioLoad();  // fallback
      }
    });
  }
  studioListEDLs();
  // Bind pulsante mix
  const btnMix = document.getElementById('btn-studio-mix');
  if (btnMix) btnMix.addEventListener('click', studioApplyMix);
  // Auto-carica l'ultimo EDL se disponibile (dopo 200ms per lasciar popolare la lista)
  setTimeout(() => {
    const sel = document.getElementById('studio-edl-select');
    if (sel && sel.options.length > 1 && !_studioEDL) {
      studioLoadByName(sel.options[1].value);
    }
  }, 300);
  const vv = document.getElementById('vol-voice');
  const vm = document.getElementById('vol-music');
  if (vv) vv.addEventListener('input', () => {
    const l = document.getElementById('vol-voice-label');
    if (l) l.textContent = vv.value + '%';
  });
  if (vm) vm.addEventListener('input', () => {
    const l = document.getElementById('vol-music-label');
    if (l) l.textContent = vm.value + '%';
  });
  studioLoadMusic();
}

// Init Studio
if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', studioBind);
} else {
  studioBind();
}


async function studioListEDLs() {
  try {
    const r = await fetch('/api/studio/edls');
    const d = await r.json();
    const sel = document.getElementById('studio-edl-select');
    if (!sel) return;
    sel.innerHTML = '<option value="">— seleziona —</option>';
    (d.edls || []).forEach(e => {
      const opt = document.createElement('option');
      opt.value = e.filename;
      const date = new Date(e.mtime * 1000);
      const ds = date.toLocaleString('it-IT', {day:'2-digit', month:'2-digit', hour:'2-digit', minute:'2-digit'});
      opt.textContent = `${e.filename.replace('job_', '').replace('_edl.json', '')} · ${e.n_clips || '?'} shot · ${ds}`;
      sel.appendChild(opt);
    });
  } catch (_) {}
}

async function studioLoadByName(filename) {
  if (!filename) { alert('seleziona un EDL'); return; }
  const info = document.getElementById('studio-info');
  if (info) info.textContent = 'caricamento…';
  try {
    const r = await fetch('/api/studio/edl/' + encodeURIComponent(filename));
    if (!r.ok) {
      const d = await r.json().catch(() => ({}));
      throw new Error(d.error || 'errore ' + r.status);
    }
    const d = await r.json();
    _studioEDL = d.edl;
    // Job id per la sezione voce (filename = job_999_edl.json)
    const mm = filename.match(/^job_(\d+)_edl\.json$/);
    if (mm) {
      _studioJobId = parseInt(mm[1], 10);
      if (window.voiceAPI) window.voiceAPI.setEdlJobId(_studioJobId);
      // Allinea il select al filename caricato (auto-load non lo aggiorna)
      const sel2 = document.getElementById('studio-edl-select');
      if (sel2) sel2.value = filename;
    }
    if (window.voiceAPI) { try { await window.voiceAPI.loadVoiceStatus(); } catch(_) {} }
    await studioLoadRawDurations();
    await studioLoadFilmstrips();
    renderStudioEDL();
    document.getElementById('studio-empty').style.display = 'none';
    document.getElementById('studio-content').style.display = 'block';
  } catch (e) {
    if (info) info.textContent = '⚠ ' + e.message;
    alert('Errore: ' + e.message);
  }
}


// === FILMSTRIP (frame previews) ===
let _filmstrips = {};

async function studioLoadFilmstrips() {
  if (!_studioEDL) return;
  const names = [...new Set((_studioEDL.timeline || []).map(s => s.clip_name).filter(Boolean))];
  for (const n of names) {
    if (_filmstrips[n]) continue;
    try {
      const r = await fetch('/api/studio/filmstrip', {
        method: 'POST', headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({clip_name: n, n: 8})
      });
      const d = await r.json();
      _filmstrips[n] = (d.frames || []).map(p =>
        '/api/studio/keyframe?path=' + encodeURIComponent(p)
      );
    } catch (_) {
      _filmstrips[n] = [];
    }
  }
}

function makeFilmstrip(s) {
  const urls = _filmstrips[s.clip_name] || [];
  if (!urls.length) return '';
  return '<div style="display:flex; height:40px; margin-top:4px; border-radius:4px; overflow:hidden; background:#000">' +
    urls.map(u => '<div style="flex:1; background:url(\'' + u + '\') center/cover; min-width:0"></div>').join('') +
    '</div>';
}


// === RAW DURATIONS (per barra trim) ===
let _rawDurations = {};

async function studioLoadRawDurations() {
  if (!_studioEDL) return;
  const names = [...new Set((_studioEDL.timeline || []).map(s => s.clip_name).filter(Boolean))];
  if (!names.length) return;
  try {
    const r = await fetch('/api/studio/raw_durations', {
      method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({names})
    });
    _rawDurations = await r.json();
  } catch (_) {}
}


// === CLIP BAR v2: handle con miniature inizio/fine ===
function makeClipBar(s, idx) {
  const rawDur = _rawDurations[s.clip_name] || 0;
  if (rawDur <= 0) return '';

  const inSec = parseFloat(s.in_sec || 0);
  const outSec = parseFloat(s.out_sec || 0);
  const inPct = Math.max(0, Math.min(100, (inSec / rawDur) * 100));
  const outPct = Math.max(0, Math.min(100, (outSec / rawDur) * 100));

  const inThumbUrl = '/api/studio/frame?clip_name=' + encodeURIComponent(s.clip_name) + '&t=' + inSec.toFixed(3);
  const outThumbUrl = '/api/studio/frame?clip_name=' + encodeURIComponent(s.clip_name) + '&t=' + outSec.toFixed(3);

  let step = 0.5;
  if (rawDur > 30) step = 5;
  else if (rawDur > 15) step = 2;
  else if (rawDur > 8) step = 1;
  else if (rawDur > 4) step = 0.5;
  else if (rawDur > 2) step = 0.25;
  else step = 0.1;

  const ticks = [];
  for (let t = 0; t <= rawDur + 1e-6; t += step) {
    ticks.push({pct: (t / rawDur) * 100, label: t.toFixed(2)});
  }
  if (!ticks.length || ticks[ticks.length - 1].pct < 99) {
    ticks.push({pct: 100, label: rawDur.toFixed(2)});
  }

  const tickLines = ticks.map(function(t) {
    return '<div style="position:absolute;left:' + t.pct + '%;top:0;bottom:0;width:1px;background:#3a4155;opacity:0.6;z-index:2;pointer-events:none"></div>';
  }).join('');

  const tickLabels = ticks.map(function(t) {
    return '<span style="position:absolute;left:' + t.pct + '%;transform:translateX(-50%);font-size:9px;color:#666;white-space:nowrap">' + t.label + 's</span>';
  }).join('');

  let h = '<div class="clip-bar" data-idx="' + idx + '" data-rawdur="' + rawDur + '" data-clipname="' + s.clip_name + '" style="margin-top:6px;user-select:none">';
  h += '<div class="bar-container" style="position:relative;height:50px;background:#0b0d13;border:1px solid #232838;border-radius:4px;touch-action:none;overflow:hidden">';
  // strip di sfondo con tint
  h += '<div class="bar-tint-left" style="position:absolute;left:0;top:0;bottom:0;width:' + inPct + '%;background:rgba(0,0,0,0.55);z-index:1;pointer-events:none"></div>';
  h += '<div class="bar-tint-right" style="position:absolute;left:' + outPct + '%;right:0;top:0;bottom:0;background:rgba(0,0,0,0.55);z-index:1;pointer-events:none"></div>';
  h += '<div class="bar-fill" style="position:absolute;left:' + inPct + '%;width:' + (outPct - inPct) + '%;top:0;bottom:0;background:rgba(45,108,223,0.25);z-index:0;pointer-events:none"></div>';
  h += tickLines;
  // HANDLE SINISTRO: miniatura del frame "in"
  h += '<div class="bar-handle bar-handle-in" data-idx="' + idx + '" style="position:absolute;left:' + inPct + '%;top:0;bottom:0;width:40px;margin-left:-40px;background-image:url(' + inThumbUrl + ');background-size:cover;background-position:center;border:2px solid #ffd400;border-right:0;border-radius:3px 0 0 3px;cursor:ew-resize;z-index:5;box-shadow:0 0 8px rgba(255,212,0,0.6)"></div>';
  // HANDLE DESTRO: miniatura del frame "out"
  h += '<div class="bar-handle bar-handle-out" data-idx="' + idx + '" style="position:absolute;left:' + outPct + '%;top:0;bottom:0;width:40px;background-image:url(' + outThumbUrl + ');background-size:cover;background-position:center;border:2px solid #ffd400;border-left:0;border-radius:0 3px 3px 0;cursor:ew-resize;z-index:5;box-shadow:0 0 8px rgba(255,212,0,0.6)"></div>';
  h += '</div>';
  h += '<div style="position:relative;height:12px;margin-top:2px">' + tickLabels + '</div>';
  h += '<div class="bar-labels" style="font-size:10px;color:#8a92a6;margin-top:3px">raw <b style="color:#b6bdcc">' + rawDur.toFixed(2) + 's</b> &middot; finestra <b style="color:#ffd400">' + (outSec - inSec).toFixed(2) + 's</b> &middot; in <b style="color:#b6bdcc">' + inSec.toFixed(2) + 's</b> &middot; out <b style="color:#b6bdcc">' + outSec.toFixed(2) + 's</b></div>';
  h += '</div>';

  return h;
}

// === BIND DRAG: handle in/out della barra trim ===
function bindBarDrag(barEl) {
  var idx = parseInt(barEl.dataset.idx);
  var rawDur = parseFloat(barEl.dataset.rawdur);
  var clipName = barEl.dataset.clipname || '';
  var container = barEl.querySelector('.bar-container');
  var fill = barEl.querySelector('.bar-fill');
  var tintL = barEl.querySelector('.bar-tint-left');
  var tintR = barEl.querySelector('.bar-tint-right');
  var inH = barEl.querySelector('.bar-handle-in');
  var outH = barEl.querySelector('.bar-handle-out');
  var labels = barEl.querySelector('.bar-labels');
  var row = barEl.closest('div[style*="border-bottom"]');
  var inInput = row ? row.querySelector('.studio-in') : null;
  var outInput = row ? row.querySelector('.studio-out') : null;

  var dragging = null;
  var containerW = 0;
  var startIn = 0, startOut = 0;
  var startX = 0;
  var lastThumbIn = null, lastThumbOut = null;
  var thumbTimer = null;

  function frameUrl(t) {
    var tk = Math.round(t * 20) / 20;
    return '/api/studio/frame?clip_name=' + encodeURIComponent(clipName) + '&t=' + tk.toFixed(3);
  }

  function updateThumbs(inSec, outSec) {
    if (!clipName) return;
    var uIn = frameUrl(inSec);
    var uOut = frameUrl(outSec);
    if (lastThumbIn !== uIn) {
      var im1 = new Image();
      im1.onload = function() {
        inH.style.backgroundImage = 'url(' + uIn + ')';
        lastThumbIn = uIn;
      };
      im1.src = uIn;
    }
    if (lastThumbOut !== uOut) {
      var im2 = new Image();
      im2.onload = function() {
        outH.style.backgroundImage = 'url(' + uOut + ')';
        lastThumbOut = uOut;
      };
      im2.src = uOut;
    }
  }

  function updateUI(inSec, outSec, skipThumbs) {
    var inPct = (inSec / rawDur) * 100;
    var outPct = (outSec / rawDur) * 100;
    fill.style.left = inPct + '%';
    fill.style.width = Math.max(0, outPct - inPct) + '%';
    tintL.style.width = inPct + '%';
    tintR.style.left = outPct + '%';
    inH.style.left = inPct + '%';
    outH.style.left = outPct + '%';
    if (labels) {
      labels.innerHTML = 'raw <b style="color:#b6bdcc">' + rawDur.toFixed(2) + 's</b>'
        + ' &middot; finestra <b style="color:#ffd400">' + (outSec - inSec).toFixed(2) + 's</b>'
        + ' &middot; in <b style="color:#b6bdcc">' + inSec.toFixed(2) + 's</b>'
        + ' &middot; out <b style="color:#b6bdcc">' + outSec.toFixed(2) + 's</b>';
    }
    if (inInput) inInput.value = inSec.toFixed(2);
    if (outInput) outInput.value = outSec.toFixed(2);
    if (!skipThumbs) updateThumbs(inSec, outSec);
  }

  function startDrag(which, ev) {
    ev.preventDefault();
    ev.stopPropagation();
    dragging = which;
    containerW = container.getBoundingClientRect().width;
    startX = ev.clientX;
    var s = _studioEDL.timeline[idx];
    startIn = parseFloat(s.in_sec) || 0;
    startOut = parseFloat(s.out_sec) || 0;
    document.body.style.cursor = 'ew-resize';
  }

  inH.addEventListener('pointerdown', function(e) { startDrag('in', e); });
  outH.addEventListener('pointerdown', function(e) { startDrag('out', e); });

  function onMove(ev) {
    if (!dragging) return;
    var dx = ev.clientX - startX;
    var dt = (dx / containerW) * rawDur;
    var s = _studioEDL.timeline[idx];
    if (dragging === 'in') {
      var newIn = startIn + dt;
      newIn = Math.max(0, Math.min(startOut - 0.3, newIn));
      s.in_sec = Math.round(newIn * 100) / 100;
    } else {
      var newOut = startOut + dt;
      newOut = Math.min(rawDur, Math.max(startIn + 0.3, newOut));
      s.out_sec = Math.round(newOut * 100) / 100;
    }
    s.duration = Math.round((s.out_sec - s.in_sec) * 100) / 100;

    if (thumbTimer) clearTimeout(thumbTimer);
    thumbTimer = setTimeout(function() {
      updateThumbs(s.in_sec, s.out_sec);
      thumbTimer = null;
    }, 150);

    updateUI(s.in_sec, s.out_sec, true);
  }

  function onUp() {
    if (!dragging) return;
    dragging = null;
    document.body.style.cursor = '';
    if (thumbTimer) { clearTimeout(thumbTimer); thumbTimer = null; }
    var s = _studioEDL.timeline[idx];
    updateThumbs(s.in_sec, s.out_sec);
    if (typeof recalcStudioMeta === 'function') recalcStudioMeta();
  }

  document.addEventListener('pointermove', onMove);
  document.addEventListener('pointerup', onUp);
  document.addEventListener('pointercancel', onUp);
}

// === STUDIO MIX (musica + volumi) ===
function studioMixRead() {
  const sel = document.getElementById('studio-music-select');
  const vv = document.getElementById('vol-voice');
  const vm = document.getElementById('vol-music');
  return {
    music: sel ? sel.value || null : null,
    voice_vol: vv ? (parseInt(vv.value) || 100) / 100.0 : 1.0,
    music_vol: vm ? (parseInt(vm.value) || 30) / 100.0 : 0.3,
  };
}

async function studioApplyMix() {
  if (!_studioEDL) { alert('nessun EDL caricato'); return; }
  const mix = studioMixRead();
  // Trova edl_job_id corrente
  const sel = document.getElementById('studio-edl-select');
  let jobId = _studioJobId;
  if (!jobId && sel && sel.value) {
    const m = sel.value.match(/^job_(\\d+)_edl\\.json$/);
    if (m) jobId = parseInt(m[1], 10);
  }
  if (!jobId) { alert('Nessun EDL caricato. Carica prima un EDL.'); return; }

  const btn = document.getElementById('btn-studio-mix');
  if (btn) { btn.disabled = true; btn.textContent = '⏳ Mix in corso…'; }
  try {
    const r = await fetch('/api/studio/apply_mix', {
      method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({ edl_job_id: jobId, ...mix })
    });
    const d = await r.json();
    if (!r.ok) throw new Error(d.error || 'errore');
    alert('✓ Mix completato\\n' + d.url);
    // Aggiorna anteprima render se presente
    const pv = document.getElementById('preview-video');
    if (pv) pv.src = d.url + '?t=' + Date.now();
  } catch (e) {
    alert('Errore: ' + e.message);
  } finally {
    if (btn) { btn.disabled = false; btn.textContent = '🎵 Applica mix finale'; }
  }
}
