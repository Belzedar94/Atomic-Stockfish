param(
    [string]$OutDir
)

$ErrorActionPreference = 'Stop'
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$repoRoot = [System.IO.Path]::GetFullPath((Join-Path $scriptDir '..\..'))
$sourceRoot = Join-Path $repoRoot 'src'
if ([string]::IsNullOrWhiteSpace($OutDir)) {
    $OutDir = Join-Path $repoRoot 'build\wasm-engine'
} elseif (-not [System.IO.Path]::IsPathRooted($OutDir)) {
    $OutDir = [System.IO.Path]::GetFullPath((Join-Path $sourceRoot $OutDir))
} else {
    $OutDir = [System.IO.Path]::GetFullPath($OutDir)
}

$resolvedRepo = $repoRoot.TrimEnd('\') + '\'
$resolvedOut = [System.IO.Path]::GetFullPath($OutDir)
if (-not $resolvedOut.StartsWith($resolvedRepo, [System.StringComparison]::OrdinalIgnoreCase)) {
    throw "Refusing to clean an output directory outside the repository: $resolvedOut"
}

if (Test-Path -LiteralPath $resolvedOut -PathType Container) {
    Remove-Item -LiteralPath $resolvedOut -Recurse -Force
}
