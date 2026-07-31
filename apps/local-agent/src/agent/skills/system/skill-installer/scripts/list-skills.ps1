param(
    [string]$Repo = "openai/skills",
    [string]$Path = "skills/.curated",
    [string]$Ref = "main",
    [ValidateSet("Text", "Json")]
    [string]$Format = "Text",
    [string]$Destination = $env:AGENT_SKILLS_DIR
)

$ErrorActionPreference = "Stop"

try {
    if ($Repo -notmatch '^[A-Za-z0-9-]+/[A-Za-z0-9_.-]+$') {
        throw "Repo must use owner/repo format."
    }
    $segments = @($Path -split '/' | Where-Object { $_ })
    if (-not $segments.Count -or $Path.StartsWith('/') -or $Path.Contains('\') -or $segments -contains "." -or $segments -contains "..") {
        throw "Path must be relative to the repository."
    }
    if (-not $Ref.Trim()) {
        throw "Ref cannot be empty."
    }

    $encodedPath = ($segments | ForEach-Object { [Uri]::EscapeDataString($_) }) -join "/"
    $uri = "https://api.github.com/repos/$Repo/contents/$encodedPath`?ref=$([Uri]::EscapeDataString($Ref))"
    $headers = @{ Accept = "application/vnd.github+json"; "User-Agent" = "obsidian-personal-agent-skill-list" }
    $token = if ($env:GITHUB_TOKEN) { $env:GITHUB_TOKEN } else { $env:GH_TOKEN }
    if ($token) { $headers.Authorization = "Bearer $token" }

    $available = @(
        Invoke-RestMethod -Uri $uri -Headers $headers |
            Where-Object { $_.type -eq "dir" } |
            ForEach-Object { $_.name } |
            Sort-Object
    )
    $installed = @{}
    if ($Destination -and (Test-Path -LiteralPath $Destination -PathType Container)) {
        Get-ChildItem -LiteralPath $Destination -Directory | ForEach-Object {
            $installed[$_.Name] = $true
        }
    }

    if ($Format -eq "Json") {
        $items = @($available | ForEach-Object {
            [ordered]@{ name = $_; installed = $installed.ContainsKey($_) }
        })
        ConvertTo-Json -InputObject $items -Compress
    } else {
        for ($index = 0; $index -lt $available.Count; $index++) {
            $suffix = if ($installed.ContainsKey($available[$index])) { " (already installed)" } else { "" }
            "{0}. {1}{2}" -f ($index + 1), $available[$index], $suffix
        }
    }
} catch {
    Write-Error $_.Exception.Message
    exit 1
}
