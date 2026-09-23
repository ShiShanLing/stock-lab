# Stock Lab

独立的个人选股、策略回测与模拟盘实验项目。当前版本使用明确标记的确定性测试行情，不连接真实交易。

## 已实现

- 自然语言条件转换为结构化选股规则
- 低估值、放量突破、均线多头、近期强势和低波动模板
- 条件筛选、排序、入选原因
- 策略保存
- 无未来数据泄漏的日线轮动回测
- 总收益、年化收益、最大回撤、胜率、交易周期和收益曲线
- SQLite 持久化和 FastAPI 接口
- React 页面

## 本地启动

后端：

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e 'backend[dev]'
stock-lab-api
```

前端：

```bash
cd frontend
npm install
npm run dev
```

打开 `http://127.0.0.1:5176/stock/`。后端默认运行在 `127.0.0.1:8010`。

## 数据说明

当前 `MARKET_DATA_PROVIDER=demo`，启动时会生成可重复的测试股票和日线数据。页面中的证券名称均带“模拟”字样，不能被误认为真实行情。接入东方财富等数据源时，应新增适配器，不改动筛选和回测核心。

## 测试与构建

```bash
python -m unittest discover -s backend/tests -v
cd frontend && npm run build
```

## 本地历史数据仓库

长期执行计划与字段范围见 `docs/DATA_PIPELINE_PLAN.md`。数据仓库使用 DuckDB，运行时文件位于 `data/market/`，不会提交 Git。

```bash
# 同步股票目录
stock-lab-data catalog

# 从2018年开始断点同步日线（默认低并发）
stock-lab-data daily --start 20180101 --workers 2

# 查看进度与数据覆盖
stock-lab-data status

# 校验重复、价格结构和覆盖范围
stock-lab-data verify

# 全量完成后导出不可覆盖的版本化Parquet快照
stock-lab-data export --version CN_A_20180101_YYYYMMDD_v1
```

下载器会记录每只股票的完成、失败和重试状态；进程中断后重新执行同一命令即可继续。回测过程中不访问外部行情接口。
