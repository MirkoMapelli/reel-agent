#!/usr/bin/env python3
"""Arricchisce active_brain.json con:
- bigrammi/trigrammi firma (dal corpus)
- frasi verbatim apertura/chiusura
- lista di varianti per parola firma (per densità controllata)
"""
import os, sys, json, re
from pathlib import Path
from collections import Counter

BASE = Path('/opt/reel-agent')
CORPUS = BASE / 'brain' / 'corpus' / 'jay.emme' / 'videos'
ACTIVE = BASE / 'brain' / 'active_brain.json'
BACKUP = BASE / 'brain' / f'active_brain.json.bak_lex_{int(__import__("time").time())}'

STOP = set("""il la lo le gli i un una uno di da in con su per tra fra a e o ma che chi cui
non si mi ti ci vi ne è sono sei siamo siete ho hai ha abbiamo avete hanno
questo questa questi queste quello quella quelli quelle come dove quando perché anche
più meno molto poco tanto tutti tutte tutto tutta solo già ancora sempre mai poi prima dopo
qui qua lì là io tu lui lei noi voi loro mio tuo suo nostro vostro essere avere fare dire
andare venire davvero proprio magari forse cioè quindi allora ok sì no bene male cose cosa
roba tipo se del della dello dei delle degli dal dalla dallo dai dalle dagli nel nella
nello nei nelle negli al alla allo ai alle agli""".split())


def load_transcripts():
    texts = []
    for vdir in sorted(CORPUS.iterdir()):
        if not vdir.is_dir(): continue
        ap = vdir / 'analysis.json'
        if not ap.exists(): continue
        try:
            d = json.loads(ap.read_text())
            ft = d.get('full_text', '').strip()
            if ft: texts.append(ft)
        except Exception:
            pass
    return texts


def tokenize(text):
    text = text.lower()
    text = re.sub(r"[.,;:!?()\[\]{}\"“”«»]", ' ', text)
    return [w.strip("'") for w in text.split() if w.strip("'")]


def compute_ngrams(tokens, n=2, min_freq=3, top=40):
    out = []
    for i in range(len(tokens) - n + 1):
        gram = tokens[i:i+n]
        if gram[0] in STOP and gram[-1] in STOP:
            continue
        if all(t in STOP for t in gram):
            continue
        if any(len(t) < 2 for t in gram):
            continue
        out.append(' '.join(gram))
    counter = Counter(out)
    return [(g, n) for g, n in counter.most_common(top) if n >= min_freq]


def extract_sentences(texts, n_open=15, n_close=15):
    openings = []
    closings = []
    for t in texts:
        sentences = re.split(r'(?<=[.!?])\s+', t.strip())
        sentences = [s.strip() for s in sentences if 15 < len(s.strip()) < 150]
        if len(sentences) >= 2:
            openings.append(sentences[0])
            closings.append(sentences[-1])
    return openings[:n_open], closings[:n_close]


def build_variant_map(marker_words):
    """Per ogni parola firma, propone varianti (altre firme) da usare per densità."""
    # Gruppi di "varianti intercambiabili" per evitare ripetizioni
    variants = {
        'ovviamente': ['appunto', 'infatti', 'praticamente'],
        'veramente': ['super', 'proprio', 'incredibilmente'],
        'comunque': ['insomma', 'in ogni caso'],
        'appunto': ['esatto', 'infatti'],
        'super': ['molto', 'proprio'],
        'praticamente': ['in pratica', 'fondamentalmente'],
        'infatti': ['appunto', 'esatto'],
        'guardate': ['guardate qua', 'vedete'],
        'insomma': ['alla fine', 'in conclusione'],
    }
    return {k: v for k, v in variants.items() if k in marker_words}


def main():
    print('[1/4] Carico trascritti...')
    texts = load_transcripts()
    print(f'  {len(texts)} trascritti')

    print('[2/4] Tokenize + ngram...')
    tokens = []
    for t in texts:
        tokens.extend(tokenize(t))
    bigrams = compute_ngrams(tokens, n=2, min_freq=3, top=40)
    trigrams = compute_ngrams(tokens, n=3, min_freq=2, top=30)

    print('[3/4] Aperture/chiusure verbatim...')
    openings, closings = extract_sentences(texts)

    print('[4/4] Arricchisco active_brain.json...')
    brain = json.loads(ACTIVE.read_text())
    lexical = brain.setdefault('lexical_memory', {})

    # Bigrammi e trigrammi
    lexical['signature_bigrams'] = [{'gram': g, 'freq': n} for g, n in bigrams]
    lexical['signature_trigrams'] = [{'gram': g, 'freq': n} for g, n in trigrams]

    # Frasi verbatim
    lexical['verbatim_openings'] = openings
    lexical['verbatim_closings'] = closings

    # Varianti per densità controllata
    lexical['signature_variants'] = build_variant_map(
        {'ovviamente', 'veramente', 'comunque', 'appunto', 'super',
         'praticamente', 'infatti', 'guardate', 'insomma'})

    # Backup + save
    BACKUP.write_text(json.dumps(brain, indent=2, ensure_ascii=False))
    ACTIVE.write_text(json.dumps(brain, indent=2, ensure_ascii=False))

    print()
    print('=== BIGRAMMI FIRMA (top 15) ===')
    for g, n in bigrams[:15]:
        print(f'  "{g}": {n}')
    print()
    print('=== TRIGRAMMI FIRMA (top 10) ===')
    for g, n in trigrams[:10]:
        print(f'  "{g}": {n}')
    print()
    print('=== APERTURE VERBATIM (5 esempi) ===')
    for o in openings[:5]:
        print(f'  - {o[:90]}')
    print()
    print('=== CHIUSURE VERBATIM (5 esempi) ===')
    for c in closings[:5]:
        print(f'  - {c[:90]}')
    print()
    print(f'backup: {BACKUP}')
    print(f'saved: {ACTIVE}')
    print(f'lexical_memory keys: {list(lexical.keys())}')


if __name__ == '__main__':
    main()
