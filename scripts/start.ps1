<#
  chorus-mw 启停脚本
  -------------------
  用法：
    powershell -ExecutionPolicy Bypass -File scripts\start.ps1            # 启动
    powershell -ExecutionPolicy Bypass -File scripts\start.ps1 -Stop      # 停止
    powershell -ExecutionPolicy Bypass -File scripts\start.ps1 -Status    # 看状态

  必填环境变量：
    CHORUS_RVC_ROOT   RVC 整合包根目录（里面有 runtime\python.exe、ffmpeg.exe、assets\weights）
  可选：
    CHORUS_PYTHON     跑服务端的 Python，默认从 PATH 找 python
    CHORUS_PORT       监听端口，默认 3333
    CHORUS_HOST       监听地址，默认 127.0.0.1
#>
[CmdletBinding()]
param(
    [switch]$Stop,
    [switch]$Status
)

$ErrorActionPreference = 'Continue'
$root = Split-Path -Parent $PSScriptRoot
$port = if ($env:CHORUS_PORT) { [int]$env:CHORUS_PORT } else { 3333 }
$logDir = Join-Path $root '_work'
$log = Join-Path $logDir 'mw.log'
$err = Join-Path $logDir 'mw.err'

function Test-Listen([int]$Port) {
    return $null -ne (Get-NetTCPConnection -State Listen -LocalPort $Port -ErrorAction SilentlyContinue | Select-Object -First 1)
}

function Get-Owner([int]$Port) {
    return (Get-NetTCPConnection -State Listen -LocalPort $Port -ErrorAction SilentlyContinue |
        Select-Object -First 1).OwningProcess
}

function Get-Python {
    if ($env:CHORUS_PYTHON -and (Test-Path $env:CHORUS_PYTHON)) { return $env:CHORUS_PYTHON }
    $cmd = Get-Command python -ErrorAction SilentlyContinue
    if ($cmd) { return $cmd.Source }
    return $null
}

if ($Status) {
    if (Test-Listen $port) {
        Write-Host ("chorus-mw  运行中，http://127.0.0.1:{0}（pid {1}）" -f $port, (Get-Owner $port)) -ForegroundColor Green
    } else {
        Write-Host 'chorus-mw  未运行' -ForegroundColor DarkGray
    }
    $rvcRoot = '(未设置)'
    if ($env:CHORUS_RVC_ROOT) { $rvcRoot = $env:CHORUS_RVC_ROOT }
    Write-Host ("  CHORUS_RVC_ROOT = {0}" -f $rvcRoot)
    exit 0
}

if ($Stop) {
    if (-not (Test-Listen $port)) {
        Write-Host 'chorus-mw 没在运行' -ForegroundColor DarkGray
        exit 0
    }
    $owner = Get-Owner $port
    try {
        Stop-Process -Id $owner -Force -ErrorAction Stop
        Write-Host ("已停止 chorus-mw（pid {0}）" -f $owner) -ForegroundColor Green
    } catch {
        Write-Host ("停止失败：{0}" -f $_.Exception.Message) -ForegroundColor Yellow
    }
    exit 0
}

# ---------- 启动 ----------
Write-Host '=== chorus-mw 启动 ===' -ForegroundColor Cyan

if (Test-Listen $port) {
    Write-Host ("  端口 {0} 已被占用，先跑 -Status 看看是不是已经在跑" -f $port) -ForegroundColor Yellow
    exit 0
}

if (-not $env:CHORUS_RVC_ROOT) {
    Write-Host '  没有设置 CHORUS_RVC_ROOT，先把它设成 RVC 整合包根目录，例如：' -ForegroundColor Red
    Write-Host '    $env:CHORUS_RVC_ROOT = ''D:\your-path\RVC20260718Nvidia50x0''' -ForegroundColor Yellow
    exit 2
}
if (-not (Test-Path (Join-Path $env:CHORUS_RVC_ROOT 'runtime\python.exe'))) {
    Write-Host ("  {0} 底下找不到 runtime\python.exe，这个变量应该指向 RVC 整合包根目录" -f $env:CHORUS_RVC_ROOT) -ForegroundColor Red
    exit 2
}

$python = Get-Python
if (-not $python) {
    Write-Host '  找不到 python：设 CHORUS_PYTHON 指过去，或者把 python 加进 PATH' -ForegroundColor Red
    exit 2
}

New-Item -ItemType Directory -Force -Path $logDir | Out-Null

# 本机开着系统代理时，Gradio 4 探测 localhost 会走代理然后拒绝启动
$env:NO_PROXY = '127.0.0.1,localhost,::1'
$env:no_proxy = $env:NO_PROXY

Write-Host ("  python : {0}" -f $python)
Write-Host ("  RVC    : {0}" -f $env:CHORUS_RVC_ROOT)
Write-Host ("  端口   : {0}" -f $port)
Write-Host ("  日志   : {0}" -f $log)

Start-Process -FilePath $python `
    -ArgumentList '-X', 'utf8', 'app.py' `
    -WorkingDirectory $root `
    -WindowStyle Hidden `
    -RedirectStandardOutput $log `
    -RedirectStandardError $err

for ($i = 1; $i -le 60; $i++) {
    Start-Sleep -Seconds 1
    if (Test-Listen $port) {
        Write-Host ("  就绪  http://127.0.0.1:{0}（{1} 秒）" -f $port, $i) -ForegroundColor Green
        exit 0
    }
}

Write-Host '  60 秒还没起来，看这两个文件：' -ForegroundColor Red
Write-Host ("    {0}" -f $log)
Write-Host ("    {0}" -f $err)
Get-Content $err -Tail 20 -ErrorAction SilentlyContinue
exit 1
