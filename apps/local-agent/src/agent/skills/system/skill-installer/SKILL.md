---
name: skill-installer
description: List and install Agent skills from the curated openai/skills collections or another GitHub repository. Use when the user asks what skills are available, asks to install a skill, or provides a GitHub repository path containing a skill.
---

# Skill Installer

Run the bundled PowerShell scripts through `exec_command`. Resolve script paths from this skill's directory. Every script accesses GitHub, and installation writes outside the workspace, so request host execution with a concise approval reason. If `exec_command` is unavailable, tell the user to enable Shell in the plugin settings.

## List skills

When no skill was specified, list the curated collection:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File "<skill-dir>\scripts\list-skills.ps1"
```

Pass `-Path skills/.experimental` for experimental skills, or `-Format Json` when structured output is useful. Present installed annotations and ask which skills to install.

## Install a skill

Install one curated skill:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File "<skill-dir>\scripts\install-skill-from-github.ps1" -Repo openai/skills -Path skills/.curated/<skill-name>
```

For another repository, pass its `owner/repo`, path, and optional `-Ref`. Existing `GITHUB_TOKEN` or `GH_TOKEN` credentials are used for private repositories. Never overwrite an existing destination.

After success, tell the user the skill will be available on their next turn. Skills under `skills/.system` are built in and do not need installation.
