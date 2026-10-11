#!/bin/bash
# Copia do servidor de produção só os arquivos de código (nunca estado, logs
# nem cache de sessão) para a pasta servidor/ deste pacote.
set -e
AQUI="$(cd "$(dirname "$0")" && pwd)"
ORIGEM="$AQUI/../../mcps/mcp-pje-tjma"
for f in server.py pje_web_client.py config.py models.py cofre.py; do
  cp "$ORIGEM/$f" "$AQUI/servidor/$f"
done
echo "servidor/ atualizado a partir de $ORIGEM"
