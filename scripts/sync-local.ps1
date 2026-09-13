[CmdletBinding()]
param(
    [string]$RepoPath = 'D:\IdeaProject\deephelp',
    [string]$SourceDirectory = 'D:\BaiduNetdiskDownload'
)
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

if (-not (Test-Path -LiteralPath $RepoPath -PathType Container)) {
    throw "Repository directory not found: $RepoPath"
}
if (-not (Get-Command git -ErrorAction SilentlyContinue)) { throw 'Git is required.' }
if (-not (Get-Command uv -ErrorAction SilentlyContinue)) { throw 'Use the existing uv installation; it was not found.' }

Push-Location -LiteralPath $RepoPath
try {
    $branch = & git branch --show-current
    if ($LASTEXITCODE -ne 0 -or $branch.Trim() -ne 'main') { throw 'Expected main branch; no files changed.' }
    $remote = & git remote get-url origin
    if ($LASTEXITCODE -ne 0 -or $remote.Trim() -notmatch '^(https://github\.com/|git@github\.com:)yuweilan7/deephelp(\.git)?$') {
        throw 'Unexpected origin; no files changed.'
    }
    $changes = & git status --porcelain
    if ($LASTEXITCODE -ne 0) { throw 'Cannot inspect working tree.' }
    if ($changes) { throw 'Working tree has local changes. Review and commit/stash them yourself; this script does not overwrite or stash.' }
    & git pull --ff-only origin main
    if ($LASTEXITCODE -ne 0) { throw 'Fast-forward pull failed; do not force or reset.' }
    if (-not (Test-Path -LiteralPath 'scripts/project_context.py' -PathType Leaf)) {
        throw 'The delivery commit is not present; no PDF copied.'
    }
    & uv run --locked python scripts/project_context.py verify
    if ($LASTEXITCODE -ne 0) { throw 'Documentation checks failed; no PDF copied.' }
    & uv run --locked python scripts/project_context.py import-source --source-dir $SourceDirectory
    if ($LASTEXITCODE -ne 0) { throw 'Source import failed safely. Read the hash/path error; the original was not deleted.' }
    Write-Host 'Remote files synchronized. Original PDF copied and verified under .local/references.'
    Write-Host 'No original deleted; no commit/push; no cloud services or paid models invoked.'
} finally {
    Pop-Location
}
