from app import buscar_autocompletado_en_ftp
import json

payload, err = buscar_autocompletado_en_ftp('BDA220')
print('ERR:', err)
if payload:
    print(json.dumps(payload['data'], ensure_ascii=False, indent=2))
else:
    print('NO PAYLOAD')
