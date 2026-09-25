#!/usr/bin/env python3
"""Rigenera il Creator Brain dal corpus.

Legge brain/corpus/{creator}/videos/*/analysis.json, aggrega per creator,
calcola contrasto (jay.emme vs altri), sintetizza via Claude, versiona.
"""
import os, sys, json, time, re, glob
from pathlib import Path
from collections import Counter, defaultdict
from dotenv import load_dotenv

BASE = Path('/opt/reel-agent')
CORPUS = BASE / 'brain' / 'corpus'
VERSIONS = BASE / 'brain' / 'versions'
ARCHIVE = VERSIONS / '_archive'
LOG = BASE / 'brain' / 'build.log'
ACTIVE_PTR = BASE / 'brain' / 'active_version.txt'
ACTIVE_BRAIN = BASE / 'brain' / 'active_brain.json'

load_dotenv(BASE / '.env')
from openai import OpenAI

CLIENT = OpenAI(base_url='https://openrouter.ai/api/v1',
                api_key=os.environ['OPENROUTER_API_KEY'])
MODEL = 'anthropic/claude-sonnet-4.5'
PRIMARY_CREATOR = 'jay.emme'
RETENTION = 3


def log(msg):
    line = f"[{time.strftime('%H:%M:%S')}] {msg}"
    print(line, flush=True)
    LOG.parent.mkdir(exist_ok=True, parents=True)
    with open(LOG, 'a') as f:
        f.write(line + '\n')


# ============================================================
# STEP 1 — Carica corpus
# ============================================================

def load_corpus():
    """Ritorna {creator: [analysis_dict, ...]}."""
    corpus = {}
    if not CORPUS.exists():
        return corpus
    for creator_dir in sorted(CORPUS.iterdir()):
        if not creator_dir.is_dir():
            continue
        videos_dir = creator_dir / 'videos'
        if not videos_dir.exists():
            continue
        analyses = []
        for vdir in sorted(videos_dir.iterdir()):
            if not vdir.is_dir():
                continue
            apath = vdir / 'analysis.json'
            if not apath.exists():
                continue
            try:
                a = json.loads(apath.read_text())
                a['_video_id'] = vdir.name
                analyses.append(a)
            except Exception as e:
                log(f"  errore {apath}: {e}")
        if analyses:
            corpus[creator_dir.name] = analyses
    return corpus


# ============================================================
# STEP 2 — Aggrega per creator
# ============================================================

def aggregate(analyses):
    """Statistiche aggregate da N analysis.json di un creator."""
    roles = Counter()
    comps = Counter()
    angles = Counter()
    lightings = Counter()
    durs = []
    opening_roles = Counter()
    closing_roles = Counter()
    opening_durs = []
    closing_durs = []
    full_texts = []
    human = food = venue = logo = 0
    tot_shots = 0

    for a in analyses:
        shots = a.get('shots', [])
        if not shots:
            continue
        tot_shots += len(shots)
        for i, sh in enumerate(shots):
            roles[sh.get('role', 'other')] += 1
            comps[sh.get('composition', '')] += 1
            angles[sh.get('angle', '')] += 1
            lightings[sh.get('lighting', '')] += 1
            d = sh.get('dur') or 0
            if d > 0:
                durs.append(d)
            r = sh.get('role', '')
            if r.startswith('food_') or r.startswith('drink_'):
                food += 1
            if r.startswith('venue_'):
                venue += 1
            if sh.get('has_person'):
                human += 1
            if sh.get('has_logo'):
                logo += 1
            if i < 3:
                opening_roles[r] += 1
                if d > 0:
                    opening_durs.append(d)
            if i >= len(shots) - 3:
                closing_roles[r] += 1
                if d > 0:
                    closing_durs.append(d)

        ft = a.get('full_text', '')
        if ft:
            full_texts.append(ft)

    durs_sorted = sorted(durs)
    n = len(durs_sorted)
    def pct(p):
        return round(durs_sorted[int(n * p)], 2) if n else 0
    dur_stats = {
        'count': n,
        'mean': round(sum(durs)/n, 2) if n else 0,
        'median': round(durs_sorted[n//2], 2) if n else 0,
        'p10': pct(0.1), 'p90': pct(0.9),
        'min': round(durs_sorted[0], 2) if n else 0,
        'max': round(durs_sorted[-1], 2) if n else 0,
    }

    return {
        'n_videos': len(analyses),
        'n_shots': tot_shots,
        'roles_dist': dict(roles.most_common()),
        'compositions_dist': dict(comps.most_common()),
        'angles_dist': dict(angles.most_common()),
        'lightings_dist': dict(lightings.most_common()),
        'dur_stats': dur_stats,
        'dur_opening_mean': round(sum(opening_durs)/len(opening_durs), 2) if opening_durs else 0,
        'dur_closing_mean': round(sum(closing_durs)/len(closing_durs), 2) if closing_durs else 0,
        'opening_roles': dict(opening_roles.most_common(5)),
        'closing_roles': dict(closing_roles.most_common(5)),
        'human_ratio': round(human/tot_shots, 3) if tot_shots else 0,
        'food_ratio': round(food/tot_shots, 3) if tot_shots else 0,
        'venue_ratio': round(venue/tot_shots, 3) if tot_shots else 0,
        'logo_ratio': round(logo/tot_shots, 3) if tot_shots else 0,
        'full_texts': full_texts,
    }


def extract_ngrams(texts, top_words=80, top_bigrams=40, top_trigrams=25):
    full = ' '.join(texts).lower()
    stopwords = set('''il la lo le gli i un una uno di da in con su per tra fra a e o ma che chi cui non si mi ti ci vi ne
        al alla allo ai alle agli del della dello dei delle degli dal dalla dallo dai dalle dagli nel nella nello nei nelle negli
        è sono sei siamo siete ho hai ha abbiamo avete hanno questo questa questi queste quello quella quelli quelle
        come dove quando perché anche più meno molto poco tanto tutti tutte tutto tutta solo già ancora sempre mai
        poi prima dopo qui qua lì là io tu lui lei noi voi loro mio tuo suo nostro vostro essere avere fare dire andare venire
        davvero proprio magari forse cioè quindi allora ok sì no bene male cose cosa roba tipo'''.split())

    words = re.findall(r"\b[a-zàèéìòù']{3,}\b", full)
    words = [w for w in words if w not in stopwords]
    top_w = Counter(words).most_common(top_words)

    all_w = re.findall(r"\b[a-zàèéìòù']{2,}\b", full)
    bigrams = Counter(zip(all_w, all_w[1:]))
    bigrams = [(' '.join(k), v) for k, v in bigrams.most_common(top_bigrams) if v >= 5]

    trigrams = Counter(zip(all_w, all_w[1:], all_w[2:]))
    trigrams = [(' '.join(k), v) for k, v in trigrams.most_common(top_trigrams) if v >= 3]

    return top_w, bigrams, trigrams


# ============================================================
# STEP 3 — Claude calls
# ============================================================

def claude(system, user, max_tokens=4000):
    try:
        r = CLIENT.chat.completions.create(
            model=MODEL,
            messages=[{'role': 'system', 'content': system},
                      {'role': 'user', 'content': user}],
            max_tokens=max_tokens, temperature=0.3,
        )
        return r.choices[0].message.content
    except Exception as e:
        log(f"  Claude error: {type(e).__name__}: {str(e)[:200]}")
        return None


def parse_json(raw):
    t = (raw or '').strip()
    if t.startswith('```'):
        t = t.split('\n', 1)[1]
        if t.endswith('```'):
            t = t[:-3]
    i, j = t.find('{'), t.rfind('}')
    if i >= 0 and j > i:
        t = t[i:j+1]
    return json.loads(t)


# ============================================================
# STEP 4 — Build
# ============================================================

def build_brain(corpus, primary=PRIMARY_CREATOR):
    if primary not in corpus:
        raise RuntimeError(f"creator primario '{primary}' non nel corpus")

    log(f"[1/5] aggrego {len(corpus)} creator...")
    per_creator = {c: aggregate(v) for c, v in corpus.items()}
    primary_stats = per_creator[primary]

    log(f"      {primary}: {primary_stats['n_videos']} video, {primary_stats['n_shots']} shot")
    for c in per_creator:
        if c == primary:
            continue
        log(f"      {c}: {per_creator[c]['n_videos']} video, {per_creator[c]['n_shots']} shot")

    # N-grammi del primary
    top_w, top_bg, top_tg = extract_ngrams(primary_stats['full_texts'])

    # ===== Contrasto =====
    log("[2/5] calcolo contrasto...")
    others = {c: s for c, s in per_creator.items() if c != primary}
    contrast_context = ""
    if others:
        contrast_context = f"""
# CONFRONTO CON ALTRI CREATOR (dati reali)
Questi sono altri creator dello stesso genere. Tu li usi SOLO come contrasto per
capire cosa fa `{primary}` di DIVERSO.

"""
        for c, s in others.items():
            contrast_context += f"""## @{c} ({s['n_videos']} video)
- Durata media shot: {s['dur_stats']['mean']}s (mediana {s['dur_stats']['median']})
- Mix: umano {s['human_ratio']*100:.0f}% · food {s['food_ratio']*100:.0f}% · venue {s['venue_ratio']*100:.0f}%
- Ruoli dominanti: {list(s['roles_dist'].items())[:5]}
- Apertura tipica: {s['opening_roles']}
- Chiusura tipica: {s['closing_roles']}
"""
    else:
        contrast_context = "(nessun altro creator nel corpus — signature unique sara' generica)"

    # Sample descrizioni del primary
    primary_desc_samples = []
    for a in corpus[primary][:30]:
        for sh in a.get('shots', [])[:3]:
            primary_desc_samples.append(
                f"{sh.get('role','?')} | {sh.get('subject','')[:30]} | {(sh.get('description') or '')[:80]}"
            )
    primary_desc_text = '\n'.join(primary_desc_samples[:90])

    # Sample aperture/chiusure
    openings = []
    closings = []
    for a in corpus[primary][:30]:
        shots = a.get('shots', [])
        if len(shots) >= 3:
            openings.append(' → '.join(f"{s.get('role','?')}({s.get('dur',0):.1f}s)" for s in shots[:3]))
            closings.append(' → '.join(f"{s.get('role','?')}({s.get('dur',0):.1f}s)" for s in shots[-3:]))

    # Sample trascritti
    text_samples = []
    for t in primary_stats['full_texts'][:15]:
        if t:
            text_samples.append(t[:800])
    text_sample = '\n\n---\n\n'.join(text_samples)

    # ===== CHIAMATA 1: visual =====
    log("[3/5] sintesi visiva...")
    prompt_visual = f"""Analizza lo stile visivo del creator TikTok @{primary}.

## STATISTICHE VISIVE @{primary}
- Distribuzione ruoli: {json.dumps(primary_stats['roles_dist'], indent=2)[:800]}
- Composizioni: {primary_stats['compositions_dist']}
- Angoli: {primary_stats['angles_dist']}
- Luci: {primary_stats['lightings_dist']}
- Mix: umano {primary_stats['human_ratio']} · food {primary_stats['food_ratio']} · venue {primary_stats['venue_ratio']} · logo {primary_stats['logo_ratio']}
- Durata media: {primary_stats['dur_stats']['mean']}s (mediana {primary_stats['dur_stats']['median']}s)
- Apertura media: {primary_stats['dur_opening_mean']}s | Chiusura media: {primary_stats['dur_closing_mean']}s

{contrast_context}

## 90 SHOT DI @{primary}
{primary_desc_text}

## APERTURE (30 video)
{chr(10).join('- ' + o for o in openings[:30])}

## CHIUSURE (30 video)
{chr(10).join('- ' + c for c in closings[:30])}

## COMPITO
Estrai la "grammatica visiva" di @{primary}. Restituisci JSON:
{{
  "visual_signature": "1-2 frasi",
  "opening_rules": ["...", ...],
  "closing_rules": ["...", ...],
  "food_vocabulary": ["...", ...],
  "human_vocabulary": ["...", ...],
  "venue_vocabulary": ["...", ...],
  "signature_moves": ["...", ...],
  "unique_vs_others": ["cosa fa @{primary} che gli altri creator NON fanno visivamente"]
}}
Max 8 per lista. Rispondi SOLO con JSON valido."""

    v_raw = claude("Sei un analista di stile visivo TikTok.", prompt_visual, 4000)
    visual = parse_json(v_raw) if v_raw else None
    log(f"      visual: {'OK' if visual else 'FAIL'}")

    # ===== CHIAMATA 2: narrative =====
    log("[4/5] sintesi narrativa...")
    prompt_narr = f"""Analizza lo stile narrativo del creator TikTok @{primary}.

## NUMERI
- {primary_stats['n_videos']} video, {primary_stats['n_shots']} shot totali

{contrast_context}

## 15 TRASCRITTI
{text_sample}

## COMPITO
Estrai la "grammatica narrativa" di @{primary}. JSON:
{{
  "narrative_signature": "1-2 frasi",
  "opening_rules": ["...", ...],
  "body_structure": ["...", ...],
  "closing_rules": ["...", ...],
  "transitions_between_beats": ["...", ...],
  "content_types": ["...", ...],
  "unique_vs_others": ["cosa fa @{primary} che gli altri NON fanno a livello narrativo"]
}}
Rispondi SOLO con JSON valido."""

    n_raw = claude("Sei un analista di storytelling TikTok.", prompt_narr, 4000)
    narrative = parse_json(n_raw) if n_raw else None
    log(f"      narrative: {'OK' if narrative else 'FAIL'}")

    # ===== CHIAMATA 3: lexical =====
    log("[5/5] sintesi lessicale...")
    words_str = ', '.join(f"{w}({n})" for w, n in top_w[:60])
    bg_str = '\n'.join(f'- "{b}" ({n})' for b, n in top_bg[:30])
    tg_str = '\n'.join(f'- "{t}" ({n})' for t, n in top_tg[:20])

    # Frasi apertura/chiusura
    open_txt, close_txt = [], []
    for t in primary_stats['full_texts']:
        if not t:
            continue
        parts = re.split(r'[.!?]+', t)
        if len(parts) >= 2:
            open_txt.append(parts[0].strip())
            close_txt.append(parts[-2].strip())

    prompt_lex = f"""Analizza il fingerprint linguistico di @{primary}.

## VOCABOLARIO TOP 60
{words_str}

## BIGRAMMI
{bg_str}

## TRIGRAMMI
{tg_str}

## FRASI APERTURA (30)
{chr(10).join('- ' + o[:150] for o in open_txt[:30] if o)}

## FRASI CHIUSURA (30)
{chr(10).join('- ' + c[:150] for c in close_txt[:30] if c)}

## COMPITO
Estrai il fingerprint linguistico. JSON:
{{
  "language_signature": "1-2 frasi",
  "signature_words": ["15 parole ricorrenti"],
  "banned_words": ["parole che NON usa"],
  "opening_phrases": ["5 template apertura"],
  "closing_phrases": ["5 template chiusura"],
  "sentence_patterns": ["formule sintattiche tipiche"],
  "tone": "tono in 1 frase"
}}
Rispondi SOLO con JSON valido."""

    l_raw = claude("Sei un linguista computazionale.", prompt_lex, 4000)
    lexical = parse_json(l_raw) if l_raw else None
    log(f"      lexical: {'OK' if lexical else 'FAIL'}")

    # ===== Assembla brain =====
    brain = {
        'schema_version': '0.2',
        'generated_at': time.strftime('%Y-%m-%dT%H:%M:%S'),
        'primary_creator': primary,
        'corpus': {
            'creators': {c: s['n_videos'] for c, s in per_creator.items()},
            'n_videos_total': sum(s['n_videos'] for s in per_creator.values()),
            'n_shots_total': sum(s['n_shots'] for s in per_creator.values()),
        },
        'rhythmic_memory': {
            'dur_stats': primary_stats['dur_stats'],
            'dur_opening_mean': primary_stats['dur_opening_mean'],
            'dur_closing_mean': primary_stats['dur_closing_mean'],
            'roles_dist': primary_stats['roles_dist'],
            'compositions_dist': primary_stats['compositions_dist'],
            'angles_dist': primary_stats['angles_dist'],
            'lightings_dist': primary_stats['lightings_dist'],
            'human_ratio': primary_stats['human_ratio'],
            'food_ratio': primary_stats['food_ratio'],
            'venue_ratio': primary_stats['venue_ratio'],
            'logo_ratio': primary_stats['logo_ratio'],
        },
        'visual_memory': visual,
        'narrative_memory': narrative,
        'lexical_memory': lexical,
        'signature_unique': {
            'visual': (visual or {}).get('unique_vs_others', []),
            'narrative': (narrative or {}).get('unique_vs_others', []),
        },
    }
    return brain


# ============================================================
# STEP 5 — Versioning
# ============================================================

def next_version_number():
    """Ritorna il prossimo N per creator_brain_v0.N.json.

    Supporta sia v0.2 (schema nuovo) sia v1.json (retro-compat).
    """
    VERSIONS.mkdir(parents=True, exist_ok=True)
    nums = []
    for f in VERSIONS.glob('creator_brain_v*.json'):
        # Pattern: v0.2.json  -> group '2'
        # Pattern: v1.json    -> group '1'
        m = re.search(r'v0\.(\d+)\.json$', f.name)
        if m:
            nums.append(int(m.group(1)))
            continue
        m = re.search(r'v(\d+)\.json$', f.name)
        if m:
            nums.append(int(m.group(1)))
    return max(nums) + 1 if nums else 1


def save_versioned(brain):
    VERSIONS.mkdir(parents=True, exist_ok=True)
    ARCHIVE.mkdir(exist_ok=True, parents=True)

    n = next_version_number()
    fname = f'creator_brain_v0.{n}.json'
    fpath = VERSIONS / fname
    fpath.write_text(json.dumps(brain, indent=2, ensure_ascii=False))
    log(f"salvato: {fpath} ({os.path.getsize(fpath)} byte)")

    # Retention: tieni le 3 piu' recenti visibili
    all_versions = sorted(
        [f for f in VERSIONS.glob('creator_brain_v*.json')],
        key=lambda p: int(re.search(r'v0\.(\d+)\.json', p.name).group(1)),
    )
    if len(all_versions) > RETENTION:
        to_archive = all_versions[:-RETENTION]
        for old in to_archive:
            dest = ARCHIVE / old.name
            old.rename(dest)
            log(f"archiviato: {old.name} -> _archive/")

    # Aggiorna active_version.txt
    ACTIVE_PTR.write_text(f'v0.{n}\n')
    log(f"active_version.txt = v0.{n}")

    # Aggiorna active_brain.json (sempre = versione attiva)
    ACTIVE_BRAIN.write_text(json.dumps(brain, indent=2, ensure_ascii=False))
    log(f"active_brain.json aggiornato")

    return {'version': f'v0.{n}', 'path': str(fpath), 'size': os.path.getsize(fpath)}


def main():
    log("=== BRAIN BUILD START ===")
    t0 = time.time()

    corpus = load_corpus()
    if not corpus:
        log("ERRORE: corpus vuoto")
        return
    log(f"corpus caricato: {sum(len(v) for v in corpus.values())} video, {list(corpus.keys())}")

    brain = build_brain(corpus, primary=PRIMARY_CREATOR)
    result = save_versioned(brain)

    dt = time.time() - t0
    log(f"=== DONE in {dt:.0f}s: {result['version']} ({result['size']} byte) ===")
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()
