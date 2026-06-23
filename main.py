"""
Mini-serviço que expõe o twscrape via HTTPS.

Não reescreve o twscrape: apenas instala ele (ver requirements.txt) e abre
uma "porta" HTTP com três endereços:
  GET /health           -> diz se o serviço está de pé (sem segredo)
  GET /trends?category= -> assuntos em alta no X/Twitter
  GET /search?q=        -> tweets de uma busca

As contas do X são carregadas na partida, a partir da variável de ambiente
ACCOUNTS_JSON (lista de {"username","cookies"}). Nada de senha em código.
"""

import json
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI, Header, HTTPException, Query
from twscrape import API, gather

# ------------------------------------------------------------------ config
WRAPPER_API_KEY = os.environ.get("WRAPPER_API_KEY", "").strip()
ACCOUNTS_JSON = os.environ.get("ACCOUNTS_JSON", "").strip()
# Caminho do banco de contas. Local: aponte para fora do OneDrive (que trava
# arquivos sqlite). No Railway o padrão "accounts.db" funciona normalmente.
DB_PATH = os.environ.get("DB_PATH", "accounts.db").strip() or "accounts.db"

api = API(DB_PATH)


def require_key(x_api_key: str | None) -> None:
    """Bloqueia se a chave não bater. Em modo local (sem chave configurada)
    a checagem é desligada para facilitar o teste."""
    if not WRAPPER_API_KEY:
        return  # modo dev: sem chave, libera
    if x_api_key != WRAPPER_API_KEY:
        raise HTTPException(status_code=401, detail="x-api-key invalida ou ausente")


async def load_accounts() -> None:
    """Lê as contas do ambiente e adiciona ao pool do twscrape."""
    if not ACCOUNTS_JSON:
        print("[startup] ACCOUNTS_JSON vazio — sem contas. /trends e /search vao falhar ate configurar.")
        return
    try:
        accounts = json.loads(ACCOUNTS_JSON)
    except Exception as e:
        print(f"[startup] ACCOUNTS_JSON invalido (nao e JSON): {e}")
        return

    for acc in accounts:
        username = acc.get("username")
        cookies = acc.get("cookies")
        if not username or not cookies:
            print(f"[startup] conta ignorada (faltando username/cookies): {acc}")
            continue
        try:
            if hasattr(api.pool, "add_account_cookies"):
                await api.pool.add_account_cookies(username, cookies)
            else:
                await api.pool.add_account(username, "x", "x@example.com", "x", cookies=cookies)
            print(f"[startup] conta '{username}' adicionada.")
        except Exception as e:
            # normalmente "ja existe" — seguimos em frente
            print(f"[startup] conta '{username}' nao adicionada ({e}).")


@asynccontextmanager
async def lifespan(app: FastAPI):
    await load_accounts()
    yield


app = FastAPI(title="twscrape-trends", lifespan=lifespan)


# ------------------------------------------------------------- serializers
def trend_to_dict(t) -> dict:
    d = {}
    for k in ("name", "metaDescription", "domainContext", "trendUrl", "rank", "tweet_count"):
        v = getattr(t, k, None)
        if v is not None:
            d[k] = v
    return d or {"raw": str(t)}


def tweet_to_dict(tw) -> dict:
    try:
        return {
            "id": tw.id,
            "url": getattr(tw, "url", None),
            "date": str(getattr(tw, "date", "")),
            "username": getattr(getattr(tw, "user", None), "username", None),
            "content": getattr(tw, "rawContent", None),
            "likes": getattr(tw, "likeCount", None),
            "retweets": getattr(tw, "retweetCount", None),
            "replies": getattr(tw, "replyCount", None),
            "views": getattr(tw, "viewCount", None),
        }
    except Exception:
        return {"raw": str(tw)}


# ----------------------------------------------------------------- rotas
@app.get("/health")
async def health():
    return {"status": "ok", "has_accounts": bool(ACCOUNTS_JSON), "auth_enabled": bool(WRAPPER_API_KEY)}


# IDs das abas de trends. O twscrape tem esses IDs com o padding base64 ERRADO
# (falta o '='), o que faz o Twitter rejeitar trending/news/entertainment com
# "Internal server error". Aqui guardamos o ID base e corrigimos o padding.
TREND_IDS = {
    "trending": "VGltZWxpbmU6DAC2CwABAAAACHRyZW5kaW5nAAA",
    "news": "VGltZWxpbmU6DAC2CwABAAAABG5ld3MAAA",
    "sport": "VGltZWxpbmU6DAC2CwABAAAABnNwb3J0cwAA",
    "entertainment": "VGltZWxpbmU6DAC2CwABAAAADWVudGVydGFpbm1lbnQAAA",
}


def padded_trend_id(category: str) -> str:
    """Resolve a categoria para o ID com padding base64 correto (multiplo de 4)."""
    raw = TREND_IDS.get(category, category)
    return raw + "=" * (-len(raw) % 4)


@app.get("/trends")
async def trends(
    category: str = Query("trending", description="trending | news | sport | entertainment"),
    x_api_key: str | None = Header(default=None),
):
    require_key(x_api_key)
    # passa o ID ja corrigido; o twscrape usa como esta (nao passa pelo map quebrado dele)
    items = await gather(api.trends(padded_trend_id(category)))
    return {"category": category, "count": len(items), "trends": [trend_to_dict(t) for t in items]}


@app.get("/search")
async def search(
    q: str = Query(..., description="termo de busca do X/Twitter"),
    limit: int = Query(20, ge=1, le=100),
    product: str = Query("Top", description="Top | Latest | Media"),
    x_api_key: str | None = Header(default=None),
):
    require_key(x_api_key)
    items = await gather(api.search(q, limit=limit, kv={"product": product}))
    return {"query": q, "product": product, "count": len(items), "tweets": [tweet_to_dict(t) for t in items]}


@app.get("/curate")
async def curate(
    topics: str = Query(..., description="temas separados por virgula, ex: growth,vendas b2b,IA"),
    per_topic: int = Query(10, ge=1, le=50),
    min_engagement: int = Query(0, ge=0, description="curtidas+retweets minimos"),
    lang: str = Query("pt", description="idioma (pt, en, ...). vazio = qualquer"),
    x_api_key: str | None = Header(default=None),
):
    """Busca os tweets populares de cada tema do ICP e devolve ranqueado por engajamento.
    Pensado para o agente curador: em vez do trending generico, traz o que esta
    bombando nos assuntos que importam."""
    require_key(x_api_key)
    topic_list = [t.strip() for t in topics.split(",") if t.strip()]
    results = []
    for t in topic_list:
        # frase exata entre aspas se tiver mais de uma palavra (mais relevante)
        phrase = f'"{t}"' if " " in t else t
        query = phrase
        if lang:
            query += f" lang:{lang}"
        # deixa o proprio Twitter filtrar por engajamento (mais sinal, menos ruido)
        if min_engagement > 0:
            query += f" min_faves:{min_engagement}"
        items = await gather(api.search(query, limit=per_topic, kv={"product": "Top"}))
        for tw in items:
            d = tweet_to_dict(tw)
            engagement = (d.get("likes") or 0) + (d.get("retweets") or 0)
            if engagement >= min_engagement:
                d["topic"] = t
                d["engagement"] = engagement
                results.append(d)
    results.sort(key=lambda x: x.get("engagement", 0), reverse=True)
    return {"topics": topic_list, "count": len(results), "results": results}
