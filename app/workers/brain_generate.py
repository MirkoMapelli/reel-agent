"""Wrapper server-friendly per il Creator Brain.
Espone run_brain_e(job_id, profile_id, venue_name) chiamabile dal job manager.
Importa le funzioni pure da brain_phase_e per non duplicare la logica.
"""
import os, sys, json, time, glob
from pathlib import Path

BASE = Path('/opt/reel-agent')
BRAIN = BASE / 'brain'
MEDIA = BASE / 'media'

sys.path.insert(0, str(BASE))
sys.path.insert(0, str(BASE / 'app'))

import brain_phase_e as bpe
from app.jobs import manager


def _rewrite_descriptive_phrases(vo):
    """Riscrive in-place le strutture descrittive residue.
    Applica regex di sostituzione su frase intera (non perfetto ma efficace).
    """
    import re
    replacements = [
        # "c'è anche X" -> "pure X, guarda qua"
        (r"\bc'è anche (il|la|lo|i|gli|le|un|una|dei|delle)\s+([\wàèéìòù]+)",
         r"pure \1 \2, guarda qua"),
        (r"\bc'è anche\s+([\wàèéìòù]+)",
         r"pure \1, guarda qua"),
        # "c'è pure X" -> "pure X"
        (r"\bc'è pure\s+", r"pure "),
        # "ci sono X" -> "guarda quanti/che X"
        (r"\bci sono\s+(gli|le|i|dei|delle)\s+", r"guarda che "),
        # "il locale ha X" -> "hanno pure X"
        (r"\bil locale ha\s+", r"hanno pure "),
        (r"\bil posto ha\s+", r"hanno pure "),
        # "lo schermo mostra X" -> "guarda sullo schermo"
        (r"\blo schermo mostra\s+", r"guarda sullo schermo "),
        (r"\bil monitor mostra\s+", r"guarda sul monitor "),
        # "sul tavolo c'è X" -> "guardate sul tavolo"
        (r"\bsul tavolo c'è\s+", r"guardate sul tavolo "),
        (r"\bsul piatto c'è\s+", r"guardate nel piatto "),
    ]
    out = vo
    for pat, rep in replacements:
        out = re.sub(pat, rep, out, flags=re.IGNORECASE)
    return out


def _post_process(edl, job_id):
    """Blocca duplicati concettuali: dispenser, claw, ordinazione self-service."""
    kws_disp = ['dispenser', 'freestyle', 'macchina per le bevande',
                'macchina delle bibite', 'macchina bibite', 'coca freestyle']
    kws_claw = ['claw', 'macchina gioco', 'macchina del gioco']
    # Novita': concept "ordinazione/pagamento" — tutti sinonimi
    kws_ord = ['chiosc', 'chioschi', 'self-service', 'self service',
               'totem', 'schermo touch', 'schermi touch', 'touch per ordinare',
               'touchscreen', 'menu touch', 'ordinazione', 'ordinare al',
               'menu digitale', 'kiosk']

    def count_kw(txt, kws):
        t = (txt or '').lower()
        return sum(t.count(k) for k in kws)

    vo = edl.get('voiceover_script', '').lower()
    n_d = count_kw(vo, kws_disp)
    n_c = count_kw(vo, kws_claw)
    n_o = count_kw(vo, kws_ord)
    manager.log(job_id, f"VO menziona: dispenser={n_d}x, claw={n_c}x, ordinazione={n_o}x")

    if n_d > 1 or n_c > 1 or n_o > 1:
        seen_d = seen_c = seen_o = False
        new_tl = []
        for t in edl['timeline']:
            subj = ((t.get('subject') or '') + ' ' +
                    (t.get('subtitle_text') or '') + ' ' +
                    (t.get('description') or '')).lower()
            has_d = any(k in subj for k in kws_disp)
            has_c = any(k in subj for k in kws_claw)
            has_o = any(k in subj for k in kws_ord)
            if has_d and seen_d:
                manager.log(job_id, f"  skip dispenser dup: {t.get('subtitle_text','')[:50]}")
                continue
            if has_c and seen_c:
                manager.log(job_id, f"  skip claw dup: {t.get('subtitle_text','')[:50]}")
                continue
            if has_o and seen_o:
                manager.log(job_id, f"  skip ordinazione dup: {t.get('subtitle_text','')[:50]}")
                continue
            if has_d: seen_d = True
            if has_c: seen_c = True
            if has_o: seen_o = True
            new_tl.append(t)
        edl['timeline'] = new_tl
        edl['n_clips'] = len(new_tl)
        edl['total_duration'] = round(sum(t['duration'] for t in new_tl), 2)
        edl['voiceover_script'] = ' '.join(t.get('subtitle_text', '') for t in new_tl)
        manager.log(job_id, f"  timeline ridotta a {edl['n_clips']} clip, {edl['total_duration']}s")

    # Rewrite strutture descrittive residue (post-Claude)
    n_before = len(edl.get('voiceover_script', ''))
    new_vo = _rewrite_descriptive_phrases(edl.get('voiceover_script', ''))
    if new_vo != edl.get('voiceover_script', ''):
        # Applica anche al timeline per coerenza
        for t in edl['timeline']:
            if t.get('subtitle_text'):
                t['subtitle_text'] = _rewrite_descriptive_phrases(t['subtitle_text'])
        edl['voiceover_script'] = ' '.join(t.get('subtitle_text', '') for t in edl['timeline'])
        manager.log(job_id, f"  rewrite descrittivo: alcune frasi riscritte")


def _load_brain():
    """Legge active_brain.json (standard nuovo), fallback al legacy."""
    active = BRAIN / 'active_brain.json'
    if active.exists():
        return json.loads(active.read_text()), 'active_brain.json'
    legacy = BRAIN / 'creator_brain_v0.1.json'
    if legacy.exists():
        return json.loads(legacy.read_text()), 'creator_brain_v0.1.json (legacy)'
    archived = BRAIN / 'versions' / '_archive' / 'legacy_pre_corpus_v0.1.json'
    if archived.exists():
        return json.loads(archived.read_text()), 'legacy_pre_corpus (archived)'
    raise RuntimeError(f"Nessun brain trovato in {BRAIN}")


def run_brain_e(job_id, profile_id=16, venue_name="Wing Stop Milano"):
    """Genera EDL usando il brain. Chiamabile dal manager."""
    t0 = time.time()

    manager.log(job_id, "[1/4] carico brain...")
    try:
        brain, brain_file = _load_brain()
    except Exception as e:
        manager.fail(job_id, str(e))
        return

    schema = brain.get('schema_version', '?')
    src_info = brain.get('corpus') or brain.get('source') or '(no source info)'
    manager.log(job_id, f"      brain {brain_file} - schema {schema}")
    manager.log(job_id, f"      corpus/source: {src_info}")

    manager.update_progress(job_id, 15, "carico curated")
    cur_files = sorted(glob.glob(str(MEDIA / 'curated' / 'job_*_curated.json')),
                       key=os.path.getmtime, reverse=True)
    if not cur_files:
        manager.fail(job_id, "nessun curated trovato")
        return
    cur = json.loads(Path(cur_files[0]).read_text())
    scenes = [s for s in cur.get('scenes', []) if s.get('decision') == 'keep']
    manager.log(job_id, f"      curated: {Path(cur_files[0]).name} - {len(scenes)} scene keep")
    if len(scenes) < 10:
        manager.fail(job_id, f"troppe poche scene: {len(scenes)}")
        return

    manager.update_progress(job_id, 30, "chiamo Claude")
    sys_prompt = bpe.brain_to_prompt(brain)
    user_prompt = bpe.build_user_prompt(scenes)
    manager.log(job_id, f"      prompt: sys={len(sys_prompt)} user={len(user_prompt)} char")

    txt = bpe.claude_call(sys_prompt, user_prompt, max_tokens=7000)
    if not txt:
        manager.fail(job_id, "Claude non ha risposto")
        return

    manager.update_progress(job_id, 70, "parsing")
    try:
        plan = bpe.parse_json(txt)
    except Exception as e:
        manager.fail(job_id, f"parse JSON fallito: {e}")
        return
    n_beat = len(plan.get('timeline', []))
    manager.log(job_id, f"      plan: {n_beat} beat, titolo='{plan.get('title','')[:50]}'")

    manager.update_progress(job_id, 80, "costruisco EDL")
    edl = bpe.build_edl(plan, scenes, profile_id=profile_id, venue_name=venue_name)
    manager.log(job_id, f"      timeline: {edl['n_clips']} clip, {edl['total_duration']}s")

    _post_process(edl, job_id)

    edl_path = MEDIA / 'edl' / 'job_999_edl.json'
    edl_path.write_text(json.dumps(edl, indent=2, ensure_ascii=False))
    manager.log(job_id, f"      EDL salvato: {edl_path}")

    manager.update_progress(job_id, 100, "completato")
    manager.finish(job_id, {
        'edl_path': str(edl_path),
        'n_clips': edl['n_clips'],
        'duration': edl['total_duration'],
        'title': edl.get('title', ''),
        'engine': f'brain_{brain_file}',
        'schema_version': schema,
    })
