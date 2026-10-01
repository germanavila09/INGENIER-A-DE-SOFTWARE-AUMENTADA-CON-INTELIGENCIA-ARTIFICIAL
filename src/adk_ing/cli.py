"""CLI mínima:  adk-ing ls | cat | sync"""

from __future__ import annotations

import argparse
import sys

from .bucket import BucketReader
from .config import get_settings


def _human(n: int) -> str:
    size = float(n)
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024 or unit == "GB":
            return f"{size:.0f} {unit}" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024
    return f"{n} B"


def main(argv: list[str] | None = None) -> int:
    s = get_settings()
    parser = argparse.ArgumentParser(prog="adk-ing", description=f"Lee gs://{s.bucket}")
    parser.add_argument("--bucket", default=None, help=f"bucket (por defecto {s.bucket})")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_ls = sub.add_parser("ls", help="lista objetos")
    p_ls.add_argument("--prefix", default=None)
    p_ls.add_argument("--max", type=int, default=None)

    p_cat = sub.add_parser("cat", help="imprime un objeto de texto")
    p_cat.add_argument("name")

    p_sync = sub.add_parser("sync", help="descarga objetos nuevos o modificados")
    p_sync.add_argument("--prefix", default=None)
    p_sync.add_argument("--dest", default=None, help=f"carpeta local (por defecto {s.local_dir})")

    args = parser.parse_args(argv)
    reader = BucketReader(bucket_name=args.bucket)

    if args.cmd == "ls":
        objs = reader.list(prefix=args.prefix, max_results=args.max)
        for o in objs:
            print(f"{_human(o.size):>10}  {o.name}")
        print(f"\n{len(objs)} objeto(s) en gs://{reader.bucket_name}/{args.prefix or reader.prefix}")
    elif args.cmd == "cat":
        sys.stdout.write(reader.read_text(args.name))
    elif args.cmd == "sync":
        files = reader.sync(dest_dir=args.dest, prefix=args.prefix)
        for f in files:
            print(f"descargado  {f}")
        print(f"{len(files)} archivo(s) descargado(s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
