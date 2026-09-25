#!/usr/bin/env python3
"""Estrai bigrammi/trigrammi + frasi verbatim dai 107 video di jay.emme.

Output: /tmp/lexicon_analysis.json
"""
import os, sys, json, re
from pathlib import Path
from collections import Counter

BASE = Path('/opt/reel-agent')
CORPUS = BASE / 'brain' / 'corpus' / 'jay.emme' / 'videos'

# Stopwords italiane
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
            if ft:
                texts.append(ft)
        except Exception:
            pass
    return texts


def tokenize(text):
    """Tokenizza tenendo conto di apostrofi (l'ala, c'è, un')."""
    text = text.lower()
    # Sostituisci punteggiatura con spazi, ma mantieni apostrofi
    text = re.sub(r"[.,;:!?()\[\]{}\"“”«»]", ' ', text)
    # Split su spazi
    return [w.strip("'") for w in text.split() if w.strip("'")]


def compute_ngrams(tokens, n=2, min_freq=3, top=100):
    """Bigrammi/trigrammi escludendo quelli con stopwords."""
    out = []
    for i in range(len(tokens) - n + 1):
        gram = tokens[i:i+n]
        # Salta se la prima o l'ultima parola sono stopwords "pure"
        if gram[0] in STOP and gram[-1] in STOP:
            continue
        # Salta se contengono solo stopwords
        if all(t in STOP for t in gram):
            continue
        # Salta se contengono token molto corti (a, e, i, o, u)
        if any(len(t) < 2 for t in gram):
            continue
        out.append(' '.join(gram))
    counter = Counter(out)
    return [(g, n) for g, n in counter.most_common(top) if n >= min_freq]


def extract_openings_closings(texts):
    """Estrai prime e ultime frasi di ogni trascritto."""
    openings = []
    closings = []
    for t in texts:
        # Split su . ! ? mantenendo le frasi
        sentences = re.split(r'(?<=[.!?])\s+', t.strip())
        sentences = [s.strip() for s in sentences if len(s.strip()) > 15]
        if len(sentences) >= 2:
            openings.append(sentences[0])
            closings.append(sentences[-1])
    return openings, closings


def extract_sentences_with_word(texts, word, max_results=20):
    """Trova frasi che contengono una parola specifica (per capire come la usa)."""
    out = []
    pattern = re.compile(r'\b' + re.escape(word) + r'\w*\b', re.IGNORECASE)
    for t in texts:
        for sent in re.split(r'(?<=[.!?])\s+', t):
            if pattern.search(sent):
                s = sent.strip()
                if 20 < len(s) < 200:
                    out.append(s)
                if len(out) >= max_results:
                    return out
    return out


def main():
    print('[1/4] Carico trascritti...')
    texts = load_transcripts()
    print(f'  {len(texts)} trascritti')

    print('[2/4] Tokenize...')
    all_tokens = []
    for t in texts:
        all_tokens.extend(tokenize(t))
    print(f'  {len(all_tokens)} token totali')

    # Vocabolario (freq singole parole non-stopword)
    word_freq = Counter(w for w in all_tokens if w not in STOP and len(w) >= 3)
    top_words = word_freq.most_common(120)

    print('[3/4] Bigrammi + trigrammi...')
    bigrams = compute_ngrams(all_tokens, n=2, min_freq=3, top=80)
    trigrams = compute_ngrams(all_tokens, n=3, min_freq=2, top=60)

    print('[4/4] Aperture/chiusure + pattern specifici...')
    openings, closings = extract_openings_closings(texts)

    # Come usa parole chiave (per capire sostituzioni)
    patterns_by_word = {}
    for w in ['veramente', 'davvero', 'super', 'ovviamente', 'proprio']:
        patterns_by_word[w] = extract_sentences_with_word(texts, w, max_results=15)

    # Distribuzione parole funzionali (per capire se usa "praticamente", "appunto", ecc.)
    marker_words = ['ovviamente', 'davvero', 'veramente', 'super', 'tra', "l'altro",
                    'appunto', 'praticamente', 'comunque', 'insomma', 'quindi',
                    'infatti', 'allora', 'ecco', 'devo', 'dire', 'guarda', 'guardate']
    markers = {w: word_freq.get(w, 0) for w in marker_words}

    out = {
        'n_videos': len(texts),
        'n_tokens': len(all_tokens),
        'top_words': top_words,
        'bigrams': bigrams,
        'trigrams': trigrams,
        'openings': openings[:40],
        'closings': closings[:40],
        'patterns_by_word': patterns_by_word,
        'marker_frequencies': markers,
    }
    Path('/tmp/lexicon_analysis.json').write_text(json.dumps(out, indent=2, ensure_ascii=False))

    print()
    print('=== TOP 30 PAROLE ===')
    for w, n in top_words[:30]:
        print(f'  {w}: {n}')

    print()
    print('=== TOP 20 BIGRAMMI ===')
    for g, n in bigrams[:20]:
        print(f'  "{g}": {n}')

    print()
    print('=== TOP 15 TRIGRAMMI ===')
    for g, n in trigrams[:15]:
        print(f'  "{g}": {n}')

    print()
    print('=== MARKER FREQUENCIES ===')
    for w, n in sorted(markers.items(), key=lambda x: -x[1]):
        print(f'  {w}: {n}')

    print()
    print('=== APERTURE (esempi) ===')
    for o in openings[:8]:
        print(f'  - {o[:90]}')

    print()
    print('=== CHIUSURE (esempi) ===')
    for c in closings[:8]:
        print(f'  - {c[:90]}')

    print()
    print('=== COME USA "veramente" ===')
    for s in patterns_by_word.get('veramente', [])[:8]:
        print(f'  - {s[:100]}')

    print()
    print(f'saved: /tmp/lexicon_analysis.json')


if __name__ == '__main__':
    main()
