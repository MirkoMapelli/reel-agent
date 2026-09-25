#!/usr/bin/env bash
# Salva in modo sicuro la OpenRouter API key in .env e testa la connessione.
set -e
ENV_FILE="/opt/reel-agent/.env"

echo "=== OpenRouter key setup ==="
echo "Ottieni la key da: https://openrouter.ai/keys"
echo
read -s -p "Incolla la key OpenRouter (non sara' visibile): " ORK
echo
echo

if [ -z "$ORK" ]; then
    echo "ERRORE: key vuota, esco."
    exit 1
fi

if [ "${ORK:0:9}" != "sk-or-v1-" ]; then
    echo "WARN: la key non inizia con 'sk-or-v1-'. Sicuro sia quella giusta?"
    read -p "Continuare comunque? (y/N): " ok
    [ "$ok" != "y" ] && echo "Annullato." && exit 1
fi

# Salva in .env (sostituisci o append)
touch "$ENV_FILE"
chmod 600 "$ENV_FILE"
if grep -q "^OPENROUTER_API_KEY=" "$ENV_FILE"; then
    # Rimuovi vecchia riga, appendi nuova (evita problemi di escaping sed)
    grep -v "^OPENROUTER_API_KEY=" "$ENV_FILE" > "$ENV_FILE.tmp"
    mv "$ENV_FILE.tmp" "$ENV_FILE"
fi
echo "OPENROUTER_API_KEY=$ORK" >> "$ENV_FILE"

echo "[ok] Key salvata in $ENV_FILE (chmod 600)"
echo

# Test connessione
source /opt/reel-agent/venv/bin/activate
python3 <<'PYEOF'
import os, sys
from dotenv import load_dotenv
load_dotenv('/opt/reel-agent/.env')
from openai import OpenAI
key = os.environ.get('OPENROUTER_API_KEY', '')
print(f"[test] key presente: {bool(key)} (len={len(key)}, prefix={key[:9]}...)")
c = OpenAI(base_url='https://openrouter.ai/api/v1', api_key=key)
for m in ['google/gemini-2.5-flash', 'google/gemini-2.0-flash-001', 'google/gemini-flash-1.5']:
    try:
        r = c.chat.completions.create(
            model=m,
            messages=[{'role': 'user', 'content': 'say OK'}],
            max_tokens=10,
        )
        print(f"✅ {m}: {r.choices[0].message.content[:40]}")
        sys.exit(0)
    except Exception as e:
        print(f"❌ {m}: {type(e).__name__}: {str(e)[:180]}")
sys.exit(1)
PYEOF
