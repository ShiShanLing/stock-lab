from __future__ import annotations

import argparse
import asyncio
import json
from datetime import date

import requests

from .data_sources import EastmoneyProvider, StockIdentity
from .warehouse import MarketWarehouse


def _date_arg(value: str) -> str:
    text = value.replace("-", "")
    if len(text) != 8 or not text.isdigit():
        raise argparse.ArgumentTypeError("日期必须为 YYYYMMDD 或 YYYY-MM-DD")
    return text


async def sync_catalog(warehouse: MarketWarehouse, provider: EastmoneyProvider) -> int:
    print("正在获取当前上市A股目录…", flush=True)
    stocks = await provider.fetch_stock_catalog(on_page=warehouse.upsert_stocks)
    count = len(stocks)
    print(f"股票目录同步完成：{count} 只", flush=True)
    return count


async def sync_daily(
    warehouse: MarketWarehouse,
    provider: EastmoneyProvider,
    start_date: str,
    end_date: str,
    workers: int,
    limit: int | None,
) -> dict[str, int]:
    stocks = warehouse.stocks_for_sync(start_date, end_date, retry_failed=True, limit=limit)
    total = len(stocks)
    if total == 0:
        print("没有需要同步的日线数据。", flush=True)
        return {"complete": 0, "failed": 0, "rows": 0}

    print(
        f"开始同步日线：{total} 只，区间 {start_date}—{end_date}，并发 {workers}",
        flush=True,
    )
    queue: asyncio.Queue[StockIdentity] = asyncio.Queue()
    for stock in stocks:
        queue.put_nowait(stock)

    counters = {"complete": 0, "failed": 0, "rows": 0}
    lock = asyncio.Lock()

    async def worker() -> None:
        with requests.Session() as session:
            while True:
                try:
                    stock = queue.get_nowait()
                except asyncio.QueueEmpty:
                    return
                warehouse.mark_sync_started(stock.secid, start_date, end_date)
                try:
                    bars = await asyncio.to_thread(
                        provider.fetch_daily_bars,
                        session,
                        stock,
                        start_date,
                        end_date,
                    )
                    row_count = warehouse.save_daily_bars(
                        stock.secid, bars, start_date=start_date, end_date=end_date
                    )
                    async with lock:
                        counters["complete"] += 1
                        counters["rows"] += row_count
                except Exception as exc:
                    warehouse.mark_sync_failed(stock.secid, str(exc))
                    async with lock:
                        counters["failed"] += 1
                finally:
                    queue.task_done()
                finished = counters["complete"] + counters["failed"]
                if finished == total or finished % 25 == 0:
                    print(
                        f"进度 {finished}/{total}：成功 {counters['complete']}，失败 {counters['failed']}，新增/更新 {counters['rows']} 行",
                        flush=True,
                    )
                await asyncio.sleep(0.45 + 0.15 * (finished % 3))

    await asyncio.gather(*(worker() for _ in range(workers)))
    return counters


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Stock Lab A股本地数据仓库")
    parser.add_argument("--data-dir", default=None, help="市场数据目录，默认 data/market")
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("catalog", help="同步当前上市A股目录")
    daily = subparsers.add_parser("daily", help="断点同步历史日线")
    daily.add_argument("--start", type=_date_arg, default="20180101")
    daily.add_argument("--end", type=_date_arg, default=date.today().strftime("%Y%m%d"))
    daily.add_argument("--workers", type=int, default=3, choices=range(1, 7))
    daily.add_argument("--limit", type=int, default=None, help="仅同步前N只，用于验证")

    full = subparsers.add_parser("full", help="同步目录后断点同步历史日线")
    full.add_argument("--start", type=_date_arg, default="20180101")
    full.add_argument("--end", type=_date_arg, default=date.today().strftime("%Y%m%d"))
    full.add_argument("--workers", type=int, default=3, choices=range(1, 7))
    full.add_argument("--limit", type=int, default=None)

    subparsers.add_parser("status", help="显示数据覆盖和同步进度")
    subparsers.add_parser("verify", help="校验重复、价格和覆盖范围")
    export = subparsers.add_parser("export", help="导出只读Parquet快照")
    export.add_argument("--version", required=True)
    return parser


async def async_main(args: argparse.Namespace) -> None:
    warehouse = MarketWarehouse(args.data_dir)
    provider = EastmoneyProvider()
    if args.command == "catalog":
        await sync_catalog(warehouse, provider)
    elif args.command == "daily":
        await sync_daily(
            warehouse, provider, args.start, args.end, args.workers, args.limit
        )
    elif args.command == "full":
        await sync_catalog(warehouse, provider)
        await sync_daily(
            warehouse, provider, args.start, args.end, args.workers, args.limit
        )
    elif args.command == "status":
        print(json.dumps(warehouse.summary(), ensure_ascii=False, indent=2))
    elif args.command == "verify":
        print(json.dumps(warehouse.verify(), ensure_ascii=False, indent=2))
    elif args.command == "export":
        path = warehouse.export_snapshot(args.version)
        print(f"快照已导出：{path}")


def main() -> None:
    args = build_parser().parse_args()
    asyncio.run(async_main(args))


if __name__ == "__main__":
    main()
