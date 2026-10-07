"""Limpeza de texto do Instagram: remoção de CTA e normalização de título.

Extraído de `workers.ig_worker` (audit F1/SRP, responsabilidade 1). Puro:
regex e strings de entrada/saída, sem IO, sem settings, sem dependências
pesadas — é o módulo mais barato de testar de todo o pipeline.
"""

from __future__ import annotations

import re

_CTA_PATTERNS = (
    r"segue(?:-me| me)?(?: aqui)? (?:para|pra)(?: não| nao)? perder",
    r"j[aá] me segue",
    r"siga para mais",
    r"salva(?: esse| este| o) vídeo",
    r"salv(e|a) para fazer depois",
    r"compartilh(a|e) com (?:seus|teus|os) amigos",
    r"link na bio",
    r"curte e compartilha",
    r"ativa o sininho",
    r"coment(?:a|e)[^.!?]*que eu te mando",
    # Inglês. Metade do que o usuário salva é de conta gringa, e o vocabulário
    # só existia em português.
    #
    # Os padrões são estreitos de propósito. `follow` sozinho apagaria "follow
    # the steps below" e "follow this pattern", que é exatamente o conteúdo que
    # esta base existe para guardar: um CTA que sobrevive é ruído, uma instrução
    # apagada é perda.
    r"follow(?:ing)? (?:me|us)(?=\W*(?:on\b|for\b|@|$|[.!?]))",
    r"follow @",
    r"follow (?:for|to get) more",
    r"still not following",
    r"save (?:this|the) (?:post|video|reel|one)",
    r"share (?:this|it) with (?:a|your|ur)",
    r"tag (?:a|your) (?:friend|buddy)",
    r"link in (?:the )?bio",
    r"(?:double.?tap|smash that)",
    r"(?:comment|drop a comment)[^.!?]*(?:below|and i(?:'|’)?ll|to get)",
    r"turn on (?:the )?notifications",
)
# Fim de frase é `.!?` SEGUIDO de espaço ou fim do texto -- não qualquer ponto.
_TERM = r"[.!?](?=\s|$)"
_NAO_TERM = r"(?:(?!" + _TERM + r").)"

_CTA_SENTENCE_RE = re.compile(
    _NAO_TERM + r"*(?:" + "|".join(_CTA_PATTERNS) + r")" + _NAO_TERM + r"*" + _TERM,
    re.IGNORECASE | re.DOTALL,
)


def strip_cta(text: str) -> str:
    """Remove sentences containing Instagram call-to-action phrases.

    A sentence is delimited by `.`, `!` or `?`. Only the sentence that
    contains the CTA is removed; surrounding content is preserved. Never
    raises and returns input unchanged when no CTA pattern matches.
    """
    if not text:
        return text
    return _CTA_SENTENCE_RE.sub("", text).strip()


# O mesmo vocabulário, mas casando até o fim da string em vez de exigir `.!?`.
_CTA_TAIL_RE = re.compile(
    _NAO_TERM + r"*(?:" + "|".join(_CTA_PATTERNS) + r")" + _NAO_TERM + r"*$",
    re.IGNORECASE | re.DOTALL,
)
_CTA_QUALQUER_RE = re.compile("|".join(_CTA_PATTERNS), re.IGNORECASE)
_HASHTAG_RE = re.compile(r"#\S+")
_WORD_RE = re.compile(r"\w{2,}", re.UNICODE)
# Abaixo disto não é título, é pontuação/lixo sobrando depois de tirar CTA e
# hashtags -- ver clean_title.
_MIN_WORDS_FOR_TITLE = 2


def clean_title(raw: str | None) -> str | None:
    """Limpa o título vindo da legenda do Instagram, ou devolve None.

    CTA na PRIMEIRA frase condena o título inteiro; depois dela, basta aparar.
    Se o que sobrar tiver menos de duas palavras, não é título -- devolve
    None, e o `ingest` cai no fallback que já existe
    (`title or resumo[:80]`), deixando a primeira linha do resumo assumir.
    """
    if not raw:
        return None
    if _CTA_QUALQUER_RE.search(re.split(_TERM, raw, maxsplit=1)[0]):
        return None
    t = _CTA_TAIL_RE.sub("", strip_cta(raw))
    t = _HASHTAG_RE.sub("", t)
    t = re.sub(r"\s+", " ", t).strip(" -–—|·•,;:")
    return t if len(_WORD_RE.findall(t)) >= _MIN_WORDS_FOR_TITLE else None
