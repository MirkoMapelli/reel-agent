# Style Bible — Creator Reference

> Contratto operativo per clonare lo stile di questo creator.
> Generato il 2026-09-19 da analisi multi-modale.

## ⚠️ Coverage
- Video analizzati: **1**
- Consigliato minimo: **10**
- Stato: PARTIAL — analisi basata su 1 solo video di riferimento. Per generalizzare servono 10-15 video.

---

## 1. Source of truth (video di riferimento)

- **Durata finished**: 79.57s
- **Numero shot**: 37
- **Durata media shot**: 2.15s (mediana 2.0s)
- **Range durata shot**: 1.2s – 3.97s
- **Rapporto di selezione source**: 18.5%

---

## 2. Archetipi narrativi

### Hook (apertura — varia per video)
- creator mostra cibo in primo piano con testo brand
- annuncio novità/esclusiva ('finalmente arrivato')
- domanda retorica o claim forte
- problema/setup della storia

**Durata tipica**: 1.5-3.0s

### Context
- insegna/entrata del locale
- tavolo o vassoio apparecchiato
- creator che cammina nel locale
- testo con indirizzo o nome del posto

### Product (il cuore del video)
- primo piano cibo (tender/wings/fries)
- dettaglio glassatura/salsa
- vassoio completo dall'alto
- packaging brand

### Reaction
- creator assaggia con espressione di piacere
- creator parla in camera
- occhi chiusi durante il morso
- sorriso/commento positivo

### CTA (chiusura — varia per video, NO mascotte fisiche)
- curiosità prospettica ('farò la prossima volta...')
- enfasi sillabata ('CLA-MO-RO-SO')
- consiglio diretto al pubblico
- domanda diretta al pubblico ('voi cosa prendereste?')
- invito esplicito a provare il locale

**Nota**: il creator NON usa premi fisici/mascotte. Spesso chiude con **enfasi sillabata** tipo "CLA-MO-RO-SO".

---

## 3. Struttura tipica (da questo video)
- `hook` × 1
- `product` × 2
- `establishing` × 1
- `context` × 2
- `establishing` × 1
- `product` × 1
- `reaction` × 1
- `detail` × 1
- `product` × 1
- `reaction` × 1
- `product` × 10
- `reaction` × 1
- `product` × 1
- `reaction` × 1
- `product` × 1
- `reaction` × 1
- `product` × 2
- `reaction` × 3
- `product` × 2
- `establishing` × 1
- `context` × 1
- `reaction` × 1

**Distribuzione tipi**:
- product: 20 shot
- reaction: 9 shot
- establishing: 3 shot
- context: 3 shot
- hook: 1 shot
- detail: 1 shot

**Pacing per fase**:
- Fase 1: 1.91s
- Fase 2: 2.5s
- Fase 3: 1.74s
- Fase 4: 2.04s
- Fase 5: 2.57s

---

## 4. Stile visivo

- **Composizione dominante**: `detail`
- **Movimento camera dominante**: `static`
- **Transizione dominante**: `cut`

**Composition distribution**:
- detail: 12 shot
- close_up_face: 8 shot
- medium_face: 8 shot
- wide: 5 shot
- hands: 3 shot
- wide_face: 1 shot

**Camera distribution**:
- static: 32 shot
- pan_right: 2 shot
- tilt_up: 1 shot
- zoom_in: 1 shot
- tilt_down: 1 shot

**Shot type distribution**:
- statica_tripod: 26 shot
- dall_alto_90: 4 shot
- pov: 3 shot
- dal_basso: 2 shot
- handheld: 2 shot

---

## 5. Sottotitoli

- **Stile**: word-by-word (una parola alla volta, sync col VO)
- **Fill**: RGB [252, 252, 250] → ASS `&H00FAFCFC&`
- **Outline**: RGB [6, 5, 5] → ASS `&H00050506&`
- **Altezza testo**: 325px (16.9% del frame)
- **Margine dal fondo**: 155px
- **Posizione**: bottom_center

**Pattern speciali**:
- sillabazione per enfasi (CLA-MO-RO-SO)
- maiuscolo forzato (tutto in caps)
- outline nero spesso, fill bianco

---

## 6. Voce

- **WPM**: **243** (molto veloce)
- **Pitch**: 137 Hz (±33)
- **Pause**: 2
- **Durata media segmento VO**: 3.21s

### Vocabolario combinato (top 30, pulito da errori Whisper)
ovviamente, super, wings, boneless, flavor, provare, oggi, location, subito, sapore, italia, secondo, altro, preso, classiche, fries, salsa, ranch, lemon, pepper, tender, devo, caso, possibilità, casa, pancakes, milano, aperto, menu, morbidi

### Frasi di esempio (pulite)
- "Wingstop è finalmente arrivato in Italia e con lui quello che probabilmente sarà anche il miglior polofretto."
- "Oggi siamo a Milano nel loro evento di apertura del loro primo storio in Italia, aperto proprio un viadante 16."
- "Posizione super centrale e location clamorosa."
- "Noi abbiamo subito ordinato, siamo saliti al secondo piano dove tra l'altro c'era anche il DJ."
- "E questo è tutto ciò che abbiamo preso."
- "Di base abbiamo preso dei menu combo con 8 wings classiche o boneless,"

### Errori Whisper corretti automaticamente
- `polofretto` → `pollofritto`
- `polofritto` → `pollofritto`
- `pollofretto` → `pollofritto`
- `renna` → `ranch`
- `rence` → `ranch`
- `renci` → `ranch`
- `viadante` → `via dante`
- `storio` → `store`
- `frirefill` → `free refill`
- `fries` → `fries`
- `retenders` → `tenders`
- `cedar` → `cheddar`
- `misalze` → `migliori`
- `grunnato` → `granulato`
- `grumato` → `granulato`
- `cla mo ro so` → `clamoroso`
- `in caso` → `in ogni caso`

---

## 7. Mix audio

- Voce: 61.6%
- Ambiente: 30.0%
- ASMR: 8.4%
- Musica: **assente**

---

## 8. Istruzioni operative

1. **Durata target**: ~79.57s (37 shot da 2.15s)
2. **Hook**: scegli UNA delle varianti hook sopra
3. **Corpo**: alterna `product` (54%) con `reaction` (24%) e `context`/`establishing` (16%)
4. **Chiusura**: scegli UNA delle varianti CTA (spesso enfasi sillabata)
5. **Camera**: static nel 86% degli shot
6. **Sottotitoli**: `&H00FAFCFC&` + outline `&H00050506&`, height 325px, margin 155px, word-by-word
7. **Tono**: VELOCE (243 wpm), pause rare
8. **Vocab**: vedi lista sopra, adattata al contesto del ristorante
9. **Ratio**: scarta ~82% del materiale
10. **Transizioni**: solo `cut`
