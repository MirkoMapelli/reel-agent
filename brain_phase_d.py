#!/usr/bin/env python3
"""Fase D: sintesi creator_brain_v0.1.json.
Step D1: pre-aggregazione Python (gratis, istantanea)
Step D2: sintesi Claude via OpenRouter (~$0.50)
"""
import os, sys, json, time, glob, re
from pathlib import Path
from collections import Counter, defaultdict
from dotenv import load_dotenv

load_dotenv('/opt/reel-agent/.env')
from openai import OpenAI

BRAIN = Path('/opt/reel-agent/brain')
ANALYSES = BRAIN / 'reference_analyses'
LOG = BRAIN / 'phase_d.log'

client = OpenAI(
    base_url='https://openrouter.ai/api/v1',
    api_key=os.environ['OPENROUTER_API_KEY'],
)
MODEL = 'anthropic/claude-sonnet-4.5'


def log(msg):
    line = f"[{time.strftime('%H:%M:%S')}] {msg}"
    print(line, flush=True)
    with open(LOG, 'a') as f:
        f.write(line + '\n')


# ============================================================
# STEP D1 — Pre-aggregazione Python
# ============================================================

def load_all():
    vis_files = sorted(glob.glob(str(ANALYSES / '*_vision.json')))
    trs_files = sorted(glob.glob(str(ANALYSES / '*_transcript.json')))
    videos = {}
    for vf in vis_files:
        rid = Path(vf).stem.replace('_vision', '')
        videos.setdefault(rid, {})['vision'] = json.load(open(vf))
    for tf in trs_files:
        rid = Path(tf).stem.replace('_transcript', '')
        videos.setdefault(rid, {})['transcript'] = json.load(open(tf))
    return videos


def aggregate(videos):
    """Estrai statistiche aggregate da 95 video."""
    agg = {
        'n_videos': len(videos),
        'all_shots': [],           # flat list di shot con tutte le info
        'per_video': [],           # info per video
        'all_text': [],
        'openings': [],            # primi 3 shot di ogni video
        'closings': [],            # ultimi 3 shot
    }

    for rid, v in videos.items():
        vis = v.get('vision', {})
        trs = v.get('transcript', {})
        descs = {d['shot_idx']: d for d in vis.get('descriptions', [])}
        trs_map = {s['shot_idx']: s for s in trs.get('aligned_shots', [])}

        shots = []
        for idx in sorted(descs.keys()):
            d = descs[idx]
            t = trs_map.get(idx, {})
            shot = {
                'ref_id': rid,
                'idx': idx,
                'dur': d.get('dur', 0),
                'role': d.get('role_visivo', 'other'),
                'soggetto': d.get('soggetto', ''),
                'composizione': d.get('composizione', ''),
                'angolo': d.get('angolo', ''),
                'presenza_umana': d.get('presenza_umana', False),
                'presenza_logo': d.get('presenza_logo', False),
                'luce': d.get('luce', ''),
                'nota': d.get('cosa_si_nota', ''),
                'transcript': t.get('transcript', ''),
            }
            shots.append(shot)
            agg['all_shots'].append(shot)

        agg['per_video'].append({
            'ref_id': rid,
            'n_shots': len(shots),
            'duration': vis.get('total_dur', 0),
            'shots': shots,
        })
        agg['all_text'].append(trs.get('full_text', ''))

        if len(shots) >= 3:
            agg['openings'].append(shots[:3])
            agg['closings'].append(shots[-3:])

    return agg


def stats_from_agg(agg):
    """Calcola le statistiche numeriche dai dati aggregati."""
    shots = agg['all_shots']

    roles = Counter(s['role'] for s in shots)
    compositions = Counter(s['composizione'] for s in shots)
    angoli = Counter(s['angolo'] for s in shots)
    luci = Counter(s['luce'] for s in shots)

    durs = [s['dur'] for s in shots if s['dur'] > 0]
    durs_sorted = sorted(durs)
    n = len(durs_sorted)
    dur_stats = {
        'count': n,
        'mean': round(sum(durs) / n, 2) if n else 0,
        'median': round(durs_sorted[n // 2], 2) if n else 0,
        'p10': round(durs_sorted[int(n * 0.1)], 2) if n else 0,
        'p90': round(durs_sorted[int(n * 0.9)], 2) if n else 0,
        'min': round(durs_sorted[0], 2) if n else 0,
        'max': round(durs_sorted[-1], 2) if n else 0,
    }

    # Durate per fase (apertura, corpo, chiusura)
    opening_durs = [s['dur'] for shots_ in agg['openings'] for s in shots_ if s['dur'] > 0]
    closing_durs = [s['dur'] for shots_ in agg['closings'] for s in shots_ if s['dur'] > 0]

    # Umano vs food vs venue
    human_shots = sum(1 for s in shots if s['presenza_umana'])
    logo_shots = sum(1 for s in shots if s['presenza_logo'])
    food_shots = sum(1 for s in shots if s['role'].startswith('food_') or s['role'].startswith('drink_'))
    venue_shots = sum(1 for s in shots if s['role'].startswith('venue_'))

    # N-grammi dal testo
    full_text = ' '.join(agg['all_text']).lower()
    words = re.findall(r"\b[a-zàèéìòù']{3,}\b", full_text)
    stopwords = {'che', 'con', 'per', 'una', 'uno', 'del', 'della', 'dello', 'dei',
                 'delle', 'degli', 'dai', 'dalle', 'dagli', 'nel', 'nella', 'nello',
                 'nei', 'nelle', 'negli', 'dal', 'dalla', 'dallo', 'gli', 'sono',
                 'questo', 'questa', 'quello', 'quella', 'anche', 'più', 'molto',
                 'poco', 'tanto', 'solo', 'già', 'ancora', 'sempre', 'poi', 'prima',
                 'dopo', 'qui', 'qua', 'lì', 'là', 'come', 'dove', 'quando', 'così',
                 'essere', 'avere', 'fare', 'dire', 'andare', 'venire', 'tipo',
                 'cosa', 'roba', 'qui', 'qua', 'ecco', 'okay'}
    words_clean = [w for w in words if w not in stopwords]
    top_words = Counter(words_clean).most_common(80)

    # Bigrammi
    all_words = re.findall(r"\b[a-zàèéìòù']{2,}\b", full_text)
    bigrams = Counter(zip(all_words, all_words[1:]))
    bigrams = [(' '.join(k), v) for k, v in bigrams.most_common(50) if v >= 5]

    # Trigrammi
    trigrams = Counter(zip(all_words, all_words[1:], all_words[2:]))
    trigrams = [(' '.join(k), v) for k, v in trigrams.most_common(30) if v >= 3]

    return {
        'roles_dist': dict(roles.most_common()),
        'compositions_dist': dict(compositions.most_common()),
        'angoli_dist': dict(angoli.most_common()),
        'luci_dist': dict(luci.most_common()),
        'dur_stats': dur_stats,
        'dur_opening_mean': round(sum(opening_durs) / len(opening_durs), 2) if opening_durs else 0,
        'dur_closing_mean': round(sum(closing_durs) / len(closing_durs), 2) if closing_durs else 0,
        'human_ratio': round(human_shots / len(shots), 3),
        'logo_ratio': round(logo_shots / len(shots), 3),
        'food_ratio': round(food_shots / len(shots), 3),
        'venue_ratio': round(venue_shots / len(shots), 3),
        'top_words': top_words,
        'top_bigrams': bigrams,
        'top_trigrams': trigrams,
    }


# ============================================================
# STEP D2 — Sintesi Claude
# ============================================================

def claude_call(system_prompt, user_prompt, max_tokens=4000):
    """Una chiamata a Claude via OpenRouter. Ritorna testo."""
    try:
        r = client.chat.completions.create(
            model=MODEL,
            messages=[
                {'role': 'system', 'content': system_prompt},
                {'role': 'user', 'content': user_prompt},
            ],
            max_tokens=max_tokens,
            temperature=0.3,
        )
        return r.choices[0].message.content
    except Exception as e:
        log(f"  Claude error: {type(e).__name__}: {str(e)[:200]}")
        return None


def synth_visual(agg, stats):
    """Sintesi strato visivo."""
    samples = []
    for v in agg['per_video'][:30]:
        for s in v['shots'][:3]:
            samples.append(
                f"{s['role']} | {s['soggetto']} | {s['composizione']}/{s['angolo']} | "
                f"umano={s['presenza_umana']} logo={s['presenza_logo']} | {s['nota'][:80]}"
            )
    sample_text = '\n'.join(samples[:90])

    open_first = '\n'.join(
        f"VIDEO {i+1}: " + ' → '.join(f"{s['role']}({s['dur']:.1f}s)" for s in sh)
        for i, sh in enumerate(agg['openings'][:30])
    )
    close_last = '\n'.join(
        f"VIDEO {i+1}: " + ' → '.join(f"{s['role']}({s['dur']:.1f}s)" for s in sh)
        for i, sh in enumerate(agg['closings'][:30])
    )

    prompt = f"""Sei un analista di stile visivo. Analizza i pattern visivi del creator TikTok @jay.emme.

## STATISTICHE VISIVE
- Distribuzione ruoli: {json.dumps(stats['roles_dist'], indent=2)}
- Composizioni: {stats['compositions_dist']}
- Angoli: {stats['angoli_dist']}
- Luci: {stats['luci_dist']}
- Ratio umano: {stats['human_ratio']} | logo: {stats['logo_ratio']} | food: {stats['food_ratio']} | venue: {stats['venue_ratio']}

## CAMPIONE DI SHOT (90 esempi)
{sample_text}

## APERTURE (primi 3 shot di 30 video)
{open_first}

## CHIUSURE (ultimi 3 shot di 30 video)
{close_last}

## COMPITO
Estrai la "grammatica visiva" del creator. Restituisci JSON con questi campi:

{{
  "visual_signature": "<1-2 frasi: cosa rende RICONOSCIBILE il suo stile visivo>",
  "opening_rules": ["<regola 1>", "<regola 2>", ...],
  "closing_rules": ["<regola 1>", ...],
  "food_vocabulary": ["<come inquadra il cibo, es. 'close-up dal centro, salsa visibile'>", ...],
  "human_vocabulary": ["<come inquadra le persone>", ...],
  "venue_vocabulary": ["<come inquadra i locali>", ...],
  "signature_moves": ["<mosse visive ricorrenti, es. 'primo piano mani che intingono'>", ...]
}}

Max 8 regole per lista. Sii SPECIFICO e DESCRITTIVO, non generico.
Rispondi SOLO con JSON valido.
"""

    txt = claude_call("Sei un analista di stile visivo di creator TikTok.", prompt, 4000)
    if not txt:
        return None
    try:
        t = txt.strip()
        if t.startswith('```'):
            t = t.split('\n', 1)[1]
            if t.endswith('```'):
                t = t[:-3]
        return json.loads(t)
    except Exception as e:
        log(f"  visual synth parse error: {e}")
        return {'_raw': txt[:2000]}


def synth_narrative(agg, stats):
    """Sintesi strato narrativo."""
    full_texts = agg['all_text'][:20]
    text_sample = '\n\n---\n\n'.join(t[:800] for t in full_texts if t)

    # pattern di lunghezza
    lengths = [v['n_shots'] for v in agg['per_video']]
    lengths_sorted = sorted(lengths)

    prompt = f"""Sei un analista narrativo. Analizza come il creator TikTok @jay.emme struttura i suoi video.

## NUMERI
- Video analizzati: {len(agg['per_video'])}
- Shot per video: mediana {lengths_sorted[len(lengths_sorted)//2]}, min {min(lengths)}, max {max(lengths)}
- Durata media shot: {stats['dur_stats']['mean']}s (mediana {stats['dur_stats']['median']}s)

## 20 TRASCRITTI COMPLETI
{text_sample}

## COMPITO
Estrai la "grammatica narrativa" del creator. Restituisci JSON:

{{
  "narrative_signature": "<1-2 frasi: come racconta le storie>",
  "opening_rules": ["<come apre i video, es. 'claim geografico + luogo'>", ...],
  "body_structure": ["<fasi del corpo>", ...],
  "closing_rules": ["<come chiude, es. 'esortazione geografica diretta'>", ...],
  "transitions_between_beats": ["<come passa da un beat all'altro>", ...],
  "content_types": ["<tipi di contenuto che fa>", ...]
}}

Sii specifico. Rispondi SOLO con JSON valido.
"""

    txt = claude_call("Sei un analista di storytelling TikTok.", prompt, 4000)
    if not txt:
        return None
    try:
        t = txt.strip()
        if t.startswith('```'):
            t = t.split('\n', 1)[1]
            if t.endswith('```'):
                t = t[:-3]
        return json.loads(t)
    except Exception as e:
        log(f"  narrative synth parse error: {e}")
        return {'_raw': txt[:2000]}


def synth_lexical(stats, agg):
    """Sintesi strato lessicale."""
    # Estrai frasi di apertura e chiusura
    openings_txt = []
    closings_txt = []
    for t in agg['all_text']:
        if not t:
            continue
        parts = re.split(r'[.!?]+', t)
        if len(parts) >= 2:
            openings_txt.append(parts[0].strip())
            closings_txt.append(parts[-2].strip())

    open_sample = '\n'.join(f'- "{o[:150]}"' for o in openings_txt[:30] if o)
    close_sample = '\n'.join(f'- "{c[:150]}"' for c in closings_txt[:30] if c)

    words_str = ', '.join(f"{w}({n})" for w, n in stats['top_words'][:60])
    bigrams_str = '\n'.join(f'- "{b}" ({n})' for b, n in stats['top_bigrams'][:30])
    trigrams_str = '\n'.join(f'- "{t}" ({n})' for t, n in stats['top_trigrams'][:20])

    prompt = f"""Sei un linguista. Analizza il "fingerprint" linguistico del creator TikTok @jay.emme.

## VOCABOLARIO TOP 60 (parola → occorrenze)
{words_str}

## BIGRAMMI RICORRENTI
{bigrams_str}

## TRIGRAMMI RICORRENTI
{trigrams_str}

## FRASI DI APERTURA (30 esempi)
{open_sample}

## FRASI DI CHIUSURA (30 esempi)
{close_sample}

## COMPITO
Estrai il fingerprint linguistico. Restituisci JSON:

{{
  "language_signature": "<1-2 frasi: come parla>",
  "signature_words": ["<15 parole chiave RICORRENTI e caratteristiche>"],
  "banned_words": ["<parole che NON usa mai>"],
  "opening_phrases": ["<5 template di apertura, es. 'X è finalmente arrivato a Y'>"],
  "closing_phrases": ["<5 template di chiusura>"],
  "sentence_patterns": ["<formule sintattiche tipiche, es. 'Guarda questa X, Y'>"],
  "tone": "<tono in 1 frase>"
}}

Rispondi SOLO con JSON valido.
"""

    txt = claude_call("Sei un linguista computazionale.", prompt, 4000)
    if not txt:
        return None
    try:
        t = txt.strip()
        if t.startswith('```'):
            t = t.split('\n', 1)[1]
            if t.endswith('```'):
                t = t[:-3]
        return json.loads(t)
    except Exception as e:
        log(f"  lexical synth parse error: {e}")
        return {'_raw': txt[:2000]}


# ============================================================
# MAIN
# ============================================================

def main():
    log("=== Fase D START ===")
    t0 = time.time()

    log("[D1] Carico analisi...")
    videos = load_all()
    log(f"     {len(videos)} video caricati")

    log("[D1] Aggrego...")
    agg = aggregate(videos)
    stats = stats_from_agg(agg)
    log(f"     {len(agg['all_shots'])} shot aggregati")
    log(f"     dur media: {stats['dur_stats']['mean']}s | mediana: {stats['dur_stats']['median']}s")
    log(f"     top word: {stats['top_words'][:5]}")

    # Salva aggregato (debug)
    agg_path = BRAIN / '_aggregated.json'
    agg_light = {
        'n_videos': agg['n_videos'],
        'n_shots': len(agg['all_shots']),
        'stats': stats,
    }
    agg_path.write_text(json.dumps(agg_light, indent=2, ensure_ascii=False))
    log(f"     aggregato salvato: {agg_path}")

    log("[D2] Sintesi Claude: layer VISIVO...")
    visual = synth_visual(agg, stats)
    log(f"     visual OK: {bool(visual)}")

    log("[D2] Sintesi Claude: layer NARRATIVO...")
    narrative = synth_narrative(agg, stats)
    log(f"     narrative OK: {bool(narrative)}")

    log("[D2] Sintesi Claude: layer LESSICALE...")
    lexical = synth_lexical(stats, agg)
    log(f"     lexical OK: {bool(lexical)}")

    # Assembla brain finale
    brain = {
        'schema_version': '0.1',
        'generated_at': time.strftime('%Y-%m-%dT%H:%M:%S'),
        'source': {
            'n_videos': len(videos),
            'n_shots': len(agg['all_shots']),
            'n_words': sum(len(t.split()) for t in agg['all_text']),
        },
        'rhythmic_memory': {
            'dur_stats': stats['dur_stats'],
            'dur_opening_mean': stats['dur_opening_mean'],
            'dur_closing_mean': stats['dur_closing_mean'],
            'roles_dist': stats['roles_dist'],
            'compositions_dist': stats['compositions_dist'],
            'angoli_dist': stats['angoli_dist'],
            'luci_dist': stats['luci_dist'],
            'human_ratio': stats['human_ratio'],
            'logo_ratio': stats['logo_ratio'],
            'food_ratio': stats['food_ratio'],
            'venue_ratio': stats['venue_ratio'],
        },
        'visual_memory': visual,
        'narrative_memory': narrative,
        'lexical_memory': lexical,
    }

    brain_path = BRAIN / 'creator_brain_v0.1.json'
    brain_path.write_text(json.dumps(brain, indent=2, ensure_ascii=False))

    dt = time.time() - t0
    log(f"=== Fase D DONE in {dt:.0f}s ({dt/60:.1f}min) ===")
    log(f"  brain: {brain_path} ({os.path.getsize(brain_path)} byte)")


if __name__ == '__main__':
    main()
