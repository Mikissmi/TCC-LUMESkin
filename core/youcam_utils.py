CHAVES_NOTA = ('ui_score', 'raw_score', 'score', 'value', 'severity')


def _numero(valor):
    return float(valor) if isinstance(valor, (int, float)) and not isinstance(valor, bool) else None


def _nota_de(item):
    direto = _numero(item)
    if direto is not None:
        return direto
    if not isinstance(item, dict):
        return None
    for chave in CHAVES_NOTA:
        nota = _numero(item.get(chave))
        if nota is not None:
            return nota
    if isinstance(item.get('whole'), dict):
        nota = _nota_de(item['whole'])
        if nota is not None:
            return nota
    regioes = [_nota_de(v) for v in item.values() if isinstance(v, dict)]
    regioes = [n for n in regioes if n is not None]
    return sum(regioes) / len(regioes) if regioes else None


def _extract_score(value, key):
    nomes = (key, 'hd_' + key)
    if isinstance(value, dict):
        if value.get('type') in nomes:
            nota = _nota_de(value)
            if nota is not None:
                return nota
        for nome in nomes:
            if nome in value:
                nota = _nota_de(value[nome])
                if nota is not None:
                    return nota
        for item in value.values():
            found = _extract_score(item, key)
            if found is not None:
                return found
    elif isinstance(value, list):
        for item in value:
            found = _extract_score(item, key)
            if found is not None:
                return found
    return None