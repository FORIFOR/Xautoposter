import argparse
import os
from pathlib import Path

import uvicorn

from .server import create_app


def main():
    parser = argparse.ArgumentParser(description="Xautoposter · ローカルX分析（読み取り専用）")
    parser.add_argument("--port", type=int, default=8790)
    parser.add_argument("--data-dir", type=Path, default=Path(os.environ.get("XAUTOP_DATA_DIR", "data")))
    args = parser.parse_args()
    # One process owns the collector. Never run multiple collection schedulers.
    print(f"Xautoposter: http://127.0.0.1:{args.port} · Xへの書き込み機能なし")
    uvicorn.run(create_app(args.data_dir), host="127.0.0.1", port=args.port, workers=1)


if __name__ == "__main__":
    main()
