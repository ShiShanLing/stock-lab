from __future__ import annotations

import argparse
import json
from pathlib import Path

from .research import run_research


def main() -> None:
    parser = argparse.ArgumentParser(description="Stock Lab 多策略样本外研究")
    parser.add_argument("--database", default="data/market/market.duckdb")
    parser.add_argument("--output", default="data/research")
    args = parser.parse_args()
    path = run_research(Path(args.database), Path(args.output))
    report = json.loads(path.read_text(encoding="utf-8"))
    print(f"研究报告：{path}")
    print(f"最终候选：{', '.join(report['finalists']) or '无'}")
    print(f"达到严格标准：{', '.join(report['reliable_candidates']) or '无'}")


if __name__ == "__main__":
    main()
