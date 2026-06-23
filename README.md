# twscrape-trends

Mini-serviço HTTP que expõe o [twscrape](https://github.com/vladkens/twscrape)
para o n8n e para o agente curador consultarem assuntos em alta no X/Twitter.

Não reescreve o twscrape: instala ele como dependência e abre uma "porta" HTTP.

## Endereços

| Método | Caminho | O que faz |
|--------|---------|-----------|
| GET | `/health` | Diz se o serviço está de pé (não exige chave) |
| GET | `/trends?category=news` | Assuntos em alta |
| GET | `/search?q=termo&limit=20` | Tweets de uma busca |

`/trends` e `/search` exigem o cabeçalho `x-api-key` igual à variável `WRAPPER_API_KEY`.

## Variáveis de ambiente

- `WRAPPER_API_KEY` — chave secreta que protege o serviço.
- `ACCOUNTS_JSON` — contas descartáveis do X em JSON (ver `.env.example`).

Se `WRAPPER_API_KEY` estiver vazia, a checagem de chave é desligada (modo local de teste).

## Rodar local (Windows)

```
pip install -r requirements.txt
uvicorn main:app --host 127.0.0.1 --port 8000
```

Depois abra http://127.0.0.1:8000/health

## Deploy (Railway)

1. Suba este repositório no GitHub.
2. No Railway: New Project -> Deploy from GitHub repo -> selecione este repo.
3. Em Variables, adicione `WRAPPER_API_KEY` e `ACCOUNTS_JSON`.
4. O Railway usa o `Dockerfile` automaticamente.
