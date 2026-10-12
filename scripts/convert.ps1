# convert.ps1 - 电子书 -> 按章切分 JSON 的入口（调用同目录的 convert.py）
#
# 用法:
#   .\convert.ps1 <文件> [更多文件...] [-OutDir <目录>] [-Title <书名>] [-Author <作者>]
#   .\convert.ps1 -SelfTest
#
# 例:
#   .\convert.ps1 '.\book.epub' -OutDir .
#   .\convert.ps1 '.\a.pdf' '.\b.txt' -OutDir out
#
# 说明:
#   * 本文件是纯 ASCII。PowerShell 5.1 读无 BOM 的 .ps1 会按 ANSI 代码页解码，
#     脚本里写非 ASCII 字符会乱码或报错，所以中文一律留给 convert.py 输出
#     （convert.py 会强制 stdout/stderr 走 UTF-8）。
#   * 参数不直接转发给 convert.py：PowerShell 会把 -o 之类当成自己的公共参数
#     并报「参数名称存在歧义」。这里显式定义参数，再拼成 convert.py 的命令行。
#   * 参数名用 -InputPath 而不是 -Path：-Path 与 PowerShell 内置的 -FilePath
#     别名冲突，多文件时绑定会失败。位置参数写法（直接跟文件）同样可用。
#
# 依赖: Python 3（只用标准库）。PDF/DOCX 直接解析；旧版 .doc 与 HUFF/CDIC 压缩的
#       azw3 会调用已安装的 Calibre ebook-convert。

[CmdletBinding()]
param(
    # 不能标 Mandatory：-SelfTest 模式不需要文件参数，标了会导致自检直接被参数绑定拒绝
    [Parameter(Position = 0, ValueFromRemainingArguments = $true)]
    [string[]]$InputPath = @(),

    [string]$OutDir = '.',

    [string]$Title = '',

    [string]$Author = '',

    [switch]$SelfTest
)

$ErrorActionPreference = 'Stop'
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$script = Join-Path $here 'convert.py'

if (-not (Test-Path -LiteralPath $script)) {
    Write-Error "convert.py not found next to this script: $script"
    exit 1
}

$python = Get-Command python -ErrorAction SilentlyContinue
if (-not $python) { $python = Get-Command py -ErrorAction SilentlyContinue }
if (-not $python) {
    Write-Error 'Python 3 not found on PATH. Install Python 3 first.'
    exit 1
}

# 中文输出走 UTF-8，避免控制台按代码页显示成乱码
try { [Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false) } catch { }

if ($SelfTest) {
    & $python.Source $script --selftest
    exit $LASTEXITCODE
}

if ($InputPath.Count -eq 0) {
    Write-Host '用法: convert.ps1 <文件> [更多文件...] [-OutDir <目录>] [-Title <书名>] [-Author <作者>]'
    Write-Host '      convert.ps1 -SelfTest'
    exit 2
}

$argv = @()
foreach ($p in $InputPath) { $argv += $p }
$argv += @('-o', $OutDir)
if ($Title) { $argv += @('--title', $Title) }
if ($Author) { $argv += @('--author', $Author) }

& $python.Source $script @argv
exit $LASTEXITCODE
