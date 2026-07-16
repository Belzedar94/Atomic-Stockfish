param(
    [string]$OutDir,
    [string]$Emsdk = 'C:\emsdk\emsdk',
    [switch]$Debug
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

$envScript = Join-Path $Emsdk 'emsdk_env.ps1'
if (-not (Test-Path -LiteralPath $envScript -PathType Leaf)) {
    throw "Emscripten environment script not found: $envScript"
}

$env:EMSDK_QUIET = '1'
if ($null -eq $env:SOURCE_DATE_EPOCH) {
    $env:SOURCE_DATE_EPOCH = '0'
} elseif ($env:SOURCE_DATE_EPOCH -notmatch '\A(?:0|[1-9][0-9]*)\z') {
    throw 'SOURCE_DATE_EPOCH must be zero or a canonical positive decimal integer'
}
. $envScript

$empp = Get-Command em++ -ErrorAction Stop
[System.IO.Directory]::CreateDirectory($OutDir) | Out-Null

$relativeSources = @(
    'atomic_init.cpp',
    'attacks.cpp',
    'benchmark.cpp',
    'bitboard.cpp',
    'evaluate.cpp',
    'main.cpp',
    'memory.cpp',
    'misc.cpp',
    'movegen.cpp',
    'movepick.cpp',
    'position.cpp',
    'score.cpp',
    'search.cpp',
    'thread.cpp',
    'timeman.cpp',
    'tt.cpp',
    'tune.cpp',
    'uci.cpp',
    'uci_move.cpp',
    'ucioption.cpp',
    'engine.cpp',
    'syzygy/tbprobe.cpp',
    'nnue/nnue_accumulator.cpp',
    'nnue/nnue_dispatcher.cpp',
    'nnue/nnue_misc.cpp',
    'nnue/network.cpp',
    'nnue/atomic_v2/atomic_v2_accumulator.cpp',
    'nnue/atomic_v2/atomic_v2_network.cpp',
    'nnue/features/half_ka_v2_atomic.cpp'
)
$sources = $relativeSources | ForEach-Object {
    $source = Join-Path $sourceRoot $_
    if (-not (Test-Path -LiteralPath $source -PathType Leaf)) {
        throw "Missing engine source: $source"
    }
    [System.IO.Path]::GetFullPath($source)
}

$uciOnlyHeader = [System.IO.Path]::GetFullPath((Join-Path $scriptDir 'uci_only.h'))
$outputJs = Join-Path $OutDir 'atomic-stockfish-nnue.js'
$prefixMap = $sourceRoot.Replace('\', '/')

$flags = @(
    '-std=c++17',
    '-fexceptions',
    '-pthread',
    '-msimd128',
    '-msse2',
    '-msse3',
    '-mssse3',
    '-msse4.1',
    '-DNO_TABLEBASES',
    '-DNO_PREFETCH',
    '-DNNUE_EMBEDDING_OFF',
    '-DUSE_POPCNT',
    '-DUSE_PTHREADS',
    '-DUSE_SSE2',
    '-DUSE_SSSE3',
    '-DUSE_SSE41',
    '-DUSE_SLOPPY_ATOMICS',
    '-include', $uciOnlyHeader,
    "-ffile-prefix-map=$prefixMap=.",
    "-fdebug-prefix-map=$prefixMap=.",
    '-sENVIRONMENT=node',
    '-sNODERAWFS=1',
    '-sFORCE_FILESYSTEM=1',
    '-sINITIAL_MEMORY=536870912',
    '-sSTACK_SIZE=8388608',
    '-sPTHREAD_POOL_SIZE=4',
    '-sEXIT_RUNTIME=1',
    '-sNO_EXIT_RUNTIME=0',
    '-sDISABLE_EXCEPTION_CATCHING=0',
    '-sWASM_BIGINT=1',
    '-sDETERMINISTIC=1'
)

if ($Debug) {
    $flags += @(
        '-O1',
        '-g3',
        '-sASSERTIONS=2',
        '-sSAFE_HEAP=1',
        '-sSTACK_OVERFLOW_CHECK=2'
    )
} else {
    $flags += @(
        '-O3',
        '-flto',
        '-DNDEBUG',
        '-sASSERTIONS=0'
    )
}

Write-Host "Building Atomic-Stockfish Node UCI/NNUE WebAssembly in $OutDir"
& $empp.Source @flags @sources '-o' $outputJs
if ($LASTEXITCODE -ne 0) {
    throw "em++ failed with exit code $LASTEXITCODE"
}

$required = @(
    $outputJs,
    (Join-Path $OutDir 'atomic-stockfish-nnue.wasm')
)
foreach ($artifact in $required) {
    if (-not (Test-Path -LiteralPath $artifact -PathType Leaf)) {
        throw "Expected WebAssembly artifact was not produced: $artifact"
    }
}

$wrapperSource = Join-Path $scriptDir 'node-uci-wrapper.mjs'
$wrapperOutput = Join-Path $OutDir 'atomic-stockfish-nnue-node.mjs'
[System.IO.File]::Copy($wrapperSource, $wrapperOutput, $true)

$artifacts = Get-ChildItem -LiteralPath $OutDir -File |
    Where-Object { $_.Name -like 'atomic-stockfish-nnue*' } |
    Sort-Object Name
$manifestArtifacts = @()
foreach ($artifact in $artifacts) {
    $manifestArtifacts += [ordered]@{
        name = $artifact.Name
        bytes = $artifact.Length
        sha256 = (Get-FileHash -LiteralPath $artifact.FullName -Algorithm SHA256).Hash.ToLowerInvariant()
    }
}

$compilerLine = (& $empp.Source --version | Select-Object -First 1)
$manifest = [ordered]@{
    schemaVersion = 2
    target = 'node-uci-nnue'
    sourceDateEpoch = [long]::Parse($env:SOURCE_DATE_EPOCH)
    debug = [bool]$Debug
    compiler = $compilerLine
    initialMemoryBytes = 536870912
    memoryGrowth = $false
    pthreadPoolSize = 4
    supportedEntrypoint = 'atomic-stockfish-nnue-node.mjs'
    generatedRuntimeGlue = 'atomic-stockfish-nnue.js'
    directRuntimeGlueSupported = $false
    supportedNetworkBackends = @('Legacy Atomic V1', 'AtomicNNUEV2')
    networkFileVersions = @('0x7AF32F20', '0xA70C0002')
    stdinPump = [ordered]@{
        command = 'isready'
        response = 'readyok'
        intervalMilliseconds = 25
        maxOutstandingPrivatePumps = 1
        preservesUserReadyok = $true
    }
    externalNetwork = $true
    artifacts = $manifestArtifacts
}
$manifestPath = Join-Path $OutDir 'manifest.json'
[System.IO.File]::WriteAllText(
    $manifestPath,
    (($manifest | ConvertTo-Json -Depth 5) + [Environment]::NewLine),
    [System.Text.UTF8Encoding]::new($false)
)

Write-Host "WASM engine build complete:"
$manifestArtifacts | ForEach-Object {
    Write-Host ("  {0}  {1} bytes  sha256={2}" -f $_.name, $_.bytes, $_.sha256)
}
