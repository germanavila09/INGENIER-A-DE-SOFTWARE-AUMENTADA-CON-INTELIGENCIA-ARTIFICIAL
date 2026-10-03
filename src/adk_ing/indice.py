"""Índice de búsqueda en memoria sobre los documentos del bucket.

- Cada documento se descarga y se extrae una sola vez por versión (generación del objeto).
- Cada vez que se busca, si pasaron más de INDICE_TTL_SEG segundos, se vuelve a listar
  el bucket: los documentos nuevos o reemplazados se indexan y los borrados se quitan.
  Así el agente ve lo que se vaya ingestando sin reiniciar ADK Web.
- La búsqueda es léxica (BM25) sobre fragmentos de ~1 000 caracteres, sin tildes ni
  mayúsculas y sin palabras vacías en español/inglés.
"""

from __future__ import annotations

import math
import os
import re
import threading
import time
import unicodedata
from collections import Counter
from dataclasses import dataclass

from .documentos import Documento, FormatoNoSoportado, es_soportado, extraer

MAX_BYTES = int(os.getenv("DOCS_MAX_MB", "50")) * 1024 * 1024
TAM_FRAGMENTO = 1000
SOLAPE = 200

_VACIAS = set(
    """
    a al algo algun alguna algunas alguno algunos ante antes aqui asi aun cada como con contra cual
    cuales cuando de del desde donde dos e el ella ellas ellos en entre era es esa esas ese eso esos
    esta estan estas este esto estos fue fueron ha han hasta hay la las le les lo los mas me mi mis
    mucho muy ni no nos o otra otras otro otros para pero poco por porque que quien quienes se sea
    segun ser si sin sobre su sus tambien tan tanto te tiene tienen todo todos tu tus u un una unas
    uno unos ya y yo
    the of and or to in on for with by is are was were be this that these those it as at from an
    """.split()
)


def tokens(texto: str) -> list[str]:
    sin_tildes = unicodedata.normalize("NFKD", texto.lower())
    sin_tildes = "".join(c for c in sin_tildes if not unicodedata.combining(c))
    return [t for t in re.findall(r"[a-z0-9]+", sin_tildes) if len(t) > 1 and t not in _VACIAS]


@dataclass
class Fragmento:
    documento: str
    seccion: int
    etiqueta: str
    texto: str
    frecuencias: Counter
    largo: int


@dataclass
class _Entrada:
    firma: str
    documento: Documento | None
    error: str | None
    tamano: int


class IndiceDocumentos:
    def __init__(self, fuente, ttl_segundos: float | None = None) -> None:
        self.fuente = fuente
        self.ttl = float(os.getenv("INDICE_TTL_SEG", "30")) if ttl_segundos is None else ttl_segundos
        self._entradas: dict[str, _Entrada] = {}
        self._fragmentos: list[Fragmento] = []
        self._df: Counter = Counter()
        self._largo_medio = 1.0
        self._ultimo_listado = 0.0
        self._lock = threading.RLock()

    # ------------------------------------------------------------ actualizar
    def refrescar(self, forzar: bool = False) -> dict:
        """Sincroniza el índice con la fuente. Devuelve qué cambió."""
        with self._lock:
            if not forzar and time.monotonic() - self._ultimo_listado < self.ttl:
                return {"nuevos": [], "actualizados": [], "eliminados": [], "errores": []}

            vistos: set[str] = set()
            cambios = {"nuevos": [], "actualizados": [], "eliminados": [], "errores": []}
            for obj in self.fuente.iter_objects():
                if not es_soportado(obj.name):
                    continue
                vistos.add(obj.name)
                previa = self._entradas.get(obj.name)
                if previa and previa.firma == obj.firma:
                    continue
                entrada = self._indexar(obj.name, obj.firma, obj.size)
                cambios["actualizados" if previa else "nuevos"].append(obj.name)
                if entrada.error:
                    cambios["errores"].append({"documento": obj.name, "error": entrada.error})

            for nombre in list(self._entradas):
                if nombre not in vistos:
                    del self._entradas[nombre]
                    cambios["eliminados"].append(nombre)

            if cambios["nuevos"] or cambios["actualizados"] or cambios["eliminados"]:
                self._reconstruir()
            self._ultimo_listado = time.monotonic()
            return cambios

    def _indexar(self, nombre: str, firma: str, tamano: int) -> _Entrada:
        if tamano > MAX_BYTES:
            entrada = _Entrada(firma, None, f"Supera {MAX_BYTES // 2**20} MB; no se indexa.", tamano)
        else:
            try:
                doc = extraer(nombre, self.fuente.read_bytes(nombre))
                entrada = _Entrada(firma, doc, None, tamano)
            except FormatoNoSoportado as exc:
                entrada = _Entrada(firma, None, str(exc), tamano)
            except Exception as exc:  # archivo dañado, permisos, etc.
                entrada = _Entrada(firma, None, f"{type(exc).__name__}: {exc}"[:300], tamano)
        self._entradas[nombre] = entrada
        return entrada

    def _reconstruir(self) -> None:
        fragmentos: list[Fragmento] = []
        for nombre, entrada in sorted(self._entradas.items()):
            if not entrada.documento:
                continue
            extra_nombre = tokens(nombre)  # el nombre del archivo también cuenta
            for sec in entrada.documento.secciones:
                for trozo in _trocear(sec.texto):
                    tk = tokens(trozo) + extra_nombre
                    if tk:
                        fragmentos.append(
                            Fragmento(nombre, sec.numero, sec.etiqueta, trozo, Counter(tk), len(tk))
                        )
        df: Counter = Counter()
        for f in fragmentos:
            df.update(f.frecuencias.keys())
        self._fragmentos = fragmentos
        self._df = df
        self._largo_medio = (sum(f.largo for f in fragmentos) / len(fragmentos)) if fragmentos else 1.0

    # --------------------------------------------------------------- consultar
    def buscar(self, consulta: str, k: int = 8, documento: str | None = None) -> list[dict]:
        self.refrescar()
        terminos = list(dict.fromkeys(tokens(consulta)))
        if not terminos:
            return []
        with self._lock:
            n = len(self._fragmentos)
            k1, b = 1.5, 0.75
            puntuados = []
            for f in self._fragmentos:
                if documento and f.documento != documento:
                    continue
                score = 0.0
                for t in terminos:
                    tf = f.frecuencias.get(t)
                    if not tf:
                        continue
                    idf = math.log(1 + (n - self._df[t] + 0.5) / (self._df[t] + 0.5))
                    score += idf * tf * (k1 + 1) / (tf + k1 * (1 - b + b * f.largo / self._largo_medio))
                if score > 0:
                    puntuados.append((score, f))
            puntuados.sort(key=lambda x: x[0], reverse=True)
            return [
                {
                    "documento": f.documento,
                    "ubicacion": f.etiqueta,
                    "seccion": f.seccion,
                    "puntaje": round(s, 2),
                    "fragmento": f.texto,
                }
                for s, f in puntuados[:k]
            ]

    def documento(self, nombre: str) -> Documento:
        """Documento extraído (desde la caché si ya está indexado en su versión actual)."""
        with self._lock:
            entrada = self._entradas.get(nombre)
            if entrada and entrada.documento:
                return entrada.documento
            if entrada and entrada.error:
                raise ValueError(entrada.error)
        # Fuera del índice (otro prefijo o recién subido): se extrae al vuelo.
        return extraer(nombre, self.fuente.read_bytes(nombre))

    def estado(self) -> list[dict]:
        with self._lock:
            out = []
            for nombre, e in sorted(self._entradas.items()):
                d = e.documento
                out.append(
                    {
                        "documento": nombre,
                        "formato": d.formato if d else None,
                        "secciones": len(d.secciones) if d else 0,
                        "caracteres": d.caracteres if d else 0,
                        "aviso": d.aviso if d else None,
                        "error": e.error,
                    }
                )
            return out


def _trocear(texto: str) -> list[str]:
    if len(texto) <= TAM_FRAGMENTO:
        return [texto] if texto.strip() else []
    trozos, inicio = [], 0
    while inicio < len(texto):
        fin = min(inicio + TAM_FRAGMENTO, len(texto))
        if fin < len(texto):  # cortar en un espacio para no partir palabras
            corte = texto.rfind(" ", inicio + TAM_FRAGMENTO // 2, fin)
            fin = corte if corte > 0 else fin
        trozos.append(texto[inicio:fin].strip())
        if fin >= len(texto):
            break
        nuevo = max(fin - SOLAPE, inicio + 1)
        espacio = texto.find(" ", nuevo, fin)  # empezar en palabra completa
        inicio = espacio + 1 if espacio != -1 else nuevo
    return [t for t in trozos if t]
