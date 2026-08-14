param(
    [Parameter(Mandatory = $true)]
    [string]$LocalDir,

    [Parameter(Mandatory = $true)]
    [string]$Server,

    [Parameter(Mandatory = $true)]
    [string]$User,

    [Parameter(Mandatory = $true)]
    [string]$RemoteDir,

    [ValidateRange(1, 65535)]
    [int]$Port = 22
)

$ErrorActionPreference = "Stop"
$source = (Resolve-Path -LiteralPath $LocalDir -ErrorAction Stop).Path
if (-not (Test-Path -LiteralPath $source -PathType Container)) {
    throw "本地目录不存在：$source"
}

$files = @(Get-ChildItem -LiteralPath $source -File -Recurse -Force)
if ($files.Count -eq 0) {
    throw "本地目录中没有文件：$source"
}
$totalBytes = ($files | Measure-Object -Property Length -Sum).Sum
$totalGB = [math]::Round($totalBytes / 1GB, 3)
$folderName = Split-Path -Leaf $source
$target = "${User}@${Server}:$RemoteDir/"

Write-Host "准备上传目录：$source"
Write-Host "文件数量：$($files.Count)"
Write-Host "总大小：$totalGB GB"
Write-Host "远程位置：$RemoteDir/$folderName"

& ssh -p $Port "${User}@${Server}" "mkdir -p -- '$RemoteDir'"
if ($LASTEXITCODE -ne 0) {
    throw "创建远程目录失败，ssh退出码：$LASTEXITCODE"
}

& scp -P $Port -r -- $source $target
if ($LASTEXITCODE -ne 0) {
    throw "上传失败，scp退出码：$LASTEXITCODE"
}

Write-Host "上传完成：$RemoteDir/$folderName"
