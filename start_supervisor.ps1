<#
  chorus-mw 守护启动器
  --------------------
  用循环看住 app.py：进程退出（包括 torch/LLVM 内存崩）就自动重启，
  每崩一次往 supervisor.log 记一行，关掉本窗口即停止。

  独立运行，不并入 QQ 机器人的一键启动脚本。
#>
[CmdletBinding()]
param(
    [int]$Port = 3333
)

$ErrorActionPreference = 'Continue'
try { $Host.UI.RawUI.WindowTitle = 'RVC 中间层（守护）' } catch {}

$Here = Split-Path -Parent $MyInvocation.MyCommand.Path
$Python = 'C:\Users\21903\AppData\Local\Programs\Python\Python312\python.exe'
$RvcRoot = 'D:\PYMSS-RVC\RVC20260723Nvidia50x0\RVC20260718Nvidia50x0'
$LogDir = Join-Path $Here '_work'
New-Item -ItemType Directory -Force -Path $LogDir | Out-Null
$SupervisorLog = Join-Path $LogDir 'supervisor.log'

$env:CHORUS_RVC_ROOT = $RvcRoot
$env:NO_PROXY = '127.0.0.1,localhost,::1'
$env:no_proxy = $env:NO_PROXY
$env:PYTHONIOENCODING = 'utf-8'
$env:PYTORCH_CUDA_ALLOC_CONF = 'expandable_segments:True'

function Test-Port([int]$p) {
    return $null -ne (Get-NetTCPConnection -State Listen -LocalPort $p -ErrorAction SilentlyContinue | Select-Object -First 1)
}

function Say([string]$text, [string]$color = 'Gray') {
    $stamp = Get-Date -Format 'HH:mm:ss'
    Write-Host "[$stamp] $text" -ForegroundColor $color
    Add-Content -Path $SupervisorLog -Value "[$stamp] $text" -Encoding UTF8
}

$crashes = 0
while ($true) {
    if (Test-Port $Port) {
        Say "端口 $Port 已在监听，可能已经有中间层在跑；等 10 秒再看。" 'Yellow'
        Start-Sleep -Seconds 10
        continue
    }

    Say '启动中间层…' 'Cyan'
    $out = Join-Path $LogDir 'mw.log'
    $err = Join-Path $LogDir 'mw.err'
    $proc = Start-Process -FilePath $Python -ArgumentList @('-X', 'utf8', 'app.py') `
        -WorkingDirectory $Here -WindowStyle Hidden `
        -RedirectStandardOutput $out -RedirectStandardError $err -PassThru

    # 等它把端口监听起来（最多 60 秒）
    $ready = $false
    for ($i = 0; $i -lt 60; $i++) {
        Start-Sleep -Seconds 1
        if ($proc.HasExited) { break }
        if (Test-Port $Port) { $ready = $true; break }
    }

    if ($ready) {
        Say "中间层就绪 http://127.0.0.1:$Port（pid $($proc.Id)）" 'Green'
        $crashes = 0
        Wait-Process -Id $proc.Id -ErrorAction SilentlyContinue
        Say '中间层进程退出，准备重启。' 'Yellow'
    } else {
        $crashes++
        Say "中间层没起来（第 $crashes 次）。看 _work\mw.err 末尾。" 'Red'
    }

    # 崩溃间隔太短就多等一会儿，避免死循环刷屏
    $wait = [Math]::Min(60, 5 + $crashes * 5)
    Say "等 $wait 秒再重启…" 'DarkGray'
    Start-Sleep -Seconds $wait
}
