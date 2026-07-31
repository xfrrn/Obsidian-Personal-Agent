param(
    [Parameter(Mandatory = $true)]
    [string]$Repo,
    [Parameter(Mandatory = $true)]
    [string]$Path,
    [string]$Ref = "main",
    [ValidateSet("Auto", "Download", "Git")]
    [string]$Method = "Auto",
    [string]$Destination = $env:AGENT_SKILLS_DIR
)

$ErrorActionPreference = "Stop"

function Invoke-Git([string[]]$Arguments) {
    $output = & git @Arguments 2>&1
    if ($LASTEXITCODE -ne 0) {
        throw (($output | ForEach-Object { $_.ToString() }) -join [Environment]::NewLine)
    }
}

function Get-DownloadedRepository([string]$TemporaryRoot) {
    $archive = Join-Path $TemporaryRoot "repo.zip"
    $extractRoot = Join-Path $TemporaryRoot "download"
    $headers = @{ "User-Agent" = "obsidian-personal-agent-skill-install" }
    $token = if ($env:GITHUB_TOKEN) { $env:GITHUB_TOKEN } else { $env:GH_TOKEN }
    if ($token) { $headers.Authorization = "Bearer $token" }
    $uri = "https://codeload.github.com/$Repo/zip/$([Uri]::EscapeDataString($Ref))"
    Invoke-WebRequest -UseBasicParsing -Uri $uri -Headers $headers -OutFile $archive
    Expand-Archive -LiteralPath $archive -DestinationPath $extractRoot
    $roots = @(Get-ChildItem -LiteralPath $extractRoot -Directory)
    if ($roots.Count -ne 1) { throw "Downloaded archive has an unexpected layout." }
    return $roots[0].FullName
}

function Get-GitRepository([string]$TemporaryRoot) {
    $repositoryRoot = Join-Path $TemporaryRoot "repo"
    $urls = @("https://github.com/$Repo.git", "git@github.com:$Repo.git")
    $lastError = "Git clone failed."
    foreach ($url in $urls) {
        try {
            if (Test-Path -LiteralPath $repositoryRoot) {
                Remove-Item -LiteralPath $repositoryRoot -Recurse -Force
            }
            try {
                Invoke-Git -Arguments @("clone", "--filter=blob:none", "--depth", "1", "--sparse", "--single-branch", "--branch", $Ref, $url, $repositoryRoot)
            } catch {
                if (Test-Path -LiteralPath $repositoryRoot) {
                    Remove-Item -LiteralPath $repositoryRoot -Recurse -Force
                }
                Invoke-Git -Arguments @("clone", "--filter=blob:none", "--depth", "1", "--sparse", "--single-branch", $url, $repositoryRoot)
                Invoke-Git -Arguments @("-C", $repositoryRoot, "checkout", $Ref)
            }
            Invoke-Git -Arguments @("-C", $repositoryRoot, "sparse-checkout", "set", $Path)
            return $repositoryRoot
        } catch {
            $lastError = $_.Exception.Message
        }
    }
    throw $lastError
}

function Assert-ValidSkill([string]$SkillRoot, [string]$ExpectedName) {
    $manifest = Join-Path $SkillRoot "SKILL.md"
    if (-not (Test-Path -LiteralPath $SkillRoot -PathType Container)) {
        throw "Skill path not found: $Path"
    }
    if (-not (Test-Path -LiteralPath $manifest -PathType Leaf)) {
        throw "SKILL.md not found in selected skill directory."
    }
    if ((Get-Item -LiteralPath $manifest).Length -gt 65536) {
        throw "SKILL.md exceeds the 64 KiB limit."
    }

    $lines = @(Get-Content -LiteralPath $manifest -Encoding UTF8)
    if (-not $lines.Count -or $lines[0].Trim() -ne "---") {
        throw "SKILL.md must start with YAML front matter."
    }
    $frontMatterEnd = -1
    for ($index = 1; $index -lt $lines.Count; $index++) {
        if ($lines[$index].Trim() -eq "---") { $frontMatterEnd = $index; break }
    }
    if ($frontMatterEnd -lt 0) { throw "SKILL.md front matter is not closed." }

    $metadata = @{}
    for ($index = 1; $index -lt $frontMatterEnd; $index++) {
        $parts = $lines[$index].Split(':', 2)
        if ($parts.Count -ne 2 -or -not $parts[0].Trim()) {
            throw "SKILL.md front matter is invalid."
        }
        $metadata[$parts[0].Trim()] = $parts[1].Trim().Trim('"').Trim("'")
    }
    if ($metadata.name -ne $ExpectedName) {
        throw "SKILL.md name must match its directory: $ExpectedName"
    }
    if (-not $metadata.description) { throw "SKILL.md is missing description." }
    if ($frontMatterEnd -eq $lines.Count - 1 -or -not (($lines[($frontMatterEnd + 1)..($lines.Count - 1)] -join "").Trim())) {
        throw "SKILL.md is missing instructions."
    }
}

$temporaryRoot = $null
$staging = $null
try {
    if ($Repo -notmatch '^[A-Za-z0-9-]+/[A-Za-z0-9_.-]+$') {
        throw "Repo must use owner/repo format."
    }
    $segments = @($Path -split '/' | Where-Object { $_ })
    if (-not $segments.Count -or $Path.StartsWith('/') -or $Path.Contains('\') -or $segments -contains "." -or $segments -contains "..") {
        throw "Path must be relative to the repository."
    }
    if (-not $Ref.Trim()) { throw "Ref cannot be empty." }
    $skillName = $segments[-1]
    if ($skillName -notmatch '^[a-z0-9][a-z0-9-]*$') {
        throw "Skill name must contain lowercase letters, digits, or hyphens."
    }
    if ($skillName -eq "skill-installer") { throw "skill-installer is built in." }
    if (-not $Destination) {
        $Destination = Join-Path ([Environment]::GetFolderPath("UserProfile")) ".codex-agent\skills"
    }
    $destinationRoot = [IO.Path]::GetFullPath($Destination)
    $finalDestination = Join-Path $destinationRoot $skillName
    if (Test-Path -LiteralPath $finalDestination) {
        throw "Destination already exists: $finalDestination"
    }

    $temporaryRoot = Join-Path ([IO.Path]::GetTempPath()) ("agent-skill-install-" + [Guid]::NewGuid().ToString("N"))
    New-Item -ItemType Directory -Path $temporaryRoot | Out-Null
    $repositoryRoot = $null
    if ($Method -in @("Auto", "Download")) {
        try {
            $repositoryRoot = Get-DownloadedRepository $temporaryRoot
        } catch {
            if ($Method -eq "Download") { throw }
        }
    }
    if (-not $repositoryRoot) {
        $repositoryRoot = Get-GitRepository $temporaryRoot
    }

    $source = [IO.Path]::GetFullPath((Join-Path $repositoryRoot ($segments -join [IO.Path]::DirectorySeparatorChar)))
    $repositoryPrefix = [IO.Path]::GetFullPath($repositoryRoot).TrimEnd('\', '/') + [IO.Path]::DirectorySeparatorChar
    if (-not $source.StartsWith($repositoryPrefix, [StringComparison]::OrdinalIgnoreCase)) {
        throw "Skill path escapes the repository."
    }
    Assert-ValidSkill $source $skillName

    New-Item -ItemType Directory -Path $destinationRoot -Force | Out-Null
    $staging = Join-Path $destinationRoot (".$skillName.install-" + [Guid]::NewGuid().ToString("N"))
    Copy-Item -LiteralPath $source -Destination $staging -Recurse
    [IO.Directory]::Move($staging, $finalDestination)
    $staging = $null
    "Installed $skillName to $finalDestination"
} catch {
    Write-Error $_.Exception.Message
    exit 1
} finally {
    if ($staging -and (Test-Path -LiteralPath $staging)) {
        Remove-Item -LiteralPath $staging -Recurse -Force
    }
    if ($temporaryRoot -and (Test-Path -LiteralPath $temporaryRoot)) {
        Remove-Item -LiteralPath $temporaryRoot -Recurse -Force
    }
}
