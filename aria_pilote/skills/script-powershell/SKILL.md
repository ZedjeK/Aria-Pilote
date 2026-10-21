---
name: script-powershell
description: Écrire ou corriger un script PowerShell propre et sûr pour l'administration Windows (paramètres, gestion d'erreurs, -WhatIf).
---

# Script PowerShell

Conventions à respecter dans tout script `.ps1` :

- En tête : `#Requires -Version 5.1` (ou 7 si nécessaire), un bloc d'aide `<# .SYNOPSIS .EXAMPLE #>`, puis
  `[CmdletBinding(SupportsShouldProcess)]` et un bloc `param()` typé et validé (`[ValidateNotNullOrEmpty()]`, `[ValidateSet()]`…).
- `Set-StrictMode -Version Latest` et `$ErrorActionPreference = 'Stop'`.
- Toute action qui modifie le système passe par `if ($PSCmdlet.ShouldProcess($cible, 'action')) { … }` :
  le script accepte ainsi `-WhatIf` et `-Confirm`.
- `try { … } catch { Write-Error "Contexte clair : $($_.Exception.Message)"; exit 1 }` autour des étapes qui peuvent échouer.
- Noms complets des cmdlets, jamais d'alias (`Get-ChildItem`, pas `ls` ni `gci`).
- Journalisation avec `Write-Verbose`, sortie d'objets plutôt que du texte formaté (pas de `Format-Table` dans un script).
- Jamais de mot de passe en clair : `Get-Credential`, `Read-Host -AsSecureString` ou un coffre de secrets.

Méthode :
1. Écris le script, puis vérifie sa syntaxe sans l'exécuter :
   `$errs = $null; $null = [System.Management.Automation.Language.Parser]::ParseFile((Resolve-Path .\script.ps1).Path, [ref]$null, [ref]$errs); $errs`
2. Propose de le lancer d'abord avec `-WhatIf`, puis seulement après accord sans `-WhatIf`.
