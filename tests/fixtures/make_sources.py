"""Synthetic files for the other P1 sources (D3 §12.2). Every value is made up.

Deterministic and stdlib-only: the same arguments give the same bytes. Malformed variants are made by the tests,
by editing the output, so the generators stay simple.
"""

import json
import zlib
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime
from typing import Literal

from tests.fixtures.synthetic import SYNTHETIC_ACCOUNT, SYNTHETIC_NMI11, SYNTHETIC_SERIAL

CRLF = "\r\n"

# --- SAPN SUMMARY CSV (D3 §5.3) --------------------------------------------------------------------------------------


@dataclass(frozen=True)
class SummaryRow:
    """One row of the SUMMARY CSV: a period and four made-up numbers."""

    start: date
    end: date
    quality: str = "A"
    numbers: tuple[str, str, str, str] = ("1.000", "2.000", "3.000", "0.500")


def make_sapn_summary_csv(
    rows: Sequence[SummaryRow], *, nmi11: str = SYNTHETIC_NMI11, serial: str = SYNTHETIC_SERIAL
) -> bytes:
    """The 11-field, header-less CRLF format: NMI, serial, `kWh`, quality, start, end, four numbers, `kW`."""
    lines = [
        ",".join(
            (nmi11, serial, "kWh", row.quality, f"{row.start:%d/%m/%Y}", f"{row.end:%d/%m/%Y}", *row.numbers, "kW")
        )
        for row in rows
    ]
    return "".join(line + CRLF for line in lines).encode("utf-8")


# --- Retailer usage export (D3 §5.4) ---------------------------------------------------------------------------------


def _month(start: date, offset: int) -> date:
    index = start.year * 12 + start.month - 1 + offset
    return date(index // 12, index % 12 + 1, 1)


def make_retailer_usage_csv(
    months: int,
    *,
    account: str = SYNTHETIC_ACCOUNT,
    serial: str = SYNTHETIC_SERIAL,
    labels: Sequence[str] = ("Off Peak", "Peak", "Shoulder"),
    blank_last: bool = False,
    start: date = date(2025, 1, 1),
    register: str = "E1",
) -> bytes:
    """A portal usage export: one row per monthly read at midnight, oldest first, CRLF.

    `blank_last` leaves the newest row's register and total values empty, as the portal does.
    """
    header = ["Account Code", "Read Datetime", *(f"{register} ({serial} - {label})" for label in labels), "Total"]
    lines = [",".join(header)]
    for offset in range(months):
        read = datetime.combine(_month(start, offset), datetime.min.time())
        if blank_last and offset == months - 1:
            values = [""] * (len(labels) + 1)
        else:
            numbers = [100 + 10 * offset + index for index in range(len(labels))]
            values = [f"{number}.000" for number in numbers] + [f"{sum(numbers)}.000"]
        lines.append(",".join([account, f"{read:%Y-%m-%dT%H:%M:%S}", *values]))
    return "".join(line + CRLF for line in lines).encode("utf-8")


# --- Retailer transactions export (D3 §5.5) --------------------------------------------------------------------------


def make_retailer_txn_csv(entries: Sequence[tuple[str, datetime, str]]) -> bytes:
    """`Description,Date,Amount` then one CRLF row per `(description, when, amount)`."""
    lines = ["Description,Date,Amount"]
    lines.extend(f"{description},{when:%Y-%m-%dT%H:%M:%S},{amount}" for description, when, amount in entries)
    return "".join(line + CRLF for line in lines).encode("utf-8")


# --- PDF (D3 §5.3, §5.6) ---------------------------------------------------------------------------------------------

_PAGE_CONTENT = b"BT /F1 12 Tf 72 720 Td (Synthetic test page) Tj ET"


def _literal(value: bytes) -> bytes:
    escaped = value.replace(b"\\", b"\\\\").replace(b"(", b"\\(").replace(b")", b"\\)")
    return b"(" + escaped + b")"


def _date_string(value: str, encoding: Literal["literal", "hex", "utf16"]) -> bytes:
    match encoding:
        case "literal":
            return _literal(value.encode("latin-1"))
        case "hex":
            return b"<" + value.encode("latin-1").hex().upper().encode("ascii") + b">"
        case "utf16":
            return _literal(b"\xfe\xff" + value.encode("utf-16-be"))


def _xmp_packet(create_date: str) -> bytes:
    return (
        '<?xpacket begin="\ufeff" id="W5M0MpCehiHzreSzNTczkc9d"?>\n'
        '<x:xmpmeta xmlns:x="adobe:ns:meta/"><rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">'
        '<rdf:Description rdf:about="" xmlns:xmp="http://ns.adobe.com/xap/1.0/">'
        f"<xmp:CreateDate>{create_date}</xmp:CreateDate><xmp:CreatorTool>Synthetic</xmp:CreatorTool>"
        "</rdf:Description></rdf:RDF></x:xmpmeta>\n"
        '<?xpacket end="w"?>'
    ).encode()


def _stream(dictionary: bytes, body: bytes) -> bytes:
    return b"<< " + dictionary + b" /Length " + str(len(body)).encode() + b" >>\nstream\n" + body + b"\nendstream"


def make_pdf(
    *,
    creation_date: str | None,
    xmp: str | None = None,
    date_encoding: Literal["literal", "hex", "utf16"] = "literal",
    info_in_objstm: bool = False,
    xmp_compressed: bool = False,
    encrypted: bool = False,
    truncated: bool = False,
) -> bytes:
    """A minimal valid one-page PDF.

    `creation_date` is the Info dictionary's `/CreationDate` as written (e.g. `D:20250101120000+10'30'`), or `None`
    for none. `xmp` is the XMP packet's `xmp:CreateDate`, or `None` for no packet. `info_in_objstm` puts the Info
    dictionary in a Flate-compressed `/ObjStm` with a cross-reference stream (PDF 1.5 style); `xmp_compressed`
    deflates the XMP packet; `encrypted` adds an `/Encrypt` entry to the trailer; `truncated` cuts the file before
    its `%%EOF`.
    """
    info_parts = [b"/Producer (Synthetic PDF generator)"]
    if creation_date is not None:
        info_parts.append(b"/CreationDate " + _date_string(creation_date, date_encoding))
    info = b"<< " + b" ".join(info_parts) + b" >>"
    catalog = b"<< /Type /Catalog /Pages 2 0 R" + (b" /Metadata 6 0 R" if xmp is not None else b"") + b" >>"
    objects: dict[int, bytes] = {
        1: catalog,
        2: b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        3: b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R >>",
        4: _stream(b"", _PAGE_CONTENT),
    }
    if xmp is not None:
        packet = _xmp_packet(xmp)
        if xmp_compressed:
            objects[6] = _stream(b"/Type /Metadata /Subtype /XML /Filter /FlateDecode", zlib.compress(packet))
        else:
            objects[6] = _stream(b"/Type /Metadata /Subtype /XML", packet)
    encrypt = b" /Encrypt 9 0 R" if encrypted else b""
    ident = b" /ID [<00112233445566778899AABBCCDDEEFF> <00112233445566778899AABBCCDDEEFF>]"
    out = bytearray(b"%PDF-1.5\n%\xe2\xe3\xcf\xd3\n" if info_in_objstm else b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    offsets: dict[int, int] = {}

    def write(number: int, body: bytes) -> None:
        offsets[number] = len(out)
        out.extend(f"{number} 0 obj\n".encode() + body + b"\nendobj\n")

    if not info_in_objstm:
        objects[5] = info
        for number in sorted(objects):
            write(number, objects[number])
        size = max(objects) + 1
        xref_at = len(out)
        out.extend(f"xref\n0 {size}\n0000000000 65535 f \n".encode())
        for number in range(1, size):
            if number in offsets:
                out.extend(f"{offsets[number]:010d} 00000 n \n".encode())
            else:
                out.extend(b"0000000000 65535 f \n")
        trailer = f"trailer\n<< /Size {size} /Root 1 0 R /Info 5 0 R".encode() + ident + encrypt + b" >>\n"
        out.extend(trailer + f"startxref\n{xref_at}\n%%EOF\n".encode())
    else:
        for number in sorted(objects):
            write(number, objects[number])
        header = b"5 0 "
        objstm = zlib.compress(header + info)
        write(7, _stream(f"/Type /ObjStm /N 1 /First {len(header)} /Filter /FlateDecode".encode(), objstm))
        size = 9
        xref_at = len(out)
        rows = bytearray()
        for number in range(size):
            if number == 5:
                rows += bytes([2]) + (7).to_bytes(4, "big") + (0).to_bytes(2, "big")
            elif number == 8:
                rows += bytes([1]) + xref_at.to_bytes(4, "big") + (0).to_bytes(2, "big")
            elif number in offsets:
                rows += bytes([1]) + offsets[number].to_bytes(4, "big") + (0).to_bytes(2, "big")
            else:
                rows += bytes([0]) + (0).to_bytes(4, "big") + (65535 if number == 0 else 0).to_bytes(2, "big")
        dictionary = f"/Type /XRef /Size {size} /W [1 4 2] /Root 1 0 R /Info 5 0 R".encode() + ident + encrypt
        offsets[8] = xref_at
        out.extend(b"8 0 obj\n" + _stream(dictionary, bytes(rows)) + b"\nendobj\n")
        out.extend(f"startxref\n{xref_at}\n%%EOF\n".encode())
    data = bytes(out)
    if truncated:
        return data[: data.rindex(b"%%EOF")]
    return data


# --- CDR plan detail (D3 §5.7) ---------------------------------------------------------------------------------------


def make_cdr_plan_detail(
    *,
    plan_id: str = "SYN000001MRE1",
    last_updated: str = "2025-03-01T00:00:00Z",
    fuel_type: str = "ELECTRICITY",
    pretty: bool = False,
) -> bytes:
    """A CDR plan-detail response with `data`, `links` and `meta`. `pretty` changes only whitespace."""
    document = {
        "data": {
            "planId": plan_id,
            "lastUpdated": last_updated,
            "displayName": "Synthetic Saver",
            "description": "A made-up plan for tests.",
            "type": "MARKET",
            "fuelType": fuel_type,
            "brand": "synthetic",
            "brandName": "Synthetic Energy",
            "customerType": "RESIDENTIAL",
            "geography": {"includedPostcodes": [], "distributors": ["Synthetic Networks"]},
            "electricityContract": {
                "pricingModel": "SINGLE_RATE",
                "isFixed": False,
                "tariffPeriod": [
                    {
                        "displayName": "All year",
                        "startDate": "01-01",
                        "endDate": "12-31",
                        "dailySupplyCharges": "1.00",
                        "rateBlockUType": "singleRate",
                        "singleRate": {"rates": [{"unitPrice": "0.30"}]},
                    }
                ],
            },
        },
        "links": {"self": f"https://cdr.example.invalid/energy/plans/{plan_id}"},
        "meta": {},
    }
    if pretty:
        return json.dumps(document, indent=4).encode("utf-8") + b"\n"
    return json.dumps(document, separators=(",", ":")).encode("utf-8")
