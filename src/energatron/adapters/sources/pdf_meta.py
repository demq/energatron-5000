"""A PDF's creation date, without a PDF library (D3 §5.6). Pure and stdlib-only (`re`, `zlib`, `codecs`).

`creation_date(data)` searches the raw bytes, then every stream whose only filter is `/FlateDecode`, inflated (this
reaches an Info dictionary in a compressed `/ObjStm` and a compressed XMP packet). In each place it looks for
`/CreationDate`, then for XMP `xmp:CreateDate`, and stops at the first date that parses. At most `INFLATE_LIMIT`
bytes are inflated in total; past that the search stops.
"""

import codecs
import re
import zlib
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta, timezone
from typing import Final

INFLATE_LIMIT: Final = 64 * 1024 * 1024
"""The most bytes `creation_date` inflates over all streams."""

_CREATION_DATE: Final = re.compile(rb"/CreationDate\s*(?=[(<])")
_PDF_DATE: Final = re.compile(
    r"(?:D:)?(\d{4})(\d{2})?(\d{2})?(\d{2})?(\d{2})?(\d{2})?(?:(Z)|([+-])(\d{2})(?:'?(\d{2})'?)?)?"
)
_XMP: Final = re.compile(rb"xmp:CreateDate\s*(?:=\s*(?:\"([^\"]*)\"|'([^']*)')|>([^<]*)</xmp:CreateDate>)")
_ISO_PARTIAL: Final = re.compile(r"(\d{4})(?:-(\d{2}))?")
_STREAM: Final = re.compile(rb"(?<!end)stream\r?\n")
_FLATE_ONLY: Final = re.compile(rb"/Filter\s*(?:/FlateDecode(?![A-Za-z0-9])|\[\s*/FlateDecode\s*\])")
_ESCAPES: Final = {ord("n"): b"\n", ord("r"): b"\r", ord("t"): b"\t", ord("b"): b"\b", ord("f"): b"\f"}


def creation_date(data: bytes) -> datetime | None:
    """The PDF's creation date: aware when it gives an offset (`Z` is UTC), naive when not; `None` if none parses."""
    for place in _places(data):
        found = _from_info(place)
        if found is None:
            found = _from_xmp(place)
        if found is not None:
            return found
    return None


def _places(data: bytes) -> Iterator[bytes]:
    """The raw bytes, then each inflated Flate stream, until the inflate budget runs out."""
    yield data
    budget = INFLATE_LIMIT
    for match in _STREAM.finditer(data):
        dictionary = _dictionary_before(data, match.start())
        if dictionary is None or _FLATE_ONLY.search(dictionary) is None:
            continue
        end = data.find(b"endstream", match.end())
        body = data[match.end() : end if end >= 0 else len(data)]
        decompressor = zlib.decompressobj()
        try:
            inflated = decompressor.decompress(body, budget + 1)
        except zlib.error:
            continue
        if len(inflated) > budget:
            return
        budget -= len(inflated)
        yield inflated


def _dictionary_before(data: bytes, keyword: int) -> bytes | None:
    """The `<< … >>` dictionary that ends right before a `stream` keyword, nested dictionaries included."""
    end = data.rfind(b">>", 0, keyword)
    if end < 0 or data[end + 2 : keyword].strip():
        return None
    depth, position = 0, end + 2
    while position > 0:
        if data[position - 2 : position] == b">>":
            depth += 1
            position -= 2
        elif data[position - 2 : position] == b"<<":
            depth -= 1
            position -= 2
            if depth == 0:
                return data[position : end + 2]
        else:
            position -= 1
    return None


def _from_info(place: bytes) -> datetime | None:
    for match in _CREATION_DATE.finditer(place):
        value = _string_at(place, match.end())
        if value is None:
            continue
        found = _pdf_date(_decode_text(value))
        if found is not None:
            return found
    return None


def _string_at(data: bytes, start: int) -> bytes | None:
    """The PDF string object at `start`: a literal `(…)` or a hex `<…>`."""
    if data[start : start + 1] == b"<":
        end = data.find(b">", start)
        if end < 0:
            return None
        digits = re.sub(rb"\s", b"", data[start + 1 : end])
        if re.fullmatch(rb"[0-9A-Fa-f]*", digits) is None:
            return None
        if len(digits) % 2:
            digits += b"0"
        return bytes.fromhex(digits.decode("ascii"))
    return _literal(data, start + 1)


def _literal(data: bytes, position: int) -> bytes | None:
    out = bytearray()
    depth = 1
    while position < len(data):
        byte = data[position]
        position += 1
        if byte == ord("\\"):
            if position >= len(data):
                return None
            escaped = data[position]
            position += 1
            if escaped in _ESCAPES:
                out += _ESCAPES[escaped]
            elif escaped in b"01234567":
                digits = bytes([escaped])
                while len(digits) < 3 and position < len(data) and data[position] in b"01234567":
                    digits += bytes([data[position]])
                    position += 1
                out.append(int(digits, 8) & 0xFF)
            elif escaped == ord("\r"):
                if data[position : position + 1] == b"\n":
                    position += 1
            elif escaped != ord("\n"):
                out.append(escaped)  # \( \) \\ and any other character stand for themselves
        elif byte == ord("("):
            depth += 1
            out.append(byte)
        elif byte == ord(")"):
            depth -= 1
            if depth == 0:
                return bytes(out)
            out.append(byte)
        else:
            out.append(byte)
    return None


def _decode_text(value: bytes) -> str:
    if value.startswith(codecs.BOM_UTF16_BE):
        return value[2:].decode("utf-16-be", errors="replace")
    return value.decode("latin-1")


def _pdf_date(text: str) -> datetime | None:
    """`D:YYYY[MM[DD[HH[mm[SS]]]]][Z | ±hh'mm' | ±hh]`; missing parts default to 01, 01, 00, 00, 00."""
    match = _PDF_DATE.match(text.strip())
    if match is None:
        return None
    year, month, day, hour, minute, second, zulu, sign, off_h, off_m = match.groups()
    zone: timezone | None = None
    if zulu:
        zone = UTC
    elif sign:
        offset = timedelta(hours=int(off_h), minutes=int(off_m or 0))
        try:
            zone = timezone(-offset if sign == "-" else offset)
        except ValueError:
            return None
    try:
        return datetime(
            int(year),
            int(month or 1),
            int(day or 1),
            int(hour or 0),
            int(minute or 0),
            int(second or 0),
            tzinfo=zone,
        )
    except ValueError:
        return None


def _from_xmp(place: bytes) -> datetime | None:
    for match in _XMP.finditer(place):
        raw = next(group for group in match.groups() if group is not None)
        found = _iso_date(raw.decode("utf-8", errors="replace").strip())
        if found is not None:
            return found
    return None


def _iso_date(text: str) -> datetime | None:
    """An XMP date: ISO 8601 with an optional offset, or just `YYYY` or `YYYY-MM`."""
    partial = _ISO_PARTIAL.fullmatch(text)
    try:
        if partial is not None:
            return datetime(int(partial[1]), int(partial[2] or 1), 1)
        return datetime.fromisoformat(text)
    except ValueError:
        return None
