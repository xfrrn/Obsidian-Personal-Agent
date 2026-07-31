$ErrorActionPreference = "Stop"

$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$workRoot = Join-Path $repoRoot ".package-build"
$releaseRoot = Join-Path $repoRoot "dist"

function Reset-WorkspaceDirectory([string]$Path) {
    $fullPath = [IO.Path]::GetFullPath($Path)
    $workspacePrefix = $repoRoot.TrimEnd("\") + "\"
    if (-not $fullPath.StartsWith($workspacePrefix, [StringComparison]::OrdinalIgnoreCase)) {
        throw "Refusing to clean a directory outside the workspace: $fullPath"
    }
    if (Test-Path -LiteralPath $fullPath) {
        Remove-Item -LiteralPath $fullPath -Recurse -Force
    }
    New-Item -ItemType Directory -Path $fullPath | Out-Null
}

if ([Environment]::OSVersion.Platform -ne [PlatformID]::Win32NT -or -not [Environment]::Is64BitProcess) {
    throw "The Windows package must be built with 64-bit Python on Windows."
}

Push-Location $repoRoot
try {
    & npm run verify
    if ($LASTEXITCODE -ne 0) { throw "Project verification failed." }
    Reset-WorkspaceDirectory $workRoot
    New-Item -ItemType Directory -Force -Path $releaseRoot | Out-Null
    $packagePython = Join-Path $workRoot "venv/Scripts/python.exe"
    & python -m venv (Join-Path $workRoot "venv")
    if ($LASTEXITCODE -ne 0) { throw "Packaging venv creation failed." }
    & $packagePython -m pip install --disable-pip-version-check --quiet -e "apps/local-agent[package]"
    if ($LASTEXITCODE -ne 0) { throw "Packaging dependencies installation failed." }

    $agentDist = Join-Path $workRoot "agent-dist"
    $agentWork = Join-Path $workRoot "agent-work"
    $agentSource = Join-Path $repoRoot "apps/local-agent/src"
    $instructions = Join-Path $agentSource "agent/config/instructions"
    $systemSkills = Join-Path $agentSource "agent/skills/system"
    & $packagePython -m PyInstaller --noconfirm --clean --onedir --name codex-agent `
        --paths $agentSource `
        --add-data "$($instructions):agent/config/instructions" `
        --add-data "$($systemSkills):agent/skills/system" `
        --distpath $agentDist --workpath $agentWork --specpath $workRoot `
        "scripts/windows-agent-entry.py"
    if ($LASTEXITCODE -ne 0) { throw "Agent EXE build failed." }

    $manifest = Get-Content -Raw -Encoding UTF8 "apps/obsidian-plugin/manifest.json" | ConvertFrom-Json
    $pluginRoot = Join-Path $workRoot $manifest.id
    New-Item -ItemType Directory -Path $pluginRoot | Out-Null
    Copy-Item "apps/obsidian-plugin/main.js", "apps/obsidian-plugin/manifest.json", "apps/obsidian-plugin/styles.css" -Destination $pluginRoot
    Copy-Item (Join-Path $agentDist "codex-agent") (Join-Path $pluginRoot "agent") -Recurse

    $zipPath = Join-Path $releaseRoot "$($manifest.id)-windows-x64-$($manifest.version).zip"
    for ($attempt = 1; $attempt -le 10; $attempt++) {
        try {
            if (Test-Path -LiteralPath $zipPath) { Remove-Item -LiteralPath $zipPath -Force }
            Compress-Archive -LiteralPath $pluginRoot -DestinationPath $zipPath -CompressionLevel Optimal -ErrorAction Stop
            break
        } catch {
            if ($attempt -eq 10) { throw }
            Start-Sleep -Seconds 2
        }
    }
    Write-Host "Windows package created: $zipPath"
} finally {
    Pop-Location
}
