---
name: script-powershell
description: Écrire ou corriger un script PowerShell propre et sûr pour l'administration Windows (paramètres, gestion d'erreurs, -WhatIf).
registre: sobre
---
# Script PowerShell

## Objectif
Un script .ps1 prêt à l'emploi, sûr à lancer, qui accepte -WhatIf.

## Ne pas l'utiliser si
- Une simple commande ponctuelle : réponds directement ou avec dev.
- Un script dans un autre langage : dev.

## Méthode
1. Écris le script selon les bonnes pratiques ci-dessous.
2. Vérifie sa syntaxe sans l'exécuter : $errs = $null; $null = [System.Management.Automation.Language.Parser]::ParseFile((Resolve-Path .\script.ps1).Path, [ref]$null, [ref]$errs); $errs
3. Propose de le lancer d'abord avec -WhatIf, puis seulement après accord sans -WhatIf.

## Bonnes pratiques
- En tête : #Requires -Version 5.1 (ou 7 si nécessaire), un bloc d'aide <# .SYNOPSIS .EXAMPLE #>, puis [CmdletBinding(SupportsShouldProcess)] et un bloc param() typé et validé ([ValidateNotNullOrEmpty()], [ValidateSet()]…).
- Set-StrictMode -Version Latest et $ErrorActionPreference = 'Stop'.
- Toute action qui modifie le système passe par if ($PSCmdlet.ShouldProcess($cible, 'action')) { … } : -WhatIf et -Confirm marchent.
- try { … } catch { Write-Error "Contexte clair : $($_.Exception.Message)"; exit 1 } autour des étapes qui peuvent échouer.
- Noms complets des cmdlets, jamais d'alias (Get-ChildItem, pas ls ni gci).
- Journalisation avec Write-Verbose ; des objets en sortie plutôt que du texte formaté (pas de Format-Table dans un script).

## Format de la réponse
Le script complet dans un bloc powershell, puis la commande pour le tester avec -WhatIf.

## Règles
### À faire toujours
- Un script qui accepte -WhatIf dès qu'il modifie le système.
### À ne jamais faire
- Un mot de passe en clair : Get-Credential, Read-Host -AsSecureString ou un coffre de secrets.
- Lancer le script pour de vrai sans l'accord de l'utilisateur.

## Vérifier avant de répondre
- [ ] La syntaxe est vérifiée.
- [ ] Les actions qui modifient le système passent par ShouldProcess.
