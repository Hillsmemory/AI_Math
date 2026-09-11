# verify-lean.ps1 — AI 管线调用 Lean 4 编译器的命令行接口（不依赖 VS Code）
#
# 用法:
#   .\verify-lean.ps1 -LeanFile ..\samples\ok.lean
#   .\verify-lean.ps1 -LeanFile C:\path\to\candidate.lean
#
# 输出: JSON
#   { "file": "...", "success": true/false,
#     "errors": [ {"line":3,"col":3,"severity":"error","message":"..."} ] }
#
# 说明: 在 Lean 项目根目录下执行 `lake env lean <file>`，
#       因此文件中可以 import Mathlib 等项目依赖。

param(
    [Parameter(Mandatory = $true, Position = 0)]
    [string]$LeanFile,

    [string]$ProjectDir = ""
)

$ErrorActionPreference = "Stop"

# 未显式指定项目目录时，默认取脚本所在目录的上一级（tools/ 的父目录）
if ([string]::IsNullOrEmpty($ProjectDir)) {
    $scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
    $ProjectDir = (Resolve-Path (Join-Path $scriptDir "..")).Path
}

if (-not (Test-Path $LeanFile)) {
    Write-Output (@{ file = $LeanFile; success = $false; errors = @(@{
        line = 0; col = 0; severity = "error"; message = "file not found: $LeanFile"
    }) } | ConvertTo-Json -Depth 5 -Compress)
    exit 2
}

$LeanFile = (Resolve-Path $LeanFile).Path

# 在 Lean 项目根目录下调用编译器（stderr 与 stdout 合并捕获）
Push-Location $ProjectDir
try {
    $raw = & lake env lean $LeanFile 2>&1 | Out-String
    $code = $LASTEXITCODE
} finally {
    Pop-Location
}

# 解析 Lean 诊断:  文件:行:列: (error|warning): 消息（消息可跨多行）
$errors = New-Object System.Collections.ArrayList
$current = $null
$diagPattern = '^(?<file>.+?):(?<line>\d+):(?<col>\d+):\s*(?<sev>error|warning):\s?(?<msg>.*)$'

foreach ($line in ($raw -split "`r?`n")) {
    if ($line -match $diagPattern) {
        if ($current) { [void]$errors.Add($current) }
        $current = @{
            line      = [int]$Matches['line']
            col       = [int]$Matches['col']
            severity  = $Matches['sev']
            message   = $Matches['msg'].Trim()
        }
    } elseif ($current) {
        # 续行（错误详情缩进部分）
        if ($line.Trim().Length -gt 0) {
            $current.message += "`n" + $line.TrimEnd()
        }
    }
}
if ($current) { [void]$errors.Add($current) }

$result = @{
    file    = $LeanFile
    success = ($code -eq 0)
    errors  = $errors
}

Write-Output ($result | ConvertTo-Json -Depth 5 -Compress)
exit $code
