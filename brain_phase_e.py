#!/usr/bin/env python3
"""Fase E: usa il brain per generare un EDL + render da un curated esistente.

Flusso:
1. Carica brain v0.1 + curated piu' recente (118 scene)
2. Claude Sonnet genera piano montaggio + VO in un'unica chiamata
3. Salva EDL come job_999_edl.json (il piu' recente per mtime)
4. Crea job in DB + chiama render_final
"""
import os, sys, json, time, glob, re
from pathlib import Path
from collections import Counter
from dotenv import load_dotenv

sys.path.insert(0, '/opt/reel-agent/app')
load_dotenv('/opt/reel-agent/.env')
from openai import OpenAI

BRAIN = Path('/opt/reel-agent/brain')
MEDIA = Path('/opt/reel-agent/media')
LOG = BRAIN / 'phase_e.log'

client = OpenAI(
    base_url='https://openrouter.ai/api/v1',
    api_key=os.environ['OPENROUTER_API_KEY'],
)
MODEL = 'anthropic/claude-sonnet-4.5'

TARGET_SHOTS = 30
TARGET_DUR = 65.0


def log(msg):
    line = f"[{time.strftime('%H:%M:%S')}] {msg}"
    print(line, flush=True)
    with open(LOG, 'a') as f:
        f.write(line + '\n')


def _load_settings():
    """Legge brain/settings.json. user_notes: lista di stringhe."""
    p = BRAIN / 'settings.json'
    if not p.exists():
        return {'user_notes': [], 'subtitle_style': {}}
    try:
        d = json.loads(p.read_text())
        # Retro-compat: se user_notes e' str, converti in lista
        un = d.get('user_notes', [])
        if isinstance(un, str):
            un = [ln.strip() for ln in un.split('\n') if ln.strip()]
        d['user_notes'] = un
        return d
    except Exception:
        return {'user_notes': [], 'subtitle_style': {}}


def brain_to_prompt(brain):
    """Trasforma il brain in un system prompt narrativo."""
    vm = brain['visual_memory']
    nm = brain['narrative_memory']
    lm = brain['lexical_memory']
    rm = brain['rhythmic_memory']
    ds = rm['dur_stats']
    settings = _load_settings()
    user_notes = settings.get('user_notes') or []
    if isinstance(user_notes, str):
        user_notes = [ln.strip() for ln in user_notes.split('\n') if ln.strip()]
    user_notes_block = ''
    if user_notes:
        notes_list = '\n'.join(f'{i+1}. {n}' for i, n in enumerate(user_notes))
        user_notes_block = f"""

# 🚨 REGOLE UTENTE HARD (PRIORITÀ MASSIMA — VINCONO SU TUTTO)

L'utente ha scritto queste regole. Devi INTERPRETARLE e APPLICARLE
attivamente alla generazione. Non sono suggerimenti: sono vincoli.

{notes_list}

**PROCEDURA OBBLIGATORIA** (segui in ordine):
1. Leggi ogni regola. Capisci cosa vieta o impone.
2. Quando scegli le scene della timeline, VERIFICA ATTIVAMENTE ogni regola:
   - Se una regola vieta un tipo di scena (es. "no smorfie"), guarda la
     descrizione della scena candidata. Se viola la regola, SCEGLI UN'ALTRA SCENA.
   - Se una regola impone una struttura (es. "apri con venue"), rispettala.
3. Quando scrivi il voiceover, VERIFICA ogni regola sul testo:
   - Se vieta una parola, NON usarla (cerca sinonimi).
   - Se impone un tono, applicalo.
4. PRIMA di restituire il JSON finale, fai un CHECK finale: ogni regola
   e' rispettata? Se NO, correggi e ricontrolla.

Le regole utente sono ASSOLUTE: se il brain dice X e una regola dice Y,
la regola VINCE.
"""

    return f"""Sei il clone digitale del creator TikTok @jay.emme. Devi montare e commentare un nuovo video ESATTAMENTE come farebbe lui. Non applicare regole generiche: ragiona come il creator.
{user_notes_block}

# CHI SEI (visual signature)
{vm.get('visual_signature', '')}

# COME APRI
{chr(10).join('- ' + r for r in vm.get('opening_rules', [])[:5])}

# COME CHIUDI
{chr(10).join('- ' + r for r in vm.get('closing_rules', [])[:5])}

# MOSSE VISIVE RICORRENTI
{chr(10).join('- ' + m for m in vm.get('signature_moves', [])[:8])}

# COME RACCONTI (narrative signature)
{nm.get('narrative_signature', '')}

# STRUTTURA NARRATIVA
Apertura: {chr(10).join('- ' + r for r in nm.get('opening_rules', [])[:4])}
Corpo: {chr(10).join('- ' + r for r in nm.get('body_structure', [])[:6])}
Chiusura: {chr(10).join('- ' + r for r in nm.get('closing_rules', [])[:4])}
Transizioni: {chr(10).join('- ' + r for r in nm.get('transitions_between_beats', [])[:5])}

# COME PARLI (linguaggio)
{lm.get('language_signature', '')}

PAROLE FIRMA (usale): {', '.join(lm.get('signature_words', [])[:15])}
PAROLE VIETATE (mai): {', '.join(lm.get('banned_words', [])[:10])}
Frasi di apertura tipiche: {chr(10).join('- ' + p for p in lm.get('opening_phrases', [])[:5])}
Frasi di chiusura tipiche: {chr(10).join('- ' + p for p in lm.get('closing_phrases', [])[:5])}
Pattern sintattici: {chr(10).join('- ' + p for p in lm.get('sentence_patterns', [])[:9])}
Tono: {lm.get('tone', '')}

# RITMO (durate)
Durata media shot: {ds['mean']}s (mediana {ds['median']}s, p10 {ds['p10']}s, p90 {ds['p90']}s)
Apertura media: {rm['dur_opening_mean']}s | Chiusura media: {rm['dur_closing_mean']}s
Mix: umano {rm['human_ratio']*100:.0f}%, food {rm['food_ratio']*100:.0f}%, venue {rm['venue_ratio']*100:.0f}%

# 🚫 NO AZIONI FISICHE (VIOLAZIONE = RISPOSTA SCARTATA)

Il creator NON racconta cosa fa la sua mano. NON descrive l'atto di mangiare
o di muoversi. Commenta il CIBO, la SALSA, la TEXTURE, il SAPORE, il LOCALE.

VERBI DI AZIONE VIETATI come soggetto principale della frase:
- "puccio" / "intingiamo" / "intingere" / "bagno nel" / "mi puccio"
- "prendo" / "ne prendo un" / "ne prendo ancora"
- "mordo" / "addento" / "assaggio" (come azione nuda)
- "provo" / "lo provo" (come azione nuda)
- "inizio" / "partiamo con" / "passiamo a"
- "mi metto a" / "mi faccio un"

REGOLA PRATICA:
Per ogni beat, chiediti: "sto raccontando l'AZIONE della mano o sto COMMENTANDO
il cibo/sapore/locale?". Se racconti l'azione, RIFORMULA.

TRASFORMAZIONI OBBLIGATORIE:

| ❌ azione fisica | ✅ commento |
|---|---|
| "Le puccio nella salsa bianca" | "La salsa bianca è la morte sua" |
| "Ovviamente le puccio nella salsa" | "Ovviamente con questa salsa è un altro livello" |
| "Ne prendo un bel po'" | "Guardate che porzioni ragazzi" |
| "Ne prendo ancora una" | "Non riesco a smettere" |
| "Le mordo subito" | "Che croccantezza assurda" |
| "Lo provo" | "Devo dire che è pazzesco" |
| "Passiamo alle ali glassate" | "E queste glassate ragazzi, roba da matti" |
| "Inizio dalle ali" | "Queste ali promettono benissimo" |
| "Provo anche la bevanda" | "E la bevanda ci sta tutta" |
| "Lo puccio nel formaggio" | "Guardate il formaggio che fila" |

**ECCEZIONE PERMESSA**: puoi usare "provo" o "assaggio" SOLO se IMMEDIATAMENTE
seguito da un giudizio: "provo... e sono clamorose". Mai "provo" da solo.

# 🚫 STRUTTURE DESCRITTIVE VIETATE (VIOLAZIONE = RISPOSTA SCARTATA)

Queste strutture sono VIETATE nel voiceover. Sono descrizioni travestite.
Se ti accorgi di averle scritte, RIFORMULA come commento.

VIETATE (❌):
- "il locale ha ..." / "il locale presenta ..."
- "c'è anche ..." / "c'è pure ..." / "ci sono ..."
- "c'è un/una ..." / "c'è il/la ..."
- "sul tavolo c'è ..." / "sul piatto c'è ..."
- "vediamo ..." / "si vede ..." / "notiamo ..."
- "X mostra Y" / "il monitor mostra ..." / "lo schermo mostra ..."
- "il posto ha ..." / "qui hanno ..."
- "sono presenti ..." / "troviamo ..."

Per ogni struttura vietata, sostituisci con COMMENTO:

| ❌ descrizione | ✅ commento |
|---|---|
| "il locale ha anche monitor pubblicitari" | "pure i monitor pubblicitari, che roba moderna" |
| "c'è anche la claw machine" | "hanno pure la claw machine, non ci credo" |
| "il tavolo ha diverse salse" | "guardate quante salse sul tavolo" |
| "lo schermo mostra il menu" | "ordinare da questi schermi è comodissimo" |
| "sul piatto ci sono le ali" | "che vassoio ragazzi, roba da matti" |

**TEST PRIMA DI SCRIVERE**: se la frase contiene "ha", "c'è", "ci sono", "mostra", "presenta"
come verbo principale, FERMATI. Non descrivere. Aggiungi:
- "guardate", "che", "roba da", "pazzesco", "non ci credo", "pure", "addirittura"

# COME SI COMMENTA (CRITICO — LEGGI CON ATTENZIONE)

Il creator NON descrive ciò che si vede. Il creator COMENTA con opinione.
Ogni frase del VO deve contenere un GIUDIZIO, un'EMOZIONE, un'OPINIONE.

## ESEMPI CHIARI:

DESCRIZIONE (❌ SBAGLIATO — non farlo mai):
- "Il locale ha anche monitor pubblicitari"
- "Ci sono dei chioschi self-service per ordinare"
- "Il tavolo ha diverse salse"
- "Il monitor mostra il menu"

COMMENTO (✅ GIUSTO — fai sempre così):
- "Il locale è davvero curato, hanno pensato a tutto"
- "Ordinare da soli è comodissimo, zero attese"
- "E niente, il tavolo è pieno di salse, roba da matti"
- "Guarda che belli questi schermi, super moderni"

## REGOLA DEL LOCALE (SPECIALE)

Almeno UNA volta nel video, devi COMMENTARE IL LOCALE con un'opinione.
Non basta dire "siamo da X" o "il locale ha Y" — devi esprimere un GIUDIZIO:
- "Il locale è bellissimo, super curato"
- "L'ambiente è proprio figo, ci tornerò"
- "Atmosfera top, sembra di stare a New York"
- "Il posto è enorme, non me l'aspettavo"
- "Location pazzesca, da portarci gli amici"

Quando vedi uno shot venue_* nella timeline, chiediti: "cosa penserebbe il creator
di questo posto?" e scrivi QUELLO, non "il locale ha X".

## REGOLA GENERALE

Per OGNI beat, chiediti: "sto descrivendo o sto commentando?"
Se descrivi, RIFORMULA in commento. Aggiungi:
- aggettivi di opinione (pazzesco, clamoroso, curato, top, assurdo, incredibile)
- reazione personale (non me l'aspettavo, ci tornerò, roba da matti)
- confronto (meglio di X, come a Y)
- invito (dovete provarlo, guardate qua)

# REGOLE ANTI-SMORFIA (ULTIMI 3 SHOT)
Gli ULTIMI 3 beat della timeline NON devono MAI mostrare:
- espressioni concentrate, occhi chiusi, bocca aperta, testa china, smorfie
- persone che mangiano con sguardo assente o arrabbiato
Se l'elenco "scene disponibili" ha soggetti con descrizioni tipo "concentrazione",
"occhi chiusi", "testa china", "espressione concentrata", SCEGLI UN'ALTRA SCENA
anche se il ruolo è meno adatto. Preferisci:
- volto sorridente
- volto neutro
- inquadrature del locale (venue) come penultimo e terzultimo shot
- food_closeup come chiusura neutra
La CTA finale DEVE essere un volto sorridente o un'inquadratura neutra.

# REGOLE HARD UTENTE (VINCONO SU QUALUNQUE PATTERN STATISTICO)
0. USA SOLO verbi colloquiali e semplici. VIETATI: intingere, intingo, intinta, adagiare,
   assaporare, degustare, deliziare, gustare, sorseggiare, addentare, divorare, apprezzare.
   Invece di "intingere" usa "pucciare" o "bagnare" o "prendere con la salsa".
   Invece di "assaporare" usa "provare". Invece di "addentare" usa "mordere".
1. Il PRIMO shot DEVE essere `venue_interior_wide` o `venue_interior_detail` (mostra il locale prima di tutto)
2. Il SECONDO shot DEVE essere venue o person_talking (ancora contesto prima di mostrare cibo)
3. Almeno 6 dei 30 shot DEVONO essere `venue_*` (20% minimo, più della media reale)
4. NESSUN ruolo ripetuto 2 volte di fila
5. La CHIUSURA (ultimo shot) DEVE essere `person_talking` o `venue_*` — NON food
6. Il voiceover dell'ULTIMO shot DEVE essere UNA frase breve (max 8 parole) di CTA
7. LA MACCHINA BEVANDE (dispenser/Freestyle) e la CLAW MACHINE possono apparire
   MASSIMO 1 VOLTA CIASCUNA nel video. Non menzionarle mai piu' di una volta
   nel voiceover. NON menzionarle nella CTA finale. NON sono il focus del video.
8. Il focus del video e' IL CIBO (ali, patatine, nugget, pane, salse). Le macchine
   sono contorno, non protagoniste. Menzionale solo se compaiono come scena.
"""


def scene_to_line(idx, s):
    desc = (s.get('description') or '')[:80]
    subj = (s.get('subject') or '')[:40]
    role = s.get('role', '?')
    dur = s.get('duration', 0)
    return f"[{idx}] {role} | subj={subj} | dur={dur:.2f}s | {desc}"


def build_user_prompt(scenes):
    lines = [scene_to_line(i, s) for i, s in enumerate(scenes)]
    return f"""# MATERIALE DISPONIBILE ({len(scenes)} scene raw)

{chr(10).join(lines)}

# COMPITO
Costruisci il video finale come lo monterebbe @jay.emme:
- Scegli ESATTAMENTE {TARGET_SHOTS} scene (dall'elenco sopra) nell'ordine in cui vanno montate
- Durata totale target: {TARGET_DUR}s (accetta 60-70s)

# REGOLE SUI NOMI (CRITICHE)
Il campo `subj=` di ogni scena e' la VERITA' su cosa si vede.
Devi COPIARE VERBATIM il `subj=` nel voiceover. NON parafrasare. NON tradurre.
- Se `subj=nugget, crocchetta` -> scrivi "nugget", NON "pane", NON "crocchetta di pollo"
- Se `subj=pane, focaccia` -> scrivi "pane" o "focaccia", NON "nugget"
- Se non sai cos'e', scrivi "questo" o usa il termine generico "piatto"
NON inventare nomi di pietanze. Se il subj dice X, il voiceover dice X.

# REGOLE STRUTTURA
- Apri con venue (vedi REGOLE HARD UTENTE sopra)
- Almeno 6 shot venue_* su 30
- Chiudi con person_talking o venue
- Per ogni beat, scrivi il voiceover di UNA frase corta (max 12 parole)
- La frase dell'ULTIMO shot: max 8 parole, deve essere CTA
- NON ripetere lo stesso ruolo 2 volte di fila
- Alterna umano/food/venue

# OUTPUT (JSON)
{{
  "title": "titolo breve",
  "timeline": [
    {{"scene_idx": 0, "duration": 2.5, "beat_type": "hook", "subtitle_text": "frase che dice"}},
    ...
  ],
  "voiceover_script": "testo completo concatenato"
}}

Il campo `scene_idx` DEVE essere un indice valido (0-{len(scenes)-1}).
Rispondi SOLO con JSON valido, niente markdown.
"""


def claude_call(system, user, max_tokens=6000):
    try:
        r = client.chat.completions.create(
            model=MODEL,
            messages=[
                {'role': 'system', 'content': system},
                {'role': 'user', 'content': user},
            ],
            max_tokens=max_tokens,
            temperature=0.4,
        )
        return r.choices[0].message.content
    except Exception as e:
        log(f"Claude error: {type(e).__name__}: {str(e)[:200]}")
        return None


def parse_json(txt):
    t = (txt or '').strip()
    if t.startswith('```'):
        t = t.split('\n', 1)[1]
        if t.endswith('```'):
            t = t[:-3]
    t = t.strip()
    i, j = t.find('{'), t.rfind('}')
    if i >= 0 and j > i:
        t = t[i:j+1]
    return json.loads(t)


def build_edl(plan, scenes, profile_id=16, venue_name="Wing Stop Milano"):
    """Costruisce l'EDL finale compatibile con render_final."""
    timeline = []
    for i, beat in enumerate(plan.get('timeline', [])):
        si = beat.get('scene_idx', -1)
        if not isinstance(si, int) or si < 0 or si >= len(scenes):
            log(f"  beat {i}: scene_idx non valido {si}, salto")
            continue
        s = scenes[si]
        s_start = float(s.get('start', 0))
        s_end = float(s.get('end', 0))
        s_dur = max(0.5, s_end - s_start)

        req_dur = float(beat.get('duration', 2.5))
        act_dur = max(1.5, min(req_dur, s_dur))

        mid = (s_start + s_end) / 2.0
        in_sec = max(s_start, mid - act_dur / 2.0)
        out_sec = min(s_end, in_sec + act_dur)
        if out_sec - in_sec < act_dur:
            in_sec = max(s_start, out_sec - act_dur)
        act_dur = out_sec - in_sec

        timeline.append({
            'scene_id': s.get('id'),
            'clip_name': s.get('clip_name', ''),
            'clip_path': s.get('clip_path', ''),
            'role': s.get('role', ''),
            'subject': s.get('subject', ''),
            'description': s.get('description', ''),
            'keyframes': s.get('keyframes', []),
            'start': s_start,
            'end': s_end,
            'in_sec': round(in_sec, 3),
            'out_sec': round(out_sec, 3),
            'duration': round(act_dur, 2),
            'requested_duration': req_dur,
            'beat_type': beat.get('beat_type', ''),
            'beat_moment': beat.get('subtitle_text', '')[:60],
            'subtitle_text': beat.get('subtitle_text', '').strip(),
        })

    # E3: estendi l'ultimo shot se la frase CTA e' lunga (>5 parole)
    if timeline:
        last = timeline[-1]
        last_words = len((last.get('subtitle_text') or '').split())
        if last_words > 5 and last['duration'] < 5.0:
            # +1.0s minimo garantito (dà respiro visivo alla chiusura)
            extra = max(1.0, min(1.8, last_words * 0.18))
            new_dur = min(5.0, last['duration'] + extra)
            # ricomputa in_sec/out_sec dal centro della scena
            scene = scenes[plan['timeline'][-1]['scene_idx']]
            s_start = float(scene.get('start', 0))
            s_end = float(scene.get('end', 0))
            if s_end - s_start >= new_dur:
                mid = (s_start + s_end) / 2.0
                new_in = max(s_start, mid - new_dur / 2.0)
                new_out = min(s_end, new_in + new_dur)
                last['in_sec'] = round(new_in, 3)
                last['out_sec'] = round(new_out, 3)
                last['duration'] = round(new_out - new_in, 2)
                log(f"  E3: ultimo shot esteso a {last['duration']}s per CTA ({last_words} parole)")

    return {
        'title': plan.get('title', 'Brain-generated'),
        'voiceover_script': plan.get('voiceover_script', ' '.join(b.get('subtitle_text', '') for b in timeline)),
        'timeline': timeline,
        'total_duration': round(sum(t['duration'] for t in timeline), 2),
        'n_clips': len(timeline),
        'profile_id': profile_id,
        'venue_name': venue_name,
    }


def main():
    log("=== Fase E START ===")
    t0 = time.time()

    # 1. Carica brain (active_brain.json = standard nuovo, fallback legacy)
    if (BRAIN / 'active_brain.json').exists():
        brain = json.load(open(BRAIN / 'active_brain.json'))
        log(f"[1] brain: active_brain.json (schema {brain.get('schema_version')})")
    else:
        brain = json.load(open(BRAIN / 'creator_brain_v0.1.json'))
        log(f"[1] brain caricato legacy: {brain.get('source', '?')}")

    # 2. Carica curated
    curated_files = sorted(glob.glob(str(MEDIA / 'curated' / 'job_*_curated.json')),
                           key=os.path.getmtime, reverse=True)
    if not curated_files:
        log("ERRORE: nessun curated trovato")
        return
    cur = json.load(open(curated_files[0]))
    log(f"[2] curated: {curated_files[0]}")
    scenes = [s for s in cur.get('scenes', []) if s.get('decision') == 'keep']
    log(f"    {len(scenes)} scene keep")
    if len(scenes) < 10:
        log("ERRORE: troppe poche scene")
        return

    # 3. Claude
    log("[3] chiamata Claude Sonnet...")
    sys_prompt = brain_to_prompt(brain)
    user_prompt = build_user_prompt(scenes)
    log(f"    system prompt: {len(sys_prompt)} char")
    log(f"    user prompt:   {len(user_prompt)} char")

    txt = claude_call(sys_prompt, user_prompt, max_tokens=7000)
    if not txt:
        log("ERRORE: Claude non ha risposto")
        return

    try:
        plan = parse_json(txt)
    except Exception as e:
        log(f"ERRORE parse JSON: {e}")
        log(f"raw (primi 500): {txt[:500]}")
        return
    log(f"    plan: {len(plan.get('timeline', []))} beat, titolo='{plan.get('title', '')[:50]}'")

    # 4. Costruisci EDL
    log("[4] costruzione EDL...")
    edl = build_edl(plan, scenes)
    log(f"    timeline: {edl['n_clips']} clip, {edl['total_duration']}s")

    # 4b. Post-process: blocca se macchina bibite/claw sono troppo presenti
    log("[4b] check macchina bibite/claw...")
    keywords_dispenser = ['dispenser', 'freestyle', 'macchina per le bevande',
                          'macchina delle bibite', 'macchina bibite', 'coca freestyle']
    keywords_claw = ['claw', 'macchina gioco', 'macchina del gioco']

    def count_kw_in_text(txt, kws):
        t = (txt or '').lower()
        return sum(t.count(k) for k in kws)

    full_vo = edl.get('voiceover_script', '').lower()
    n_disp_vo = count_kw_in_text(full_vo, keywords_dispenser)
    n_claw_vo = count_kw_in_text(full_vo, keywords_claw)
    log(f"    VO menziona: dispenser={n_disp_vo}x, claw={n_claw_vo}x")

    # Se troppo, rimuovi beat extra che li menzionano (mantieni il primo)
    if n_disp_vo > 1 or n_claw_vo > 1:
        seen_disp = False
        seen_claw = False
        new_timeline = []
        for t in edl['timeline']:
            subj = ((t.get('subject') or '') + ' ' + (t.get('subtitle_text') or '') + ' ' + (t.get('description') or '')).lower()
            has_disp = any(k in subj for k in keywords_dispenser)
            has_claw = any(k in subj for k in keywords_claw)
            if has_disp and seen_disp:
                log(f"    skip beat (dispenser duplicato): {t.get('subtitle_text','')[:50]}")
                continue
            if has_claw and seen_claw:
                log(f"    skip beat (claw duplicato): {t.get('subtitle_text','')[:50]}")
                continue
            if has_disp: seen_disp = True
            if has_claw: seen_claw = True
            new_timeline.append(t)
        edl['timeline'] = new_timeline
        edl['n_clips'] = len(new_timeline)
        edl['total_duration'] = round(sum(t['duration'] for t in new_timeline), 2)
        edl['voiceover_script'] = ' '.join(t.get('subtitle_text', '') for t in new_timeline)
        log(f"    timeline ridotta a {edl['n_clips']} clip, {edl['total_duration']}s")

    # 5. Salva (job_id=999, diventa il piu' recente per mtime)
    edl_path = MEDIA / 'edl' / 'job_999_edl.json'
    edl_path.write_text(json.dumps(edl, indent=2, ensure_ascii=False))
    log(f"[5] EDL salvato: {edl_path}")

    # 6. Crea job in DB + lancia render_final
    log("[6] lancio render_final...")
    from app.jobs import manager
    from app.workers.render_final import render_final as rf

    job_id = manager.start_job(
        job_type='render_final',
        payload={'profile_id': 16, 'source': 'brain_phase_e'},
        fn=rf,
        profile_id=16,
    )
    log(f"    render job_id={job_id}")
    log(f"=== Fase E DONE in {time.time()-t0:.0f}s ===")
    log(f"    Guarda: media/renders/render_999.mp4 (o _998 se job_id precedente)")
    log(f"    Attendi 1-2 min per completamento render")


if __name__ == '__main__':
    main()
