$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
Set-Location $Root
$env:PYTHONPATH = "packages/domain/src;packages/application/src;adapters/operations/src;packages/application/tests"
python -m unittest discover -s packages/domain/tests -v
python -m unittest discover -s packages/application/tests -v
