from __future__ import annotations

import argparse
import asyncio
import json
from concurrent.futures import ProcessPoolExecutor
from datetime import date
from typing import Any

import requests

from .data_sources import BaoStockProvider, EastmoneyProvider, StockIdentity
from .data_sources.baostock_provider import (
    fetch_baostock_worker,
    initialize_baostock_worker,
)
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
    provider: Any,
    start_date: str,
    end_date: str,
    workers: int,
    limit: int | None,
    retry_failed: bool = True,
) -> dict[str, int]:
    warehouse.ensure_daily_adjustment(provider.adjustment)
    stocks = warehouse.stocks_for_sync(
        start_date, end_date, retry_failed=retry_failed, limit=limit
    )
    total = len(stocks)
    if total == 0:
        print("没有需要同步的日线数据。", flush=True)
        return {"complete": 0, "failed": 0, "rows": 0}

    if not getattr(provider, "supports_parallel", True):
        workers = 1
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

    if hasattr(provider, "open"):
        await asyncio.to_thread(provider.open)
    try:
        await asyncio.gather(*(worker() for _ in range(workers)))
    finally:
        if hasattr(provider, "close"):
            await asyncio.to_thread(provider.close)
    return counters


async def sync_baostock_parallel(
    warehouse: MarketWarehouse,
    provider: BaoStockProvider,
    start_date: str,
    end_date: str,
    workers: int,
    limit: int | None,
    retry_failed: bool = True,
) -> dict[str, int]:
    warehouse.ensure_daily_adjustment(provider.adjustment)
    stocks = warehouse.stocks_for_sync(
        start_date, end_date, retry_failed=retry_failed, limit=limit
    )
    total = len(stocks)
    if total == 0:
        print("没有需要同步的日线数据。", flush=True)
        return {"complete": 0, "failed": 0, "rows": 0}
    process_count = max(1, min(workers, 4))
    print(
        f"开始BaoStock补偿同步：{total} 只，区间 {start_date}—{end_date}，进程 {process_count}",
        flush=True,
    )
    counters = {"complete": 0, "failed": 0, "rows": 0}
    loop = asyncio.get_running_loop()
    with ProcessPoolExecutor(
        max_workers=process_count,
        initializer=initialize_baostock_worker,
        initargs=(provider.adjustment,),
    ) as executor:
        async def fetch_one(
            stock: StockIdentity,
        ) -> tuple[StockIdentity, list[Any] | None, Exception | None]:
            try:
                bars = await loop.run_in_executor(
                    executor, fetch_baostock_worker, stock, start_date, end_date
                )
                return stock, bars, None
            except Exception as exc:
                return stock, None, exc

        pending: list[asyncio.Task[tuple[StockIdentity, list[Any] | None, Exception | None]]] = []
        for stock in stocks:
            warehouse.mark_sync_started(stock.secid, start_date, end_date)
            pending.append(asyncio.create_task(fetch_one(stock)))
        for future in asyncio.as_completed(pending):
            stock, bars, error = await future
            if error is None and bars is not None:
                counters["rows"] += warehouse.save_daily_bars(
                    stock.secid, bars, start_date=start_date, end_date=end_date
                )
                counters["complete"] += 1
            else:
                warehouse.mark_sync_failed(stock.secid, str(error))
                counters["failed"] += 1
            finished = counters["complete"] + counters["failed"]
            if finished == total or finished % 25 == 0:
                print(
                    f"补偿进度 {finished}/{total}：成功 {counters['complete']}，失败 {counters['failed']}，新增/更新 {counters['rows']} 行",
                    flush=True,
                )
    return counters


async def run_daily_sync(
    warehouse: MarketWarehouse,
    provider: Any,
    start_date: str,
    end_date: str,
    workers: int,
    limit: int | None,
    retry_failed: bool,
) -> dict[str, int]:
    if isinstance(provider, BaoStockProvider):
        return await sync_baostock_parallel(
            warehouse, provider, start_date, end_date, workers, limit, retry_failed
        )
    return await sync_daily(
        warehouse, provider, start_date, end_date, workers, limit, retry_failed
    )


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
    daily.add_argument(
        "--adjustment", choices=("qfq", "hfq", "none"), default="qfq",
        help="价格复权方式，回测默认使用前复权qfq",
    )
    daily.add_argument(
        "--provider", choices=("eastmoney", "baostock"), default="eastmoney",
        help="日线数据源；BaoStock为顺序补偿源",
    )
    daily.add_argument(
        "--skip-failed", action="store_true",
        help="暂时跳过失败和中断项，只处理从未同步的股票",
    )

    full = subparsers.add_parser("full", help="同步目录后断点同步历史日线")
    full.add_argument("--start", type=_date_arg, default="20180101")
    full.add_argument("--end", type=_date_arg, default=date.today().strftime("%Y%m%d"))
    full.add_argument("--workers", type=int, default=3, choices=range(1, 7))
    full.add_argument("--limit", type=int, default=None)
    full.add_argument(
        "--adjustment", choices=("qfq", "hfq", "none"), default="qfq",
        help="价格复权方式，回测默认使用前复权qfq",
    )
    full.add_argument(
        "--provider", choices=("eastmoney", "baostock"), default="eastmoney",
        help="日线数据源；BaoStock为顺序补偿源",
    )
    full.add_argument("--skip-failed", action="store_true")

    subparsers.add_parser("status", help="显示数据覆盖和同步进度")
    subparsers.add_parser("verify", help="校验重复、价格和覆盖范围")
    subparsers.add_parser("repair-invalid", help="把OHLC异常股票标为待补偿，不删除原数据")
    export = subparsers.add_parser("export", help="导出只读Parquet快照")
    export.add_argument("--version", required=True)
    return parser


async def async_main(args: argparse.Namespace) -> None:
    warehouse = MarketWarehouse(args.data_dir)
    adjustment = getattr(args, "adjustment", "qfq")
    provider_name = getattr(args, "provider", "eastmoney")
    provider = (
        BaoStockProvider(adjustment=adjustment)
        if provider_name == "baostock"
        else EastmoneyProvider(adjustment=adjustment)
    )
    if args.command == "catalog":
        await sync_catalog(warehouse, provider)
    elif args.command == "daily":
        await run_daily_sync(
            warehouse, provider, args.start, args.end, args.workers, args.limit,
            retry_failed=not args.skip_failed,
        )
    elif args.command == "full":
        await sync_catalog(warehouse, EastmoneyProvider(adjustment=adjustment))
        await run_daily_sync(
            warehouse, provider, args.start, args.end, args.workers, args.limit,
            retry_failed=not args.skip_failed,
        )
    elif args.command == "status":
        print(json.dumps(warehouse.summary(), ensure_ascii=False, indent=2))
    elif args.command == "verify":
        print(json.dumps(warehouse.verify(), ensure_ascii=False, indent=2))
    elif args.command == "repair-invalid":
        count = warehouse.mark_invalid_stocks_for_resync()
        print(f"已将 {count} 只OHLC异常股票标记为待补偿。")
    elif args.command == "export":
        path = warehouse.export_snapshot(args.version)
        print(f"快照已导出：{path}")


def main() -> None:
    args = build_parser().parse_args()
    asyncio.run(async_main(args))


if __name__ == "__main__":
    main()
