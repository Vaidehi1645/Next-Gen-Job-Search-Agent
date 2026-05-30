#!/usr/bin/env python3
"""Small helper: convert resume PDF to plain text resume.txt.

Usage:
  python convert_resume.py --in resume.pdf --out resume.txt

The script prefers `pdfplumber` for text PDFs. If that fails and
`pdf2image` + `pytesseract` are installed (with Poppler/Tesseract available
on PATH), it will fall back to OCR.
"""
from __future__ import annotations

import argparse
import sys
import warnings
from pathlib import Path


def extract_with_pdfplumber(path: Path) -> str | None:
    try:
        import pdfplumber

        pages = []
        with pdfplumber.open(path) as pdf:
            for p in pdf.pages:
                pages.append(p.extract_text() or "")
        text = "\n\n".join(pages).strip()
        return text if text else None
    except Exception as exc:
        warnings.warn(f"pdfplumber extraction failed: {exc}")
        return None


def extract_with_ocr(path: Path) -> str | None:
    try:
        from pdf2image import convert_from_path
        import pytesseract

        images = convert_from_path(str(path))
        pages = [pytesseract.image_to_string(img) for img in images]
        text = "\n\n".join(pages).strip()
        return text if text else None
    except Exception as exc:
        warnings.warn(f"OCR extraction failed: {exc}")
        return None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Convert resume PDF to resume.txt")
    parser.add_argument("--in", dest="in_path", default="resume.pdf", help="Input PDF path (default: resume.pdf)")
    parser.add_argument("--out", dest="out_path", default="resume.txt", help="Output text path (default: resume.txt)")
    args = parser.parse_args(argv)

    in_path = Path(args.in_path)
    out_path = Path(args.out_path)
    if not in_path.exists():
        print(f"Input file not found: {in_path}")
        return 2

    text = extract_with_pdfplumber(in_path)
    if not text:
        print("pdfplumber did not extract text — attempting OCR fallback...")
        text = extract_with_ocr(in_path)

    if not text:
        print("Failed to extract text from PDF. Install pdfplumber for text PDFs or pdf2image+pytesseract plus Poppler/Tesseract for OCR.")
        return 3

    out_path.write_text(text, encoding="utf-8")
    print(f"Wrote: {out_path} ({len(text)} chars)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
