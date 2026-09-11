# 回归监控

```powershell
cd F:\datapp\server
uv run python -m tools.regression_monitor --once
uv run python -m tools.regression_monitor
```

默认每 120 秒扫描一次。`Ctrl+C` 安全停止。结果追加到 `storage/regression-audit.jsonl`，状态保存到 `.tmp/regression-monitor-state.json`。监控器只执行离线验证，不触发 OpenCLI 或真实平台采集。

Windows 若 `uv` 默认缓存目录无权限，使用：

```powershell
$env:UV_CACHE_DIR = "F:\datapp\.tmp\uv-cache"
uv run python -m tools.regression_monitor
```
