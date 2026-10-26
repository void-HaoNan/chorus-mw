# 下载 Rubber Band Library 4.0.0 的 Windows 可执行文件到 bin/
# 用法：powershell -ExecutionPolicy Bypass -File scripts\fetch_rubberband.ps1
#
# 为什么单独下：rubberband 是 GPL，二进制不随本仓库分发；
# 而且只有 exe 不够，sndfile.dll 必须一起放，否则 exe 起来就报缺 dll。
$ErrorActionPreference = 'Stop'

$url    = 'https://breakfastquay.com/files/releases/rubberband-4.0.0-gpl-executable-windows.zip'
$root   = Split-Path -Parent $PSScriptRoot
$binDir = Join-Path $root 'bin'
$tmp    = Join-Path $root '_work\rubberband.zip'
$ext    = Join-Path $root '_work\rubberband'

New-Item -ItemType Directory -Force -Path $binDir | Out-Null
New-Item -ItemType Directory -Force -Path (Join-Path $root '_work') | Out-Null

Write-Host "下载 $url"
$ProgressPreference = 'SilentlyContinue'
Invoke-WebRequest -Uri $url -OutFile $tmp -UseBasicParsing
Write-Host ("  下载完成 {0:N1} MB" -f ((Get-Item $tmp).Length / 1MB))

Remove-Item $ext -Recurse -Force -ErrorAction SilentlyContinue
Expand-Archive -Path $tmp -DestinationPath $ext -Force

$inner = Get-ChildItem $ext -Directory | Select-Object -First 1
foreach ($name in 'rubberband.exe', 'sndfile.dll') {
    $src = Join-Path $inner.FullName $name
    if (-not (Test-Path $src)) { throw "压缩包里没有 $name" }
    Copy-Item $src (Join-Path $binDir $name) -Force
    Write-Host "  -> bin\$name"
}
foreach ($name in 'CHANGELOG.txt', 'COPYING.txt') {
    $src = Join-Path $inner.FullName $name
    if (Test-Path $src) { Copy-Item $src (Join-Path $binDir $name) -Force }
}

& (Join-Path $binDir 'rubberband.exe') --version
Write-Host '完成。'
