// === Health ===
async function refreshHealth() {
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

// === Step 8.1: mode switcher ===
document.querySelectorAll('.mode-tab').forEach(tab => {
  tab.addEventListener('click', () => {
    document.querySelectorAll('.mode-tab').forEach(t => t.classList.remove('active'));
    tab.classList.add('active');
    const mode = tab.dataset.mode;
    document.getElementById('mode-learn').classList.toggle('hidden', mode !== 'learn');
    document.getElementById('mode-apply').classList.toggle('hidden', mode !== 'apply');
    if (mode === 'apply') {
      loadRawList();
      loadApplyProfiles();
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
  const pid = sel ? sel.value : null;
  const msg = document.getElementById('apply-msg');
  if (!pid) { msg.textContent = '⚠ scegli un profilo prima'; return; }
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
    document.getElementById('render-script').textContent = d.voiceover_script || '(nessuno script)';
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
      const r = await fetch('/api/apply/generate', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ profile_id: pid })
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

// ============================================================
// ==== STEP 8.7: Script voce per scena + registrazione ======
// ============================================================
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
          <div class="voice-thumb"></div>
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
        const blob = new Blob(chunks, { type: AUDIO_MIME || 'audio/webm' });
        recordedBlobs[sid] = blob;
        // Upload
        const fd = new FormData();
        fd.append('audio', blob, `scene_${sid}.${AUDIO_EXT}`);
        fd.append('edl_job_id', currentEdlJobId);
        fd.append('scene_id', sid);
        status.textContent = 'carico…';
        status.className = 'voice-status';
        try {
          const r = await fetch('/api/voice/upload', { method: 'POST', body: fd });
          const d = await r.json();
          if (r.ok) {
            recordingsRemote[String(sid)] = true;
            status.textContent = `✓ registrata (${d.size_kb} KB)`;
            status.className = 'voice-status ok';
            btn.classList.remove('recording');
            btn.classList.add('has-rec');
            btn.textContent = '🔁 Riregistra';
            const card = btn.closest('.voice-card');
            if (card) card.classList.add('recorded');
            const playBtn = document.querySelector(`.btn-play[data-sid="${sid}"]`);
            if (playBtn) playBtn.disabled = false;
            updateSummary();
          } else {
            status.textContent = '✗ ' + (d.error || 'errore');
          }
        } catch (e) {
          status.textContent = '✗ ' + e.message;
        }
      };

      mediaRecorders[sid] = mr;
      mr.start();
      btn.classList.add('recording');
      btn.textContent = '⏹ Stop';
      status.textContent = '● REC…';
      status.className = 'voice-status rec';
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
})();
